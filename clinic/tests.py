import threading
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone
from datetime import timedelta
from .models import User, DoctorProfile, PatientProfile, Service, Appointment
from django.test import RequestFactory
from .views import AppointmentViewSet
from rest_framework.exceptions import ValidationError as DRFValidationError
from django.db import connection
class NoticePeriodTest(TestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username='dr_test', password='testpass123', role='doctor'
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality='Cardiology',
            license_number='LIC12345',
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name='Checkup',
            duration_minutes=30,
            price=50.00,
        )
        patient_user = User.objects.create_user(
            username='patient_test', password='testpass123', role='patient'
        )
        self.patient_profile = PatientProfile.objects.create(user=patient_user)

    def test_cannot_book_within_minimum_notice(self):   # <- ab yeh indented hai, class ke andar
        appointment = Appointment(
            patient=self.patient_profile,
            doctor=self.doctor_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(minutes=40),
            end_time=timezone.now() + timedelta(minutes=70),
            status='pending'
        )
        with self.assertRaises(ValidationError):
            appointment.clean()
class CancellationWindowTest(TestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username='dr_test', password='testpass123', role='doctor'
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality='Cardiology',
            license_number='LIC12345',
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name='Checkup',
            duration_minutes=30,
            price=50.00,
        )
        self.patient_user = User.objects.create_user(
            username='patient_test', password='testpass123', role='patient'
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)

    def test_cannot_cancel_within_window(self):
        # Step 1: appointment already-saved — start_time sirf 1 hour baad
        # (2-hour cancellation window se kam, isliye cancel reject hona chahiye)
        appointment = Appointment.objects.create(
            patient=self.patient_profile,
            doctor=self.doctor_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=1, minutes=30),
            status='confirmed',
        )

        # Step 2: fake POST request banana, jisme patient wala user ho
        factory = RequestFactory()
        request = factory.post('/fake-url/')
        request.user = self.patient_user

        # Step 3: viewset ka instance banana, request aur kwargs manually set karna
        view = AppointmentViewSet()
        view.request = request
        view.kwargs = {'pk': appointment.pk}

        # Step 4 + 5: cancel() ko call karna, expect karna ke ValidationError aaye
        with self.assertRaises(DRFValidationError):
            view.cancel(request, pk=appointment.pk)

#Test class for Double booking race condition.
class DoubleBookingRaceConditionTest(TransactionTestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username='dr_race', password='testpass123', role='doctor'
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality='Cardiology',
            license_number='LIC99999',
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name='Checkup',
            duration_minutes=30,
            price=50.00,
        )

        # Do alag patients — dono isi doctor ke isi slot ko book karne ki koshish karenge
        patient_user_1 = User.objects.create_user(
            username='patient_one', password='testpass123', role='patient'
        )
        self.patient_profile_1 = PatientProfile.objects.create(user=patient_user_1)

        patient_user_2 = User.objects.create_user(
            username='patient_two', password='testpass123', role='patient'
        )
        self.patient_profile_2 = PatientProfile.objects.create(user=patient_user_2)

        # Same slot dono ke liye — 1-hour notice se aage, taake notice-period fail na kare
        self.start_time = timezone.now() + timedelta(hours=3)
        self.end_time = self.start_time + timedelta(minutes=30)

        self.results = []  # yahan har thread apna result (success/fail) dalega

    def try_book(self, patient_profile):
        """Yeh function har thread mein chalega — ek booking attempt simulate karta hai."""
        try:
            with transaction.atomic():
                DoctorProfile.objects.select_for_update().get(pk=self.doctor_profile.pk)

                conflict = Appointment.objects.filter(
                    doctor=self.doctor_profile,
                    status__in=['pending', 'confirmed'],
                    start_time__lt=self.end_time,
                    end_time__gt=self.start_time,
                ).exists()

                if conflict:
                    self.results.append('rejected')
                    return

                Appointment.objects.create(
                    patient=patient_profile,
                    doctor=self.doctor_profile,
                    service=self.service,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    status='confirmed',
                )
                self.results.append('success')
        except Exception:
            self.results.append('rejected')
        finally:
            connection.close()  # thread apna connection khud band kare

    def test_only_one_booking_succeeds(self):
        thread1 = threading.Thread(target=self.try_book, args=(self.patient_profile_1,))
        thread2 = threading.Thread(target=self.try_book, args=(self.patient_profile_2,))

        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        # Assert: sirf ek 'success' hona chahiye, ek 'rejected'
        self.assertEqual(self.results.count('success'), 1)
        self.assertEqual(self.results.count('rejected'), 1)

        # Extra confirmation: database mein bhi sirf ek appointment ho is slot ke liye
        total_appointments = Appointment.objects.filter(
            doctor=self.doctor_profile,
            start_time=self.start_time,
        ).count()
        self.assertEqual(total_appointments, 1)