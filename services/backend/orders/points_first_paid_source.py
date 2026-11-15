"""Current-read verification of an actual payout before the first refund.

Receipt callers already hold their own receipt lock: this routine never locks
the order, money mutex, another receipt or the provider wallet.
"""
import hashlib
import json
from collections import defaultdict

from django.core.exceptions import ValidationError


def verify_original_addons(order, settlement, person, boundary, *, receipt_id=None):
    """Verify paid add-ons and this person's original credits at the boundary."""
    from .models import OrderAddonPayment, OrderAddonFundingReleaseRun, OrderAddonHandover, OrderAddonRefund
    from .addon_payment import verify_receipt
    from .addon_handover import current_responsibilities, verify_handover
    from .addon_funding_release import verify_run
    from rest_framework.exceptions import ValidationError as RestValidationError
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related(
            'intent__segment__attribution', 'buyer_transaction__wallet').order_by('pk'))
    if OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=boundary).exists():
        raise ValidationError('加购付款与首退时间无法区分')
    accepted = list(OrderAddonHandover.objects.filter(payment__intent__order=order,
        status='ACCEPTED', accepted_at__lt=boundary).order_by('accepted_at', 'pk'))
    if accepted:
        refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lte=boundary)
                       .order_by('created_at', 'pk'))
        if (any(not item.accepted_at or item.accepted_at >= person.settled_at for item in accepted)
                or any(not row.created_at or row.created_at >= person.settled_at for row in refunds)):
            raise ValidationError('首退前同单接替与退款时序待核对')
        events = [(item.accepted_at, 'handover', item) for item in accepted]
        events.extend((row.created_at, 'refund', row) for row in refunds)
        previous_at = None
        for event_at, kind, event in sorted(events, key=lambda item: item[0]):
            run = OrderAddonFundingReleaseRun.objects.filter(**{kind: event}).first()
            if (not run or not event_at or (previous_at and event_at <= previous_at)
                    or run.created_at < event_at or run.created_at >= person.settled_at):
                raise ValidationError('首退前同单资金事件顺序待核对')
            try:
                verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
            except (RestValidationError, ValidationError) as exc:
                raise ValidationError('首退前同单资金事件凭证异常') from exc
            previous_at = run.created_at
    expected = {}
    for payment in payments:
        if (not payment.buyer_transaction.created_at or payment.buyer_transaction.created_at >= person.settled_at
                or payment.intent.segment.attribution.order_id != order.pk
                or payment.intent.segment.provider_account_id != payment.intent.provider_id
                or payment.refund_records.filter(created_at__gte=person.settled_at,
                                                 created_at__lte=boundary).exists()):
            raise ValidationError('首退前加购付款或责任范围待核对')
        try:
            verify_receipt(payment)
            attempts = list(payment.handovers.filter(created_at__lte=boundary).order_by('version', 'pk'))
            if any(item.status not in ('ACCEPTED', 'REJECTED', 'SUPERSEDED') or item.created_at >= person.settled_at
                   for item in attempts):
                raise ValidationError('首退前加购接替方案状态待核对')
            if attempts:
                from .points_team_addon_handover_facts import verify_rejected_attempts, _verify_invalidated
                verify_rejected_attempts(payment, before=person.settled_at)
                for index, attempt in enumerate(attempts):
                    if attempt.status == 'SUPERSEDED':
                        cutoff = attempts[index + 1].created_at if index + 1 < len(attempts) else person.settled_at
                        _verify_invalidated(attempt, before=cutoff, receipt_id=receipt_id)
            handovers = [item for item in attempts if item.status == 'ACCEPTED']
            if handovers:
                # The initial payout must reflect the accepted responsibility,
                # including its actual funding release, before A was paid.
                if any(item.status != 'ACCEPTED' or not item.accepted_at
                       or item.accepted_at >= person.settled_at for item in handovers):
                    raise ValidationError('首退前接替和加购退款的时序待核对')
                for handover in handovers:
                    run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
                    if (not run or not isinstance(run.points_handover_snapshot, dict)
                            or run.points_handover_snapshot.get('kind') != 'SAME_TEAM_V1'
                            or run.created_at >= person.settled_at):
                        raise ValidationError('首退前接替的资金版本缺失')
                    verify_handover(payment, handover, as_of=person.settled_at, receipt_id=receipt_id)
            _, shares = current_responsibilities(payment, before=person.settled_at)
        except RestValidationError as exc:
            raise ValidationError('首退前加购付款凭证异常') from exc
        # Cross-payment and interleaved handovers are ordered above as one
        # ledger; this loop separately verifies each refund on its payment.
        prior_run_at = None
        for refund in payment.refund_records.filter(created_at__lt=person.settled_at).order_by('created_at', 'pk'):
            run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if (not run or run.created_at < refund.created_at or run.created_at >= person.settled_at
                    or (prior_run_at and refund.created_at <= prior_run_at)):
                raise ValidationError('首退前加购退款释放版本缺失')
            try:
                verify_run(run, _skip_usage=True, witness_receipt_id=receipt_id)
            except (RestValidationError, ValidationError) as exc:
                raise ValidationError('首退前加购退款释放凭证异常') from exc
            prior_run_at = run.created_at
        refunded = sum(payment.refund_records.filter(created_at__lt=person.settled_at).values_list('amount', flat=True))
        if refunded == payment.intent.amount:
            # The actual original lots must all be returned, and no residual
            # provider income can disappear merely because its basis is zero.
            if any(row.refunded_amount != row.amount for row in payment.allocations.all()) or shares:
                raise ValidationError('全额退款加购的原路付款或剩余份额待核对')
            deductions = payment.refund_earning_deductions.filter(refund__created_at__lt=person.settled_at)
            if handovers:
                income = sum(handovers[-1].shares.values_list('income', flat=True))
                deductions = deductions.filter(refund__created_at__gt=handovers[-1].accepted_at)
            else:
                income = payment.provider_income
            if income != sum(deductions.values_list('amount', flat=True)):
                raise ValidationError('全额退款加购仍有未结收益，请先核对')
        if handovers:
            refunds = list(payment.refund_records.order_by('created_at', 'pk'))
            if refunds:
                if (any(item.created_at >= person.settled_at or item.amount <= 0 for item in refunds)
                        or sum(item.amount for item in refunds) > payment.intent.amount):
                    raise ValidationError('首退前接替后的退款时序待核对')
                from .points_addon_handover_refund_facts import settlement_parts
                verified = settlement_parts(refunds[0], before=person.settled_at, receipt_id=receipt_id)
                actual = defaultdict(lambda: [0, 0])
                for row in shares:
                    amount = actual[(row['segment_id'], row['provider_id'])]
                    amount[0] += row['basis']; amount[1] += row['income']
                expected_shares = {(row['segment_id'], row['provider_account_id']):
                    (row['basis'], row['provider_income_units']) for row in verified}
                if {key: tuple(value) for key, value in actual.items()} != expected_shares:
                    raise ValidationError('首退前接替退款的逐人收益不一致')
        own = [row for row in shares if row['segment_id'] == person.segment_id]
        if own:
            if any(row['provider_id'] != person.segment.provider_account_id for row in own):
                raise ValidationError('首退前已付人的加购份额异常')
            expected[payment.pk] = sum(row['income'] for row in own)
    rows = list(settlement.addon_settlements.select_related(
        'payment__intent', 'segment', 'report', 'income_transaction__wallet').order_by('pk'))
    if len(rows) != len(expected) or {row.payment_id for row in rows} != set(expected):
        raise ValidationError('首退前已付人的加购结算记录不完整')
    for row in rows:
        amount, tx = expected[row.payment_id], row.income_transaction
        if (row.segment_id != person.segment_id or row.report_id != person.report_id
                or row.settled_at != person.settled_at or row.amount != amount
                or (not row.amount and tx)
                or (row.amount and (not tx or tx.order_id != order.pk
                    or tx.wallet.account_id != person.segment.provider_account_id
                    or tx.tx_type != 'INCOME' or tx.status != 'SUCCESS' or tx.amount != row.amount
                    or tx.balance_after != tx.balance_before + tx.amount or tx.created_at >= boundary
                    or tx.reversal_of_id or tx.reversals.exists()))):
            raise ValidationError('首退前已付人的加购收益流水异常')
    return payments, rows


def verify_current(posting, release, *, receipt_id=None):
    from .models import (OrderAddonPayment, OrderProvider, OrderPointsSettlement,
                         OrderOriginalShareTransfer,
                         PointsFirstRefundPaidPersonEvidence)
    from .points_service_participation import service_snapshots
    from .points_settlement_test import _verify_person
    from .settlement import SettlementError

    order = posting.proposal.case.order
    evidence = PointsFirstRefundPaidPersonEvidence.objects.filter(posting=posting).select_related(
        'person__settlement', 'person__segment__attribution', 'person__report',
        'person__income_transaction__wallet').first()
    if not evidence:
        raise ValidationError('首退前已付人员资金证据缺失')
    person = evidence.person
    source = OrderPointsSettlement.objects.filter(order=order, pk=person.settlement_id).first()
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        raise ValidationError('首退前已付人员的服务中转单范围未接通')
    snapshots = service_snapshots(order)
    snapshot = next((item for item in snapshots if item.segment_id == person.segment_id), None)
    assignment = OrderProvider.objects.filter(order=order, provider_account_id=person.segment.provider_account_id).first()
    payload = [posting.pk, person.pk, person.amount, person.income_transaction_id, evidence.allocation_snapshot]
    digest = hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
    if (not source or source.pk != release.original_settlement_id
            or source.redemption_id != posting.points_refund.redemption_id
            or source.finalized_at or source.inviter_transaction_id or source.store_transaction_id
            or source.persons.count() != 1 or not snapshot or not assignment
            or order.required_provider_count != 2 or not person.settled_at or person.settled_at >= posting.created_at
            or posting.shares.filter(account_id=person.segment.provider_account_id).exists()
            or person.segment.provider_account_id in (posting.proposal.case.affected_provider_account_ids or [])
            or evidence.evidence_hash != digest
            or list(source.funding_uses.order_by('allocation_id').values_list('allocation_id', 'amount'))
               != [(item[0], item[1]) for item in evidence.allocation_snapshot if item[1]]):
        raise ValidationError('首退前已付人员或原资金占用不一致')
    from .points_funding_release import _net_allocations
    if evidence.allocation_snapshot != _net_allocations(
            release.allocations, release.prior_addon_release_snapshot)[0]:
        raise ValidationError('首退前原净到账分配不一致')
    payments, _ = verify_original_addons(order, source, person, posting.created_at, receipt_id=receipt_id)
    history = release.addon_funding_snapshot
    if ((payments and (not isinstance(history, list) or
            {row.get('payment_id') for row in history if isinstance(row, dict)} != {item.pk for item in payments}
            or len(history) != len(payments))) or (not payments and history is not None)):
        raise ValidationError('首退前加购资金版本不一致')
    try:
        from .addon_main_deduction import deductions_at
        deduction = deductions_at(order, before=person.settled_at).get(person.segment.provider_account_id, 0)
        _verify_person(order, person, snapshot, assignment, deduction=deduction)
    except SettlementError as exc:
        raise ValidationError('首退前已付人员的报单或收入流水异常') from exc
    return person
