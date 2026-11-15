"""Unpaid ordinary service income at a real earlier add-on refund event."""
from django.core.exceptions import ValidationError


def deduction_at(order, account_id, boundary, amount):
    """Replay the recorded writer's per-segment allocation at this refund."""
    from django.db.models import Q
    from .addon_main_segment_deduction import verified_deductions
    from .models import OrderOriginalShareTransfer, OrderParticipationSegment, OrderSegmentFinancialSnapshot

    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
    if transfers.filter(created_at=boundary).exists():
        raise ValidationError('主单转单与加购退款时序无法区分')
    if transfers.filter(created_at__lt=boundary).exists():
        from .ordinary_transfer_event_scope import segment_lines_at
        lines = [row for row in segment_lines_at(order, boundary) if row.account_id == account_id]
        income = {row.segment_id: row.income for row in lines}
        if len(income) != len(lines):
            raise ValidationError('加购退款本人历史履约段重复')
    else:
        snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
            segment__provider_account_id=account_id, segment__accepted_at__lt=boundary,
            segment__started_at__isnull=False, segment__started_at__lt=boundary).filter(
                Q(segment__released_at__isnull=True) | Q(segment__released_at__gt=boundary))
        income = {row.segment_id: row.income_at_entry for row in snapshots}
    if not income:
        raise ValidationError('加购退款本人历史履约快照缺失')
    used = verified_deductions(order, before=boundary)
    segments = list(OrderParticipationSegment.objects.filter(pk__in=income,
        provider_account_id=account_id).order_by('-accepted_at', '-pk').values_list('pk', flat=True))
    if len(segments) != len(income):
        raise ValidationError('加购退款本人历史履约段归属异常')
    remaining, parts = amount, []
    for segment_id in segments:
        available = income[segment_id] - used.get(segment_id, 0)
        if available < 0:
            raise ValidationError('加购退款历史主单收益扣减异常')
        take = min(remaining, available)
        if take:
            parts.append((segment_id, take))
            remaining -= take
    return parts
