from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.db.models import Q
from django.db.models import Func

class TsTzRange(Func):
    function = 'TSTZRANGE'
    output_field = DateTimeRangeField()
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

    def __str__(self):
        return f"{self.patient.user.username} with Dr. {self.doctor.user.username} on {self.start_time}"

    def clean(self):
        # Basic overlap check: does this doctor already have an appointment
        # that overlaps with this new one?
        overlapping = Appointment.objects.filter(
            doctor=self.doctor,
            status__in=['pending', 'confirmed'],
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        ).exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError("This doctor already has an appointment during this time slot.")

    class Meta:
        constraints = [
            ExclusionConstraint(
                name="exclude_overlapping_appointments",
                expressions=[
                    (TsTzRange('start_time', 'end_time'), RangeOperators.OVERLAPS),
                    ("doctor", RangeOperators.EQUAL),
                ],
                condition=Q(status__in=['pending', 'confirmed']),
                ),
            ]