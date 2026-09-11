import threading
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from clinic.serializers import AppointmentSerializer

from .models import Appointment, DoctorProfile, PatientProfile, Service, User
from .permissions import IsAdminRole, IsDoctorOrReadOnly, IsOwnerPatientOrDoctorReadOnly
from .views import AppointmentViewSet


class NoticePeriodTest(TestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username="dr_test", password="testpass123", role="doctor"
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality="Cardiology",
            license_number="LIC12345",
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=50.00,
        )
        patient_user = User.objects.create_user(
            username="patient_test", password="testpass123", role="patient"
        )
        self.patient_profile = PatientProfile.objects.create(user=patient_user)

    def test_cannot_book_within_minimum_notice(
        self,
    ):  # <- ab yeh indented hai, class ke andar
        appointment = Appointment(
            patient=self.patient_profile,
            doctor=self.doctor_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(minutes=40),
            end_time=timezone.now() + timedelta(minutes=70),
            status="pending",
        )
        with self.assertRaises(ValidationError):
            appointment.clean()


class CancellationWindowTest(TestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username="dr_test", password="testpass123", role="doctor"
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality="Cardiology",
            license_number="LIC12345",
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=50.00,
        )
        self.patient_user = User.objects.create_user(
            username="patient_test", password="testpass123", role="patient"
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
            status="confirmed",
        )

        # Step 2: fake POST request banana, jisme patient wala user ho
        factory = RequestFactory()
        request = factory.post("/fake-url/")
        request.user = self.patient_user

        # Step 3: viewset ka instance banana, request aur kwargs manually set karna
        view = AppointmentViewSet()
        view.request = request
        view.kwargs = {"pk": appointment.pk}

        # Step 4 + 5: cancel() ko call karna, expect karna ke ValidationError aaye
        with self.assertRaises(DRFValidationError):
            view.cancel(request, pk=appointment.pk)


# Test class for Double booking race condition.
class DoubleBookingRaceConditionTest(TransactionTestCase):
    def setUp(self):
        doctor_user = User.objects.create_user(
            username="dr_race", password="testpass123", role="doctor"
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=doctor_user,
            speciality="Cardiology",
            license_number="LIC99999",
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=50.00,
        )

        # Do alag patients — dono isi doctor ke isi slot ko book karne ki koshish karenge
        patient_user_1 = User.objects.create_user(
            username="patient_one", password="testpass123", role="patient"
        )
        self.patient_profile_1 = PatientProfile.objects.create(user=patient_user_1)

        patient_user_2 = User.objects.create_user(
            username="patient_two", password="testpass123", role="patient"
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
                    status__in=["pending", "confirmed"],
                    start_time__lt=self.end_time,
                    end_time__gt=self.start_time,
                ).exists()

                if conflict:
                    self.results.append("rejected")
                    return

                Appointment.objects.create(
                    patient=patient_profile,
                    doctor=self.doctor_profile,
                    service=self.service,
                    start_time=self.start_time,
                    end_time=self.end_time,
                    status="confirmed",
                )
                self.results.append("success")
        except Exception:
            self.results.append("rejected")
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
        self.assertEqual(self.results.count("success"), 1)
        self.assertEqual(self.results.count("rejected"), 1)

        # Extra confirmation: database mein bhi sirf ek appointment ho is slot ke liye
        total_appointments = Appointment.objects.filter(
            doctor=self.doctor_profile,
            start_time=self.start_time,
        ).count()
        self.assertEqual(total_appointments, 1)


class IsAdminRolePermissionTest(TestCase):
    def setUp(self):
        # Create a user with role='admin'. create_user() is used (not
        # create()) because it properly hashes the password, matching
        # how real users are created in the app.
        self.admin_user = User.objects.create_user(
            username="admin_test", password="testpass123", role="admin"
        )
        # Create a second user with role='patient', to prove the
        # permission correctly REJECTS non-admin roles, not just that
        # it accepts admins.
        self.patient_user = User.objects.create_user(
            username="patient_test", password="testpasss123", role="patient"
        )
        # RequestFactory lets us build a lightweight, fake HTTP request
        # without running a real server — enough for permission classes,
        # which only look at request.user.
        self.factory = RequestFactory()

    def test_admin_user_has_permission(self):
        # Build a fake GET request. The HTTP method doesn't matter here,
        # since IsAdminRole only checks the user's role, not the method.
        request = self.factory.get("/fake-url/")
        # Manually attach the admin user to the request — normally
        # Django's authentication middleware does this automatically
        # for a real request, but in a test we set it ourselves.
        request.user = self.admin_user

        # Create an instance of the permission class we're testing.
        permission = IsAdminRole()
        # has_permission() is the actual method DRF calls internally
        # on every request. We call it directly here to test its logic
        # in isolation, without needing a real view or URL.
        # The second argument is normally the view, but IsAdminRole
        # doesn't use it, so None is a safe stand-in.
        self.assertTrue(permission.has_permission(request, None))
        # assertTrue expects the result to be True — i.e. an admin
        # user SHOULD be allowed through. If has_permission() ever
        # returns False for an admin, this test will fail and catch it.

    def test_non_admin_user_denied(self):
        # Same setup as above, but this time with the patient user —
        # to confirm the permission correctly blocks non-admins.
        request = self.factory.get("/fake-url/")
        request.user = self.patient_user

        permission = IsAdminRole()
        # assertFalse expects the result to be False — i.e. a patient
        # user should NOT be allowed through. This is just as important
        # as the "admin allowed" test: a permission class that always
        # returns True would pass test_admin_user_has_permission but
        # be completely broken. This test catches that.
        self.assertFalse(permission.has_permission(request, None))


class IsDoctorOrReadOnlyPermissionTest(TestCase):
    def setUp(self):
        self.doctor_1 = User.objects.create_user(
            username="doctor_1", password="testpass123", role="doctor"
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=self.doctor_1, speciality="Neurology", license_number="LIC54321"
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=100.00,
        )
        self.doctor_2 = User.objects.create_user(
            username="doctor_2", password="testpass123", role="doctor"
        )
        self.patient = User.objects.create_user(
            username="patient", password="testpass123", role="patient"
        )
        self.factory = RequestFactory()

    def test_get_request_allowed_for_anyone(self):
        request = self.factory.get("fake-url")
        request.user = self.patient
        permission = IsDoctorOrReadOnly()
        self.assertTrue(permission.has_permission(request, None))

    def test_post_request_allowed_for_doctor(self):
        request = self.factory.post("fake-url")
        request.user = self.doctor_1
        permission = IsDoctorOrReadOnly()
        self.assertTrue(permission.has_permission(request, None))

    def test_post_request_denied_for_patient(self):
        request = self.factory.post("fake-url")
        request.user = self.patient
        permission = IsDoctorOrReadOnly()
        self.assertFalse(permission.has_permission(request, None))

    def test_object_permission_allowed_for_owning_doctor(self):
        request = self.factory.put("fake-url")
        request.user = self.doctor_1
        permission = IsDoctorOrReadOnly()
        self.assertTrue(permission.has_object_permission(request, None, self.service))

    def test_object_permission_denied_for_other_doctor(self):
        request = self.factory.put("fake-url")
        request.user = self.doctor_2
        permission = IsDoctorOrReadOnly()
        self.assertFalse(permission.has_object_permission(request, None, self.service))


class IsOwnerPatientOrDoctorReadOnlyPermissionTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_test", password="testpass123", role="admin"
        )
        self.doctor_1 = User.objects.create_user(
            username="doctor_1", password="testpass123", role="doctor"
        )
        self.doctor_2 = User.objects.create_user(
            username="doctor_2", password="testpass123", role="doctor"
        )
        self.patient_1 = User.objects.create_user(
            username="patient_1", password="testpass123", role="patient"
        )
        self.patient_2 = User.objects.create_user(
            username="patient_2", password="testpass123", role="patient"
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_1)
        self.doctor_profile = DoctorProfile.objects.create(
            user=self.doctor_1,
            speciality="Physiology",
            license_number="LIC7890",
        )
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=300.00,
        )
        self.appointment = Appointment.objects.create(
            doctor=self.doctor_profile,
            patient=self.patient_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(hours=1),
            end_time=timezone.now() + timedelta(hours=1, minutes=30),
            status="confirmed",
        )
        self.factory = RequestFactory()

    def test_admin_has_full_access(self):
        request = self.factory.get("fake-url")
        request.user = self.admin
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertTrue(
            permission.has_object_permission(request, None, self.appointment)
        )

    def test_owning_patient_allowed(self):
        request = self.factory.get("fake-url")
        request.user = self.patient_1
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertTrue(
            permission.has_object_permission(request, None, self.appointment)
        )

    def test_other_patient_denied(self):
        request = self.factory.get("fake-url")
        request.user = self.patient_2
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertFalse(
            permission.has_object_permission(request, None, self.appointment)
        )

    def test_assigned_doctor_allowed_on_get(self):
        request = self.factory.get("fake-url")
        request.user = self.doctor_1
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertTrue(
            permission.has_object_permission(request, None, self.appointment)
        )

    def test_assigned_doctor_denied_on_delete(self):
        request = self.factory.delete("fake-url")
        request.user = self.doctor_1
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertFalse(
            permission.has_object_permission(request, None, self.appointment)
        )

    def test_other_doctor_denied(self):
        request = self.factory.get("fake-url")
        request.user = self.doctor_2
        permission = IsOwnerPatientOrDoctorReadOnly()
        self.assertFalse(
            permission.has_object_permission(request, None, self.appointment)
        )


class AppointmentViewSetTest(TestCase):
    def setUp(self):
        self.doctor_user = User.objects.create_user(
            username="doctor", password="testpass123", role="doctor"
        )
        self.doctor_profile = DoctorProfile.objects.create(
            user=self.doctor_user,
            speciality="Dermatology",
            license_number="LIC1111",
        )
        self.patient_user = User.objects.create_user(
            username="patient", password="testpass123", role="patient"
        )
        self.patient_profile = PatientProfile.objects.create(user=self.patient_user)
        self.service = Service.objects.create(
            doctor=self.doctor_profile,
            name="Checkup",
            duration_minutes=30,
            price=50.00,
        )
        self.factory = RequestFactory()

    def test_perform_create_successful_booking(self):
        request = self.factory.post("/fake-url/")
        request.user = self.patient_user

        view = AppointmentViewSet()
        view.request = request

        serializer = AppointmentSerializer(
            data={
                "doctor": self.doctor_profile.id,
                "service": self.service.id,
                "start_time": (timezone.now() + timedelta(hours=2)).isoformat(),
                "end_time": (
                    timezone.now() + timedelta(hours=2, minutes=30)
                ).isoformat(),
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

        view.perform_create(serializer)

        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Appointment.objects.first().status, "pending")

    def test_perform_create_rejects_conflicting_slot(self):
        existing_start = timezone.now() + timedelta(hours=2)
        existing_end = existing_start + timedelta(minutes=30)
        Appointment.objects.create(
            doctor=self.doctor_profile,
            patient=self.patient_profile,
            service=self.service,
            start_time=existing_start,
            end_time=existing_end,
            status="confirmed",
        )

        serializer = AppointmentSerializer(
            data={
                "doctor": self.doctor_profile.id,
                "service": self.service.id,
                "start_time": existing_start.isoformat(),
                "end_time": existing_end.isoformat(),
            }
        )

        # Conflict is caught at the serializer level (via clean()),
        # before perform_create() is even reached.
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_cancel_successful_outside_window(self):
        # Step 1: ek appointment banayein — start_time 3 hours baad (window se bahar)
        appointment = Appointment.objects.create(
            doctor=self.doctor_profile,
            patient=self.patient_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(hours=3),
            end_time=timezone.now() + timedelta(hours=3, minutes=30),
            status="confirmed",
        )

        # Step 2: fake request, patient attach
        request = self.factory.post("fake-url")
        request.user = self.patient_user

        # Step 3: viewset instance — request aur kwargs dono set karna zaroori hai
        view = AppointmentViewSet()
        view.request = request
        view.kwargs = {"pk": appointment.pk}

        # Step 4: cancel() ko manually call karna
        view.cancel(request, pk=appointment.pk)

        # Step 5: assert karna — database se fresh value nikaal kar check karna
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, "cancelled")

    def test_cancel_already_cancelled_rejected(self):
        appointment = Appointment.objects.create(
            doctor=self.doctor_profile,
            patient=self.patient_profile,
            service=self.service,
            start_time=timezone.now() + timedelta(hours=3),
            end_time=timezone.now() + timedelta(hours=3, minutes=30),
            status="cancelled",
        )

        request = self.factory.get("fake-url")
        request.user = self.patient_user

        view = AppointmentViewSet()
        view.request = request
        view.kwargs = {"pk": appointment.pk}

        with self.assertRaises(DRFValidationError):
            view.cancel(request, pk=appointment.pk)
