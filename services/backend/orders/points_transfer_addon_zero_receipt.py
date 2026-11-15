"""Current-read proof for a zero-release add-on event on a transferred order."""
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def facts(run, *, receipt_id):
    from django.db.models import Q
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _require_current_unlocked_reads
    from .models import (OrderAddonFundingRelease, OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .points_addon_receipt_usage import digest, facts as refund_facts, payment_facts
    from .points_addon_handover_receipt_usage import postmain_facts
    from .points_transfer_receipt_usage import facts as transfer_facts
    from .points_post_refund_transfer_receipt_usage import facts as post_transfer_facts
    from .points_transfer_refund_receipt_usage import facts as main_facts
    from .refunded_addon_revision import verify as verify_revision
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    if (sum(bool(value) for value in (run.payment_id, run.refund_id, run.handover_id)) != 1
            or not isinstance(run.refunded_main_snapshot, dict)
            or not (run.refunded_main_snapshot.get('transfer_revision_id')
                    or run.refunded_main_snapshot.get('transfer_origin_posting_id'))
            or not isinstance(run.allocation_snapshot, list) or run.released_amount != 0 or run.releases.exists()):
        raise ValidationError('零释放加购事件来源异常')
    order = (run.payment.intent.order if run.payment_id else run.refund.order if run.refund_id
             else run.handover.payment.intent.order)
    witness = OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), receipt_id=receipt_id).order_by('pk').first()
    if not witness:
        raise ValidationError('零释放加购到账见证不属于原订单')
    normalized, net = [], []
    for item in run.allocation_snapshot:
        if (not isinstance(item, list) or len(item) != 6 or any(type(item[i]) is not int for i in range(3))
                or not all(isinstance(group, list) for group in item[3:])):
            raise ValidationError('零释放加购分配快照异常')
        allocation = OrderPointsFundingAllocation.objects.filter(pk=item[0]).first()
        if not allocation or allocation.funding_order_id != order.pk or allocation.amount != item[1]:
            raise ValidationError('零释放加购分配不属于原订单')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation=allocation, run_id__lt=run.pk)
                       .select_related('run', 'allocation').order_by('pk'))
        if sum(row.amount for row in earlier) != item[2]:
            raise ValidationError('零释放加购此前释放不一致')
        for row in earlier:
            _addon_release_facts(row, witness_receipt_id=receipt_id)
        transfers = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=run.created_at).select_related('revision__order', 'allocation__redemption').order_by('pk'))
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation,
            created_at__lt=run.created_at).select_related('posting').order_by('pk'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
            revision__created_at__lt=run.created_at).select_related('revision__order', 'revision__transfer',
                'allocation__redemption').order_by('pk'))
        if ([[row.pk, row.amount] for row in transfers] != item[3]
                or [[row.pk, row.amount] for row in main] != item[4]
                or [[row.pk, row.amount] for row in post] != item[5]):
            raise ValidationError('零释放加购前序资金记录异常')
        for row in transfers:
            transfer_facts(SimpleNamespace(revision=row.revision, allocation=allocation, pk=row.pk,
                amount=row.amount, read_receipt_id=receipt_id))
        for row in main:
            main_facts(SimpleNamespace(posting=row.posting, allocation=allocation, pk=row.pk,
                amount=row.amount, read_receipt_id=receipt_id, history_before=run.created_at))
        for row in post:
            post_transfer_facts(SimpleNamespace(revision=row.revision, allocation=allocation, pk=row.pk,
                amount=row.amount, read_receipt_id=receipt_id))
        used = item[2] + sum(row.amount for row in transfers + main + post)
        if used > allocation.amount:
            raise ValidationError('零释放加购资金超出原分配')
        normalized.append([allocation.pk, allocation.amount, used])
        net.append([allocation.pk, allocation.amount - used, allocation.receipt_id])
    before = sum(amount - used for _, amount, used in normalized)
    try:
        planned = plan_release(allocations=normalized, amount=max(before - run.required_amount, 0))
        verify_revision(run, net, receipt_local=True, witness_allocation=witness)
        if run.refund_id:
            from .points_voluntary_paid_evidence import verify_current_read
            verify_current_read(run)
        if run.payment_id:
            from .points_voluntary_paid_evidence import verify_payment_current_read
            verify_payment_current_read(run)
        if (run.handover_id and isinstance(run.points_handover_snapshot, dict)
                and run.points_handover_snapshot.get('kind') == 'POINTS_VOLUNTARY_HANDOVER_V1'):
            from .points_voluntary_handover_event import facts as voluntary_facts
            actual = voluntary_facts(run, receipt_id=receipt_id)
        else:
            actual = (payment_facts(run.payment, read_receipt_id=receipt_id) if run.payment_id else
                      refund_facts(run.refund, read_receipt_id=receipt_id) if run.refund_id else
                      postmain_facts(run, receipt_id))
    except (RestValidationError, PointsFundingAllocationError, TypeError) as exc:
        raise ValidationError('零释放加购资金或来源异常') from exc
    snapshot = (run.points_payment_snapshot if run.payment_id else run.points_addon_snapshot if run.refund_id
                else run.points_handover_snapshot)
    event_hash = (run.points_payment_hash if run.payment_id else run.points_addon_hash if run.refund_id
                  else run.points_handover_hash)
    if (before != run.allocated_before or planned or not isinstance(snapshot, dict)
            or event_hash != digest(snapshot) or actual != snapshot):
        raise ValidationError('零释放加购事件与资金计划不一致')
    return {'run_id': run.pk, 'event_hash': event_hash, 'funding_hash': run.refunded_main_hash}
