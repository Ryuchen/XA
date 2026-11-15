"""Per-order seats and durable handoff counts."""
from django.db import models


class OrderTransferSeat(models.Model):
    order = models.ForeignKey('orders.Order', on_delete=models.PROTECT, related_name='transfer_limit_seats')
    slot = models.PositiveSmallIntegerField()
    initial_account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    current_account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    transfer_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order', 'slot'], name='transfer_limit_order_slot_unique'),
            models.UniqueConstraint(fields=['order', 'current_account'], name='transfer_limit_order_account_unique'),
            models.CheckConstraint(condition=models.Q(slot__in=[1, 2]), name='transfer_limit_slot_valid'),
            models.CheckConstraint(condition=models.Q(transfer_count__lte=2), name='transfer_limit_count_max_two'),
        ]


class OrderTransferSeatEvent(models.Model):
    seat = models.ForeignKey(OrderTransferSeat, on_delete=models.PROTECT, related_name='events')
    sequence = models.PositiveSmallIntegerField()
    old_account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    new_account = models.ForeignKey('club_accounts.ClubAccount', on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['seat', 'sequence'], name='transfer_limit_seat_seq_unique'),
            models.CheckConstraint(condition=models.Q(sequence__in=[1, 2]), name='transfer_limit_sequence_valid'),
            models.CheckConstraint(condition=~models.Q(old_account=models.F('new_account')), name='transfer_limit_accounts_differ'),
        ]
