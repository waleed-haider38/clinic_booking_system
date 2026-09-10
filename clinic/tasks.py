from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone
from .models import Appointment
from django.conf import settings


@shared_task
def send_confirmation_email(appointment_id):
    # yahan Appointment.objects.get() se object nikalna hoga
    # phir send_mail() use karke email bhejna hoga
    appointment = Appointment.objects.get(id=appointment_id)

    patient_email = appointment.patient.user.email
    doctor_name = appointment.doctor.user.username
    service_name = appointment.service.name
    start_time = appointment.start_time.strftime("%Y-%m-%d %H:%M UTC")

    subject = "Appointment Confirmation - Clinic Booking"
    message = (
            f"Hello {appointment.patient.user.username},\n\n"
            f"Your appointment for '{service_name}' with Dr. {doctor_name} "
            f"has been confirmed for {start_time}.\n\n"
            f"Thank you for choosing our clinic!"
        )
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[patient_email],
        fail_silently=False,
    )


@shared_task
def send_cancellation_email(appointment_id):
    # Step A: appointment object nikalna (jaisa pehle task mein kiya tha)
    appointment = Appointment.objects.get(id=appointment_id)
    # Step B: patient_email, doctor_name, service_name, start_time nikalna
        #         (bilkul same jaisa confirmation email mein tha)
    patient_email = appointment.patient.user.email
    doctor_name = appointment.doctor.user.username
    service_name = appointment.service.name
    start_time = appointment.start_time.strftime("%Y-%m-%d %H:%M UTC")
    
    
    
    
    # Step C: subject aur message likhna — is baar "cancelled" wording ke sath
    subject = "Appointment Cancelled - Clinic Booking"
    message = (
            f"Hello {appointment.patient.user.username},\n\n"
            f"Your appointment for '{service_name}' with Dr. {doctor_name} "
            f"has been cancelled for {start_time}.\n\n"
            f"We are sorry for this cancellation"
            f"Thank you for choosing our clinic!"
        )
    
    # Step D: send_mail() call karna
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[patient_email],
        fail_silently=False,
    )

#Reminder Email.

@shared_task
def send_appointment_reminders():
    now = timezone.now()
    reminder_window_end = now + timedelta(hours=24)

    appointments = Appointment.objects.filter(
        status='confirmed',
        reminder_sent=False,
        start_time__gte=now,
        start_time__lte=reminder_window_end,
    )

    for appointment in appointments:
        patient_email = appointment.patient.user.email
        doctor_name = appointment.doctor.user.username
        service_name = appointment.service.name
        start_time = appointment.start_time.strftime("%Y-%m-%d %H:%M UTC")

        subject = "Appointment Reminder - Clinic Booking"
        message = (
            f"Hello {appointment.patient.user.username},\n\n"
            f"This is a reminder that you have an upcoming appointment for "
            f"'{service_name}' with Dr. {doctor_name} on {start_time}.\n\n"
            f"See you soon!"
        )

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[patient_email],
            fail_silently=False,
        )

        appointment.reminder_sent = True
        appointment.save()