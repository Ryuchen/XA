from celery import shared_task

from .income import release_due_income


@shared_task
def release_due_provider_income():
    return release_due_income()


@shared_task
def recover_withdrawal_notice_messages():
    from .withdraw_notice_recovery import recover_withdrawal_messages
    return recover_withdrawal_messages()
