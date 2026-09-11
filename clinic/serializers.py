# bookings/serializers.py
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from .models import (
    Appointment,
    Availability,
    DoctorProfile,
    PatientProfile,
    Service,
    User,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "role"]
        read_only_fields = [
            "id",
            "role",
        ]  # role assigned at registration, not self-editable via this serializer


class DoctorProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = DoctorProfile
        fields = ["id", "user", "speciality", "license_number", "bio"]


class PatientProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = PatientProfile
        fields = ["id", "user", "date_of_birth", "medical_history_notes"]


class AvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Availability
        fields = ["id", "doctor", "day_of_week", "start_time", "end_time"]

    def validate(self, data):
        if data["start_time"] >= data["end_time"]:
            raise serializers.ValidationError("start_time must be before end_time.")
        return data


class ServiceSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.user.username", read_only=True)

    class Meta:
        model = Service
        fields = ["id", "doctor", "doctor_name", "name", "duration_minutes", "price"]


class AppointmentSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.user.username", read_only=True)
    patient_name = serializers.CharField(source="patient.user.username", read_only=True)

    class Meta:
        model = Appointment
        fields = [
            "id",
            "patient",
            "doctor",
            "service",
            "patient_name",
            "doctor_name",
            "start_time",
            "end_time",
            "status",
        ]
        read_only_fields = [
            "status",
            "patient",
        ]  # status is set by booking logic / doctor / admin, not by the patient directly

    def validate(self, data):
        # Field-level sanity checks
        start = data.get("start_time", getattr(self.instance, "start_time", None))
        end = data.get("end_time", getattr(self.instance, "end_time", None))

        if start and end and start >= end:
            raise serializers.ValidationError("start_time must be before end_time.")
        if start and start < timezone.now():
            raise serializers.ValidationError("Cannot book an appointment in the past.")

        # Reuse your model's clean() overlap logic instead of duplicating it here.
        instance = Appointment(
            id=self.instance.id if self.instance else None,
            patient=data.get("patient", getattr(self.instance, "patient", None)),
            doctor=data.get("doctor", getattr(self.instance, "doctor", None)),
            service=data.get("service", getattr(self.instance, "service", None)),
            start_time=start,
            end_time=end,
        )
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(
                e.message_dict if hasattr(e, "message_dict") else e.messages
            )

        return data
