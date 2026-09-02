
from rest_framework import permissions


class IsDoctorOrReadOnly(permissions.BasePermission):
    """Only users with role='doctor' can create/edit Services & Availability."""
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_authenticated and request.user.role == 'doctor'

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.doctor.user == request.user


class IsOwnerPatientOrDoctorReadOnly(permissions.BasePermission):
    """
    Patients: full control over their own appointments only.
    Doctors: can view/update (not delete) appointments assigned to them.
    Admins: full access.
    """
    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.role == 'admin':
            return True
        if user.role == 'patient':
            return obj.patient.user == user
        if user.role == 'doctor':
            if request.method == 'DELETE':
                return False
            return obj.doctor.user == user
        return False


class IsAdminRole(permissions.BasePermission):
    """Admin-only endpoints, e.g. listing raw User records."""
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'admin'