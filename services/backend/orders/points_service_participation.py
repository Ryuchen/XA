"""Select the actual service entries after any pre-service replacements."""
from rest_framework.exceptions import ValidationError

from .models import (OrderOriginalShareTransfer, OrderPrestartHandoff,
                     OrderSegmentFinancialSnapshot)


def service_snapshots(order):
    """Keep the last pre-service entry in each seat, including terminated seats."""
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        raise ValidationError('服务中转单须使用逐段履约凭证')
    query = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
    handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
    if handoff:
        from .points_prestart_release import capture
        ids = capture(order, handoff)['segments']
        query = query.filter(segment_id__in=ids)
    rows = list(query.select_related('segment', 'segment__attribution').order_by('segment__slot', 'pk'))
    if handoff and (len(rows) != len(ids) or {row.segment_id for row in rows} != set(ids)):
        raise ValidationError('改派后的服务收益席位缺失')
    return rows
