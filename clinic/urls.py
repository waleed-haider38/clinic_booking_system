# clinic/urls.py
from rest_framework.routers import DefaultRouter
from .views import (
    UserViewSet, DoctorProfileViewSet, PatientProfileViewSet,
    AvailabilityViewSet, ServiceViewSet, AppointmentViewSet,
)

router = DefaultRouter()
router.register(r'users', UserViewSet)
router.register(r'doctors', DoctorProfileViewSet)
router.register(r'patients', PatientProfileViewSet)
router.register(r'availabilities', AvailabilityViewSet)
router.register(r'services', ServiceViewSet)
router.register(r'appointments', AppointmentViewSet)

urlpatterns = router.urls