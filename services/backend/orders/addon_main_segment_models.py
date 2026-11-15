from django.db import models


class OrderAddonRefundMainSegmentDeduction(models.Model):
    """The actual unpaid service segment consumed by an add-on refund."""
    participant = models.ForeignKey('orders.OrderAddonRefundParticipant', on_delete=models.PROTECT,
                                    related_name='main_segment_deductions')
    segment = models.ForeignKey('orders.OrderParticipationSegment', on_delete=models.PROTECT,
                                related_name='addon_main_deductions')
    amount = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['participant', 'segment'], name='addon_main_segment_unique'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='addon_main_segment_positive'),
        ]
