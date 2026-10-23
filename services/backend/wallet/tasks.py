from celery import shared_task

from .income import release_due_income


@shared_task
def release_due_provider_income():
    return release_due_income()
