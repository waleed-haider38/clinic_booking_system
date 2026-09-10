# Import the Celery app as soon as Django starts, so that
# @shared_task decorators anywhere in the project get registered.
from .celery import app as celery_app

__all__ = ('celery_app',)