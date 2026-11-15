"""Verify post-main add-on events for a transferred points order."""
from rest_framework.exceptions import ValidationError


def later_runs(order, posting, *, before=None):
    from .models import OrderAddonHandover, OrderAddonFundingReleaseRun, PointsNegotiationPosting
    from .points_refunded_funding import later_addon_runs
    from .addon_funding_release import verify_run
    base = later_addon_runs(order, posting, before=before)
    following = PointsNegotiationPosting.objects.filter(
        proposal__points_terms__prior_refund_id=posting.points_refund_id).order_by('created_at', 'pk').first()
    events = OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                                               accepted_at__gt=posting.created_at)
    if before is not None:
        if events.filter(accepted_at=before).exists():
            raise ValidationError('加购接替与历史凭证时间无法区分')
        events = events.filter(accepted_at__lt=before)
    if following:
        if events.filter(accepted_at=following.created_at).exists():
            raise ValidationError('接替与主单追加退款时序无法区分')
        events = events.filter(accepted_at__lt=following.created_at)
    handovers = list(events.order_by('accepted_at', 'pk'))
    runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__in=handovers)
                .select_related('handover__payment__intent'))
    if len(runs) != len(handovers) or {row.handover_id for row in runs} != {row.pk for row in handovers}:
        raise ValidationError('主单退款后的接替资金版本缺失')
    for run in runs:
        if (not run.refunded_main_snapshot or not run.points_handover_snapshot
                or run.created_at <= run.handover.accepted_at):
            raise ValidationError('主单退款后的接替资金凭证异常')
        verify_run(run)
    combined = base + runs
    def event_at(run):
        return (run.payment.buyer_transaction.created_at if run.payment_id else
                run.refund.created_at if run.refund_id else run.handover.accepted_at)
    if len({event_at(row) for row in combined}) != len(combined):
        raise ValidationError('主单退款后的加购事件时序无法区分')
    combined.sort(key=event_at)
    if any(left.pk >= right.pk or left.created_at > right.created_at for left, right in zip(combined, combined[1:])):
        raise ValidationError('主单退款后的加购版本顺序不一致')
    return combined
