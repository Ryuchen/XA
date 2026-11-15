from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import ensure_entity_number, numbered_model


@receiver(post_save, dispatch_uid='identifiers-assign-entity-number')
def assign_entity_number(sender, instance, created, raw=False, using=None, **kwargs):
    if not created or raw or not numbered_model(sender):
        return
    try:
        ensure_entity_number(instance, using=using)
    except (OperationalError, ProgrammingError) as exc:
        # The initial identifier migration also saves django_migrations rows.
        if 'identifiers_entitynumber' in str(exc):
            return
        raise
