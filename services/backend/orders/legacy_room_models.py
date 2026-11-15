"""Manual attestation of a legacy playground completion; no room linkage."""
from django.db import models


class LegacyRoomCompletion(models.Model):
    order = models.OneToOneField('orders.Order', on_delete=models.PROTECT, related_name='legacy_room_completion')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    customer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    provider_accounts = models.JSONField()
    confirmed_at = models.DateTimeField()
    evidence = models.CharField(max_length=1000)
    request_id = models.CharField(max_length=64)
    recorded_at = models.DateTimeField(auto_now_add=True)

