from django.conf import settings
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver

from .models import Wallet


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)


@receiver(post_migrate)
def ensure_platform_wallet(sender, **kwargs):
    if getattr(sender, 'name', '') != 'wallet':
        return
    from .models import get_platform_wallet

    get_platform_wallet()
