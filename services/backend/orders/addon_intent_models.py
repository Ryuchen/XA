from django.db import models


class OrderAddonIntent(models.Model):
    """A reviewed purchase intent; it never represents a payment or entitlement."""

    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='addon_intents')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT, related_name='+')
    provider = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    addon = models.ForeignKey('orders.ServiceItem', on_delete=models.PROTECT, related_name='+')
    buyer = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    operator = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    request_id = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=64)
    unit = models.CharField(max_length=12)
    quantity = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField()
    amount = models.PositiveIntegerField()
    principal = models.PositiveIntegerField()
    gift = models.PositiveIntegerField(default=0)
    compensation = models.PositiveIntegerField(default=0)
    config_version = models.PositiveIntegerField()
    condition_mode = models.CharField(max_length=16)
    condition_snapshot = models.CharField(max_length=500, blank=True, default='')
    addon_name = models.CharField(max_length=100)
    commission_snapshot = models.JSONField()
    confirmation = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'request_id'], name='addon_intent_request_unique'),
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name='addon_intent_quantity_positive'),
            models.CheckConstraint(condition=models.Q(amount=models.F('unit_price') * models.F('quantity')),
                                   name='addon_intent_price_total_valid'),
            models.CheckConstraint(condition=models.Q(amount=models.F('principal') + models.F('gift') + models.F('compensation')),
                                   name='addon_intent_sources_sum'),
            models.CheckConstraint(condition=models.Q(condition_mode__in=['NONE', 'STAFF_REVIEW']),
                                   name='addon_intent_condition_valid'),
            models.CheckConstraint(condition=models.Q(unit__in=['HOUR', 'ROUND', 'ITEM', 'PACKAGE']),
                                   name='addon_intent_unit_valid'),
        ]
