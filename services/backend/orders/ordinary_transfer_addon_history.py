"""Receipt-local verification of confirmed add-on handover funding events."""
from django.core.exceptions import ValidationError


def verify_handovers(order, payment, *, before, witness_receipt_id):
    from .addon_funding_release import verify_run
    from .models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation, OrdinaryPostRefundTransferRevision
    from .ordinary_post_refund_transfer import verify_locked as verify_revision
    from .ordinary_transfer_pre_main_addon import verify_handover_at

    handovers = payment.handovers.filter(status='ACCEPTED', accepted_at__lt=before).select_related(
        'payment__intent', 'source_segment__attribution', 'target_segment__attribution').order_by('accepted_at', 'pk')
    for handover in handovers:
        verify_handover_at(payment, handover, before)
        direct = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
        revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
            source_kind='ADDON_HANDOVER', source_id=handover.pk).first()
        revision_run = None
        if revision:
            if revision.created_at >= before:
                raise ValidationError('加购接替资金版本晚于退款事件')
            verify_revision(revision)
            revision_run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
        if (OrderPointsFundingAllocation.objects.filter(service_order=order,
                created_at__lte=handover.accepted_at).exists() and not (direct or revision_run)):
            raise ValidationError('已分配到账的加购接替资金版本缺失')
        for event_run in (direct, revision_run):
            if event_run:
                if event_run.created_at <= handover.accepted_at or event_run.created_at >= before:
                    raise ValidationError('加购接替资金版本时序异常')
                verify_run(event_run, _skip_usage=True, witness_receipt_id=witness_receipt_id)
