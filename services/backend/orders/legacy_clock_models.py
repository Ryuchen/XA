"""Auditable per-order snapshot of the new rules for an unfinished legacy order."""
from django.db import models


class LegacyClockCutover(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='legacy_clock_cutover')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=64)
    plan_fingerprint = models.CharField(max_length=64)
    previous = models.JSONField()
    rules = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)
