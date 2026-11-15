import uuid

from django.db import models


class BossRegistrationChallenge(models.Model):
    """Store-local, single-use SMS proof for a new boss account."""
    phone = models.CharField(max_length=11, primary_key=True)
    nonce = models.UUIDField(default=uuid.uuid4, unique=True)
    code_hash = models.CharField(max_length=64)
    sent_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed = models.BooleanField(default=False)
