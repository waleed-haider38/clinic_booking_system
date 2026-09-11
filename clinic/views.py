from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import (
    Appointment,
    Availability,
    DoctorProfile,
    PatientProfile,
    Service,
    User,
)
from .permissions import IsAdminRole, IsDoctorOrReadOnly, IsOwnerPatientOrDoctorReadOnly
from .serializers import (
    AppointmentSerializer,
    AvailabilitySerializer,
    DoctorProfileSerializer,
    PatientProfileSerializer,
    ServiceSerializer,
    UserSerializer,
)
from .tasks import send_cancellation_email, send_confirmation_email


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminRole]  # only admins can list/manage raw User records


class DoctorProfileViewSet(viewsets.ModelViewSet):
    queryset = DoctorProfile.objects.all()
    serializer_class = DoctorProfileSerializer
    permission_classes = [
        permissions.IsAuthenticatedOrReadOnly
    ]  # anyone (even logged out) can browse doctors

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "date", str, description="Date in YYYY-MM-DD format", required=True
            ),
            OpenApiParameter(
                "service_id",
                int,
                description="Optional service ID to size the slot duration",
                required=False,
            ),
        ]
    )
    @action(detail=True, methods=["get"], url_path="available-slots")
    def available_slots(self, request, pk=None):
        """
        GET /api/doctors/{id}/available-slots/?date=YYYY-MM-DD&service_id=1
        Returns free time slots for this doctor on the given date.
        """
        doctor = self.get_object()
        date_str = request.query_params.get("date")
        service_id = request.query_params.get("service_id")

        if not date_str:
            return Response(
                {"error": "date query param is required (YYYY-MM-DD)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "date must be in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # figure out slot length from the requested service, default 30 min
        slot_minutes = 30
        if service_id:
            try:
                service = Service.objects.get(id=service_id, doctor=doctor)
                slot_minutes = service.duration_minutes
            except Service.DoesNotExist:
                return Response(
                    {"error": "service not found for this doctor."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        day_name = target_date.strftime("%A").lower()  # e.g. 'monday'
        availabilities = Availability.objects.filter(
            doctor=doctor, day_of_week=day_name
        )

        existing = Appointment.objects.filter(
            doctor=doctor,
            status__in=["pending", "confirmed"],
            start_time__date=target_date,
        ).values_list("start_time", "end_time")
        booked_ranges = [
            (timezone.localtime(s), timezone.localtime(e)) for s, e in existing
        ]

        free_slots = []
        for avail in availabilities:
            slot_start = timezone.make_aware(
                datetime.combine(target_date, avail.start_time)
            )
            window_end = timezone.make_aware(
                datetime.combine(target_date, avail.end_time)
            )

            while slot_start + timedelta(minutes=slot_minutes) <= window_end:
                slot_end = slot_start + timedelta(minutes=slot_minutes)
                overlaps = any(
                    b_start < slot_end and b_end > slot_start
                    for b_start, b_end in booked_ranges
                )
                if not overlaps and slot_start > timezone.now():
                    free_slots.append(
                        {
                            "start_time": slot_start.isoformat(),
                            "end_time": slot_end.isoformat(),
                        }
                    )
                slot_start = slot_end

        return Response(
            {
                "doctor": doctor.user.username,
                "date": date_str,
                "available_slots": free_slots,
            }
        )


class PatientProfileViewSet(viewsets.ModelViewSet):
    queryset = PatientProfile.objects.all()
    serializer_class = PatientProfileSerializer
    permission_classes = [
        permissions.IsAuthenticated
    ]  # patient data stays private to logged-in users

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return (
                PatientProfile.objects.none()
            )  # safe fallback for schema generation / anonymous calls
        if getattr(user, "role", None) == "admin":
            return PatientProfile.objects.all()
        return PatientProfile.objects.filter(
            user=user
        )  # patients only ever see their own profile


class AvailabilityViewSet(viewsets.ModelViewSet):
    queryset = Availability.objects.all()
    serializer_class = AvailabilitySerializer
    permission_classes = [IsDoctorOrReadOnly]


class ServiceViewSet(viewsets.ModelViewSet):
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    permission_classes = [IsDoctorOrReadOnly]


class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = Appointment.objects.all()
    serializer_class = AppointmentSerializer
    permission_classes = [IsOwnerPatientOrDoctorReadOnly]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return (
                Appointment.objects.none()
            )  # safe fallback for schema generation / anonymous calls
        role = getattr(user, "role", None)
        if role == "admin":
            return Appointment.objects.all()
        if role == "doctor":
            return Appointment.objects.filter(doctor__user=user)
        return Appointment.objects.filter(patient__user=user)

    def perform_create(self, serializer):
        patient_profile = PatientProfile.objects.get(user=self.request.user)

        doctor = serializer.validated_data["doctor"]
        start_time = serializer.validated_data["start_time"]
        end_time = serializer.validated_data["end_time"]

        with transaction.atomic():
            DoctorProfile.objects.select_for_update().get(pk=doctor.pk)

            conflict = Appointment.objects.filter(
                doctor=doctor,
                status__in=["pending", "confirmed"],
                start_time__lt=end_time,
                end_time__gt=start_time,
            ).exists()
            if conflict:
                raise ValidationError(
                    "This slot was just booked by someone else. Please pick another."
                )

            serializer.save(patient=patient_profile)
            appointment = serializer.instance
            transaction.on_commit(lambda: send_confirmation_email.delay(appointment.id))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        appointment = self.get_object()
        # finding current time
        current_time = timezone.now()
        # Checking appoinment cancelling time. It it is less than 2 hours appoinment will not be cancelled.
        if appointment.start_time - current_time < timedelta(hours=2):
            raise ValidationError("We cannot move forward with this request")
        # If appoinment already cancelled out then this error with gave up
        if appointment.status in ["cancelled", "completed"]:
            raise ValidationError("This appointment cannot be cancelled.")
        appointment.status = "cancelled"
        appointment.save()
        transaction.on_commit(lambda: send_cancellation_email.delay(appointment.id))
        return Response({"status": "cancelled"})
