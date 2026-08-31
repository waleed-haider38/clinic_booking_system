from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, DoctorProfile, PatientProfile, Availability, Service, Appointment

# Register your models here.
class CustomUserAdmin(UserAdmin):
    list_display = ('username','email', 'role','is_staff', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('Role Info' , {'fields' : ('role',)}),
    )

admin.site.register(User , CustomUserAdmin)
admin.site.register(DoctorProfile)
admin.site.register(PatientProfile)
admin.site.register(Availability)
admin.site.register(Service)
admin.site.register(Appointment)