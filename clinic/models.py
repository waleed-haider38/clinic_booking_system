from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db.models import Q
from django.db.models import Func
from django.utils import timezone
from datetime import timedelta


# --- Double-booking prevention: Part 1 (DB-level safety net) ---
#
# Postgres's ExclusionConstraint needs a single "range" value to check
# for overlaps (e.g. a DateTimeRangeField). Our Appointment model stores
# time as two separate columns (start_time, end_time) instead of a native
# range field, so we can't pass those columns directly into the constraint.
#
# This class is a small wrapper around Postgres's built-in TSTZRANGE()
# function. It tells Django: "whenever this class is used inside a query,
# generate the SQL function TSTZRANGE(start_time, end_time)" — which
# combines our two columns into one range value on the fly, only for the
# purpose of the constraint check. No new column is created in the table.
#
# We inherit from Func (Django's base class for SQL functions) instead of
# writing our own __init__, because Func already knows how to accept
# positional field names and turn them into SQL function arguments.
class TsTzRange(Func):
    function = 'TSTZRANGE'  # the actual Postgres function name to call
    output_field = DateTimeRangeField()  # tells Django the result is a "range" type,
                                        # so it knows how to compare it with OVERLAPS

MINIMUM_NOTICE_HOURS = timedelta(hours=1)
# Create your models here.

#Abstract User has already built in column. like name , email etc so we are saying that add a new attribute of name role init with choices.
class User(AbstractUser):
    ROLE_CHOICES = (
        ('patient' , 'Patient'),
        ('doctor', 'Doctor'),
        ('admin', 'Admin')
    )

    role = models.CharField(max_length=10 , choices=ROLE_CHOICES , default='patient')
    #Dunder Method. which whenever we open this table it shows us the username and its role.
    def __str__(self):
        return f"{self.username} ({self.role})"

class DoctorProfile(models.Model):
    user = models.OneToOneField(User , on_delete=models.CASCADE, related_name="doctor_profile")
    speciality = models.CharField(max_length=100)
    license_number = models.CharField(max_length=50 , unique=True)
    bio = models.TextField(blank=True)

    def __str__(self):
        return f"Dr {self.user.username} - {self.speciality}"

class PatientProfile(models.Model):
    user = models.OneToOneField(User , on_delete=models.CASCADE, related_name="patient_profile")
    date_of_birth = models.DateField(null=True , blank=True)
    medical_history_notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.user.username} (Patientt)"


class Availability(models.Model):
    DAY_CHOICES = (
        ('monday', 'Monday'),
        ('tuesday', 'Tuesday'),
        ('wednesday', 'Wednesday'),
        ('thursday', 'Thursday'),
        ('friday', 'Friday'),
        ('saturday', 'Saturday'),
        ('sunday', 'Sunday'),
    )
    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='availabilities')
    day_of_week = models.CharField(max_length=10, choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def __str__(self):
        return f"{self.doctor.user.username} - {self.day_of_week} ({self.start_time}-{self.end_time})"


class Service(models.Model):
    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='services')
    name = models.CharField(max_length=100)
    duration_minutes = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.name} ({self.duration_minutes} min) - {self.doctor.user.username}"



class Appointment(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    )
    patient = models.ForeignKey(PatientProfile, on_delete=models.CASCADE, related_name='appointments')
    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, related_name='appointments')
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='appointments')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    reminder_sent = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.patient.user.username} with Dr. {self.doctor.user.username} on {self.start_time}"

    def clean(self):
        # Application-level overlap check, run whenever full_clean()/serializer
        # validation calls it. This is NOT race-condition safe on its own —
        # two simultaneous requests can both pass this check before either
        # one saves. It's kept as a first line of defense (e.g. for Django
        # admin, where the DB constraint below still applies but this gives
        # a friendlier error message earlier).
        
        overlapping = Appointment.objects.filter(
            doctor=self.doctor,
            status__in=['pending', 'confirmed'],
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        ).exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("This doctor already has an appointment during this time slot.")

        # Minimum notice period: a new appointment must start at least
        # MINIMUM_NOTICE_HOURS from now — prevents last-minute bookings
        # that a doctor/clinic can't realistically prepare for.
       
        current_time = timezone.now()
        if self.start_time - current_time < MINIMUM_NOTICE_HOURS:
            raise ValidationError("Appointments must be booked at least 1 hour in advance.")



    class Meta:
        constraints = [
            # --- Double-booking prevention: Part 2 (the actual DB rule) ---
            #
            # This is the hard guarantee that Python-level checks (like
            # clean() above) can't fully provide, because it's enforced by
            # Postgres itself at insert/update time — even if two requests
            # race past the application-level check, the database will
            # reject the second conflicting row outright.
            #
            # It blocks any two rows where:
            #   - the doctor is the SAME (RangeOperators.EQUAL on 'doctor')
            #   - AND their time ranges OVERLAP (using our TsTzRange
            #     wrapper to combine start_time/end_time into one range)
            #
            # The condition= clause scopes this rule to only pending/confirmed
            # appointments — a cancelled or completed appointment should never
            # block a new booking for that same slot.
            ExclusionConstraint(
                name="exclude_overlapping_appointments",
                expressions=[
                    (TsTzRange('start_time', 'end_time'), RangeOperators.OVERLAPS),
                    ("doctor", RangeOperators.EQUAL),
                ],
                condition=Q(status__in=['pending', 'confirmed']),
                ),
            ]