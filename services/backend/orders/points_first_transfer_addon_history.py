"""Verify add-on refunds before a refunded order's first transfer."""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError


def _same_team_history(order, posting, payments, refunds, *, receipt_id, before):
    """Replay actual same-team releases before the first post-refund transfer."""
    from .models import OrderAddonFundingReleaseRun, OrderAddonHandover, PointsHandoverRefundInvalidation
    from .addon_funding_release import verify_run
    from .points_addon_receipt_usage import facts as refund_facts, payment_facts
    from .points_handover_refund_invalidation import _verify_facts as verify_invalidation
    from .points_transfer_followup_handover_scope import _verify_rejected
    handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
        created_at__lt=before).select_related('payment__intent').order_by('payment_id', 'version'))
    by_payment = {payment.pk: payment for payment in payments}
    if (any(item.payment_id not in by_payment for item in handovers)
            or any(item.status == 'PENDING' or (item.status == 'ACCEPTED' and
                   (not item.accepted_at or item.accepted_at >= before)) for item in handovers)):
        raise ValidationError('首次转单前同单接替尚未结束')
    for payment in payments:
        payment_facts(payment, read_receipt_id=receipt_id)
        chain = [row for row in handovers if row.payment_id == payment.pk]
        if [row.version for row in chain] != list(range(1, len(chain) + 1)):
            raise ValidationError('首次转单前同单接替版本缺失')
        for index, item in enumerate(chain):
            boundary = chain[index + 1].created_at if index + 1 < len(chain) else before
            if item.status == 'REJECTED':
                _verify_rejected(payment, item, before=boundary)
            elif item.status == 'SUPERSEDED':
                record = PointsHandoverRefundInvalidation.objects.filter(handover=item).select_related(
                    'handover__payment__intent', 'refund').first()
                if not record or record.created_at >= boundary:
                    raise ValidationError('首次转单前接替失效退款缺失')
                verify_invalidation(record, receipt_id=receipt_id)
            elif item.status != 'ACCEPTED':
                raise ValidationError('首次转单前同单接替状态异常')
    events = [(row.accepted_at, 'handover', row) for row in handovers if row.status == 'ACCEPTED']
    events += [(row.created_at, 'refund', row) for row in refunds]
    events += [(row.buyer_transaction.created_at, 'payment', row) for row in payments
               if row.buyer_transaction.created_at > posting.created_at]
    if any(not at or at >= before or at == posting.created_at for at, _, _ in events) or \
            len({at for at, _, _ in events}) != len(events):
        raise ValidationError('首次转单前加购资金事件时序异常')
    previous_at = None
    for at, kind, event in sorted(events, key=lambda item: item[0]):
        run = OrderAddonFundingReleaseRun.objects.filter(**{kind: event}).first()
        if (not run or run.created_at < at or run.created_at >= before
                or (at < posting.created_at <= run.created_at)
                or (previous_at and at <= previous_at)):
            raise ValidationError('首次转单前加购资金事件缺失')
        if at > posting.created_at and (not isinstance(run.refunded_main_snapshot, dict)
                or run.refunded_main_snapshot.get('posting_id') != posting.pk
                or run.refunded_main_snapshot.get('transfer_revision_id')
                or run.refunded_main_snapshot.get('transfer_origin_posting_id')):
            raise ValidationError('首次转单前加购事件的主单退款来源异常')
        if kind == 'handover':
            if not isinstance(run.points_handover_snapshot, dict) or run.points_handover_snapshot.get('kind') != 'SAME_TEAM_V1':
                raise ValidationError('首次转单前同单接替资金版本缺失')
        elif kind == 'refund':
            if not isinstance(run.points_addon_snapshot, dict) or refund_facts(event, read_receipt_id=receipt_id) != run.points_addon_snapshot:
                raise ValidationError('首次转单前实际加购退款异常')
        elif (not isinstance(run.points_payment_snapshot, dict)
              or payment_facts(event, read_receipt_id=receipt_id) != run.points_payment_snapshot):
            raise ValidationError('首次转单前实际加购付款资金版本缺失')
        verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        previous_at = run.created_at
    return refunds


def verify(order, posting, *, receipt_id=None, before=None):
    from .models import OrderAddonFundingReleaseRun, OrderAddonPayment
    from .points_addon_receipt_usage import facts, digest
    from .addon_funding_release import verify_run
    if receipt_id is not None:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import _require_current_unlocked_reads
        _locked_receipt(receipt_id)
        _require_current_unlocked_reads()
    if (OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=posting.created_at).exists()
            or order.addon_refunds.filter(created_at=posting.created_at).exists()):
        raise ValidationError('加购事件与主单退款时间无法区分')
    payments_query = OrderAddonPayment.objects.filter(intent__order=order)
    payments_query = payments_query.filter(buyer_transaction__created_at__lt=before if before is not None else posting.created_at)
    payments = list(payments_query.select_related('buyer_transaction', 'intent'))
    query = order.addon_refunds.all()
    if before is not None:
        if query.filter(created_at=before).exists():
            raise ValidationError('加购退款与转单时间无法区分')
        query = query.filter(created_at__lt=before)
    refunds = list(query.select_related('payment__intent', 'proposal').order_by('created_at', 'pk'))
    if before is not None and (any(row.buyer_transaction.created_at > posting.created_at for row in payments)
            or order.addon_intents.filter(payment__handovers__created_at__lt=before).exists()):
        return _same_team_history(order, posting, payments, refunds, receipt_id=receipt_id, before=before)
    times = [row.buyer_transaction.created_at for row in payments] + [row.created_at for row in refunds]
    by_payment = {item.pk: item for item in payments}
    if (any(not at for at in times) or len(set(times)) != len(times)
            or any(row.payment_id not in by_payment for row in refunds)):
        raise ValidationError('首次转单前加购退款时序或承担范围异常')
    used = {item.pk: 0 for item in payments}
    refunded = {item.pk: 0 for item in payments}
    for refund in refunds:
        original = by_payment[refund.payment_id]
        if original.buyer_transaction.created_at >= refund.created_at:
            raise ValidationError('加购退款早于原付款')
        refunded[original.pk] += refund.amount
        deductions = list(refund.earning_deductions.select_related('participant'))
        if sum(item.amount for item in deductions) != refund.addon_pending_deduction:
            raise ValidationError('加购退款收益扣减合计异常')
        for item in deductions:
            target = by_payment.get(item.payment_id)
            if (not target or target.buyer_transaction.created_at >= refund.created_at
                    or target.intent.provider_id != original.intent.provider_id
                    or item.participant.account_id != original.intent.provider_id
                    or item.segment_id != target.intent.segment_id
                    or item.segment_id != original.intent.segment_id
                    or item.participant.proposal_id != refund.proposal_id):
                raise ValidationError('加购退款跨付款扣减不属于同一陪玩和履约段')
            used[target.pk] += item.amount
        if any(refunded[item.pk] > item.intent.amount or used[item.pk] > item.provider_income
               or item.provider_income - used[item.pk] > item.intent.amount - refunded[item.pk]
               for item in payments if item.buyer_transaction.created_at < refund.created_at):
            raise ValidationError('逐笔退款后的加购剩余收益异常')
        runs = list(OrderAddonFundingReleaseRun.objects.filter(refund=refund))
        if len(runs) != 1 or not isinstance(runs[0].points_addon_snapshot, dict):
            raise ValidationError('首次转单前加购退款的资金事件缺失')
        run = runs[0]
        if (run.created_at < refund.created_at or (before is not None and run.created_at >= before)
                or run.points_addon_hash != digest(run.points_addon_snapshot)):
            raise ValidationError('首次转单前加购退款的释放版本异常')
        if refund.created_at < posting.created_at:
            if run.created_at >= posting.created_at or run.refunded_main_snapshot is not None:
                raise ValidationError('主单退款前的加购释放时序异常')
        elif (refund.created_at <= posting.created_at or not isinstance(run.refunded_main_snapshot, dict)
              or run.refunded_main_snapshot.get('posting_id') != posting.pk
              or run.refunded_main_snapshot.get('transfer_revision_id')
              or run.refunded_main_snapshot.get('transfer_origin_posting_id')):
            raise ValidationError('主单退款后的加购资金来源异常')
        try:
            if facts(refund, read_receipt_id=receipt_id) != run.points_addon_snapshot:
                raise ValidationError('首次转单前加购退款的资金来源不一致')
            verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('首次转单前加购退款的到账凭证异常') from exc
    return refunds
