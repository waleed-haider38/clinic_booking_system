import os
from celery import Celery
from celery.schedules import crontab



# Tell Celery where to find Django's settings — this must run before
# Celery tries to read any CELERY_ settings from settings.py
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Create the Celery application instance. 'config' is just a name for
# this Celery app — matches the Django project name by convention.
app = Celery('config')

# Load all CELERY_-prefixed settings from Django's settings.py
# (e.g. CELERY_BROKER_URL, CELERY_RESULT_BACKEND) into this Celery app.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Automatically look inside every installed app for a tasks.py file
# and register any @shared_task functions found there.
app.autodiscover_tasks()

#Reminder task will run without any trigger action
app.conf.beat_schedule = {
    'send-appointment-reminders-every-30-minutes': {
        'task': 'clinic.tasks.send_appointment_reminders',
        'schedule': 60.0,  # seconds — 1800 = 30 minutes
    },
}