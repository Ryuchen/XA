"""Rebuild a non-transferred order after its second main refund and an add-on event."""
import hashlib
import json
from dataclasses import asdict
from contextlib import contextmanager
from contextvars import ContextVar

from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

_verified = ContextVar('points_followup_addon_preflight', default=None)


@contextmanager
def preflight_locked(order):
    """Verify the previous version before a new event mutates its facts."""
    from stores.db import transaction
    from .models import (OrderPointsFollowupFundingRevision, OrderPointsRefundSettlement,
                         OrderPointsRefundSettlementPerson, OrderAddonHandover)
    from .points_followup_funding import verify_followup_funding
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('后续加购预核须在订单事务内执行')
    from .points_business_access import _bind
    with _bind(phase='refund', kind='addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        funding = verify_followup_funding(order.pk)
    revision = OrderPointsFollowupFundingRevision.objects.select_for_update().filter(
        posting__points_refund__redemption__order=order).first()
    if (not revision or funding.allocated > funding.required or order.settled_at
            or OrderPointsRefundSettlement.objects.filter(posting_id=revision.posting_id).exists()):
        raise ValidationError('后续退款资金版本待核对')
    people = list(OrderPointsRefundSettlementPerson.objects.filter(
        settlement__posting__points_refund__redemption__order=order).values_list('pk', flat=True))
    expected = [revision.paid_person_id] if revision.paid_person_id else []
    if people != expected:
        raise ValidationError('已付陪玩或加购接替资金版本待核对')
    if revision.paid_person_id:
        from .models import OrderAddonFundingReleaseRun
        handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order))
        if any(item.status not in ('ACCEPTED', 'REJECTED', 'SUPERSEDED')
               or not item.created_at or item.created_at >= revision.paid_person.settled_at
               for item in handovers):
            raise ValidationError('已付前的加购接替资金版本待核对')
        from .points_team_addon_handover_facts import verify_rejected_attempts
        for payment_id in {item.payment_id for item in handovers}:
            payment = next(item.payment for item in handovers if item.payment_id == payment_id)
            verify_rejected_attempts(payment, before=revision.paid_person.settled_at)
        from django.db.models import Q
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .models import PointsHandoverRefundInvalidation, OrderPointsFundingAllocation
        from .points_handover_refund_invalidation import _verify_facts as verify_invalidation
        witness = OrderPointsFundingAllocation.objects.filter(
            Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
        for item in (row for row in handovers if row.status == 'SUPERSEDED'):
            record = PointsHandoverRefundInvalidation.objects.filter(handover=item).select_related(
                'handover__payment__intent', 'refund').first()
            release_run = (OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
                           if record else None)
            if (not record or record.created_at >= revision.paid_person.settled_at
                    or record.refund.created_at >= revision.paid_person.settled_at
                    or not release_run or release_run.created_at >= revision.paid_person.settled_at):
                raise ValidationError('已付款前接替失效的真实退款凭证缺失')
            try:
                verify_invalidation(record, receipt_id=witness.receipt_id if witness else None)
            except DjangoValidationError as exc:
                raise ValidationError('已付款前接替失效的退款及释放待核对') from exc
        if any(not item.accepted_at
                or item.accepted_at >= revision.paid_person.settled_at
                or not OrderAddonFundingReleaseRun.objects.filter(handover=item,
                    points_handover_snapshot__kind='SAME_TEAM_V1',
                    created_at__lt=revision.paid_person.settled_at).exists()
               for item in handovers if item.status == 'ACCEPTED'):
            raise ValidationError('已付前的加购接替资金版本待核对')
    token = _verified.set((transaction.get_connection().alias, order.pk, revision.pk, revision.evidence_hash))
    try:
        yield revision
    finally:
        _verified.reset(token)


def verified_revision(order):
    from stores.db import transaction
    from .models import OrderPointsFollowupFundingRevision
    state = _verified.get()
    if not state or state[:2] != (transaction.get_connection().alias, order.pk):
        raise ValidationError('后续退款后的加购缺少锁内预核')
    revision = OrderPointsFollowupFundingRevision.objects.filter(pk=state[2], evidence_hash=state[3]).first()
    if not revision:
        raise ValidationError('后续退款原资金版本已变化')
    return revision


def later_runs(order, revision):
    """Verify a complete ordered chain of add-on events after refund two."""
    from django.db.models import Q
    from .models import OrderAddonFundingReleaseRun, OrderAddonPayment, OrderAddonRefund
    from .addon_funding_release import verify_run
    boundary = revision.posting.created_at
    payments = list(OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__gte=boundary)
                    .select_related('buyer_transaction'))
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gte=boundary))
    events = [(item.buyer_transaction.created_at, 'payment', item.pk) for item in payments]
    events += [(item.created_at, 'refund', item.pk) for item in refunds]
    if any(at <= boundary for at, _, _ in events) or len({at for at, _, _ in events}) != len(events):
        raise ValidationError('后续退款与加购事件时序无法区分')
    runs = list(OrderAddonFundingReleaseRun.objects.filter(
        Q(payment_id__in=[item.pk for item in payments]) | Q(refund_id__in=[item.pk for item in refunds]))
        .select_related('payment__buyer_transaction', 'refund').order_by('pk'))
    by_event = {(('payment', row.payment_id) if row.payment_id else ('refund', row.refund_id)): row for row in runs}
    if len(by_event) != len(events) or set(by_event) != {(kind, pk) for _, kind, pk in events}:
        raise ValidationError('后续退款后的加购资金版本缺失')
    ordered = []
    for at, kind, pk in sorted(events):
        run = by_event[(kind, pk)]
        if (not isinstance(run.points_followup_snapshot, dict)
                or run.points_followup_snapshot.get('followup_revision_id') != revision.pk or run.created_at < at
                or (ordered and ordered[-1].pk >= run.pk)):
            raise ValidationError('后续加购资金版本顺序异常')
        verify_run(run)
        ordered.append(run)
    return ordered


class EffectiveRevision:
    """Expose the latest verified funding amounts while retaining its origin."""
    def __init__(self, revision, snapshot):
        self.revision, self.snapshot = revision, snapshot

    def __getattr__(self, name):
        values = self.snapshot['funding']
        mapping = {'net_paid_units': 'net_coin_units', 'principal_units': 'net_principal_units',
                   'gift_units': 'net_gift_units', 'compensation_units': 'net_compensation_units',
                   'recovered_units': 'planned_provider_external_units', 'provider_units': 'provider_income_units',
                   'inviter_units': 'inviter_income_units', 'store_units': 'store_income_units',
                   'required_units': 'required_units'}
        if name == 'addon_snapshot':
            return {'addon_history': self.snapshot['addon_history'], 'main_deductions': self.snapshot['main_deductions']}
        if name == 'remaining_provider_units' and 'paid_provider_units' in self.snapshot:
            return values['provider_income_units'] - self.snapshot['paid_provider_units']
        if name in mapping:
            return values[mapping[name]]
        return getattr(self.revision, name)


def capture(order, revision, net_allocations, at):
    from .models import OrderOriginalShareTransfer, PointsNegotiationPosting
    from .points_service_participation import service_snapshots
    from .points_negotiation_preview import funding_after_refund
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        raise ValidationError('转单订单不能使用非转单加购版本')
    second = revision.posting
    first = PointsNegotiationPosting.objects.filter(points_refund_id=second.proposal.points_terms.prior_refund_id).first()
    proofs = [first.points_refund, second.points_refund] if first else []
    if (not first or first.points_refund.redemption_id != second.points_refund.redemption_id
            or second.points_refund.redemption.order_id != order.pk or order.refund_records.count() != 2
            or set(order.refund_records.values_list('pk', flat=True)) != {item.refund_id for item in proofs}
            or any(item.refund.amount != item.coin_units for item in proofs)):
        raise ValidationError('后续退款的原支付与退款记录不一致')
    snapshots = service_snapshots(order)
    shares = [*first.shares.all(), *second.shares.all()]
    if len(snapshots) != 2 or len(shares) != 2 or len({item.account_id for item in shares}) != 2:
        raise ValidationError('后续退款的陪玩收益范围待核对')
    sources = list(order.funding_allocations.all())
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    returned = {kind: sum(item.refunded_amount for item in sources if item.kind == kind) for kind in paid}
    expected = {'PREPAID': sum(item.prepaid_units for item in proofs),
                'GIFT': sum(item.gift_units for item in proofs),
                'COMPENSATION': sum(item.compensation_units for item in proofs)}
    if sum(paid.values()) != order.amount or returned != expected:
        raise ValidationError('后续退款的店币来源不一致')
    main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
        compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
        returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
        provider_income_units=sum(item.income_at_entry for item in snapshots),
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=sum(item[1] for item in net_allocations),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    pending = {item.account_id: item.pending_deduction for item in shares}
    retained = {item.segment.provider_account_id: item.income_at_entry - pending.get(item.segment.provider_account_id, 0)
                for item in snapshots}
    segment_shares = [(item.segment_id, item.segment.provider_account_id, item.incoming_basis, 0,
                       retained[item.segment.provider_account_id]) for item in snapshots]
    history = combined_at(order, main, at, segment_shares=segment_shares)
    if not history.get('addon_history'):
        raise ValidationError('后续退款后的加购事件缺失')
    plan = combine_refunded_history(main, history, main_retained_by_account=retained)
    result = {'first_posting_id': first.pk, 'second_posting_id': second.pk,
              'followup_revision_id': revision.pk, 'followup_revision_hash': revision.evidence_hash,
              'captured_at': at.isoformat(), 'addon_history': history['addon_history'],
              'main_deductions': history['main_deductions'], 'funding': asdict(plan)}
    if revision.paid_person_id:
        from .models import OrderAddonPayment, OrderAddonRefundParticipant
        paid_person = revision.paid_person
        paid_account = paid_person.segment.provider_account_id
        if (not revision.prior_settlement_id or paid_person.settlement_id != revision.prior_settlement_id
                or not paid_person.settled_at or paid_person.settled_at >= at
                or OrderAddonRefundParticipant.objects.filter(proposal__payment__intent__order=order,
                    proposal__refund__created_at__gt=paid_person.settled_at,
                    proposal__refund__created_at__lt=at, account_id=paid_account,
                    main_pending_deduction__gt=0).exists()):
            raise ValidationError('后续加购改变已付陪玩主单收益')
        payments = {item.pk: item for item in OrderAddonPayment.objects.filter(
            intent__order=order, pk__in=[item['payment_id'] for item in history['addon_history']])
            .select_related('intent', 'buyer_transaction')}
        from .addon_handover import current_responsibilities
        paid_history = {}
        for payment in payments.values():
            _, responsibilities = current_responsibilities(payment, before=at)
            own = [item for item in responsibilities if item['provider_id'] == paid_account]
            if own:
                if any(item['segment_id'] != paid_person.segment_id for item in own):
                    raise ValidationError('已付陪玩加购履约段异常')
                paid_history[payment.pk] = sum(item['income'] for item in own)
        paid_rows = list(revision.prior_settlement.addon_settlements.filter(segment_id=paid_person.segment_id)
                         .select_related('income_transaction__wallet'))
        for row in paid_rows:
            if row.payment_id in payments:
                paid_history.setdefault(row.payment_id, 0)
        if (len({item.payment_id for item in paid_rows}) != len(paid_rows)
                or {item.payment_id: item.amount for item in paid_rows} != paid_history
                or any(item.settled_at != paid_person.settled_at
                    or (item.amount and (not item.income_transaction_id
                        or item.income_transaction.order_id != order.pk
                        or item.income_transaction.wallet.account_id != paid_account
                        or item.income_transaction.amount != item.amount
                        or item.income_transaction.tx_type != 'INCOME'
                        or item.income_transaction.status != 'SUCCESS'
                        or item.income_transaction.balance_after !=
                            item.income_transaction.balance_before + item.income_transaction.amount
                        or item.income_transaction.reversal_of_id or item.income_transaction.reversals.exists()))
                    or (not item.amount and item.income_transaction_id) for item in paid_rows)
                or any(payments[item.payment_id].buyer_transaction.created_at >= paid_person.settled_at
                       for item in paid_rows)):
            raise ValidationError('后续加购改变已付陪玩加购收益')
        paid_units = paid_person.amount + sum(item.amount for item in paid_rows)
        if paid_units != revision.paid_provider_units or paid_units > plan.provider_income_units:
            raise ValidationError('后续加购已付收益总额异常')
        result['paid_provider_units'] = paid_units
    from .models import OrderAddonFundingReleaseRun, OrderAddonRefund
    team_runs = list(OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
        points_handover_snapshot__kind='SAME_TEAM_V1', created_at__lt=second.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gt=second.created_at,
        created_at__lt=at).select_related('payment', 'proposal', 'order').order_by('created_at', 'pk'))
    from .points_team_other_addon_refund import applies as other_applies, facts as other_facts
    if team_runs or any(other_applies(item) for item in refunds):
        from .points_team_addon_handover_facts import has_ordered_funding_chain
        if not has_ordered_funding_chain(team_runs):
            raise ValidationError('后续加购的同单接替版本重复')
        team_payment_ids = {item.handover.payment_id for item in team_runs}
        from .points_addon_handover_refund_facts import post_followup_facts
        from .points_addon_receipt_usage import digest as fact_digest
        from django.core.exceptions import ValidationError as DjangoValidationError
        if len({item.created_at for item in refunds}) != len(refunds):
            raise ValidationError('后续退款的加购事件范围待核对')
        witness = net_allocations[0][2] if net_allocations else None
        try:
            result['team_refund_facts'] = [[item.pk, fact_digest(
                post_followup_facts(item, receipt_id=witness)
                if item.payment_id in team_payment_ids else
                other_facts(item, receipt_id=witness) if other_applies(item) else
                _unsupported_team_refund())] for item in refunds]
        except DjangoValidationError as exc:
            raise ValidationError('后续退款的加购个人资金事实待核对') from exc
    return result


def _unsupported_team_refund():
    raise ValidationError('其他加购退款资金范围尚未接入')


def digest(run, snapshot):
    value = [run.refund_id, run.payment_id, run.required_amount, run.allocated_before,
             run.released_amount, run.allocation_snapshot, snapshot]
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def verify(run, net_allocations):
    from .models import OrderPointsFollowupFundingRevision
    snapshot = run.points_followup_snapshot
    if not isinstance(snapshot, dict) or run.points_followup_hash != digest(run, snapshot):
        raise ValidationError('后续退款的加购资金版本异常')
    revision = OrderPointsFollowupFundingRevision.objects.filter(pk=snapshot.get('followup_revision_id')).select_related(
        'posting__proposal__case__order', 'posting__points_refund__redemption').first()
    at = parse_datetime(snapshot.get('captured_at', ''))
    event_at = run.refund.created_at if run.refund_id else run.payment.buyer_transaction.created_at if run.payment_id else None
    if (not revision or not at or not event_at or at < event_at or at > run.created_at
            or revision.evidence_hash != snapshot.get('followup_revision_hash')
            or capture(revision.posting.proposal.case.order, revision, net_allocations, at) != snapshot
            or snapshot['funding']['required_units'] != run.required_amount):
        raise ValidationError('后续退款的加购资金事实不一致')
    return snapshot


def record_locked(order, *, refund=None, payment=None):
    """Record one event's rebalance inside its original locked transaction."""
    from django.utils import timezone
    from .models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderPointsFundingRelease
    from .points_funding_allocation import plan_release
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_receipt_usage import followup_eligible
    from .addon_funding_release import addon_release_totals
    from stores.db import transaction
    if not transaction.get_connection().in_atomic_block or bool(refund) == bool(payment):
        raise ValidationError('后续加购资金事件不完整')
    revision = verified_revision(order)
    from .points_business_access import _bind
    with _bind(phase='refund', kind='addon', order_id=order.pk,
               account_id=order.customer_account_id, service_id=order.service_id):
        allocations = verified_order_allocations_locked(order, _main_refund_usage=followup_eligible(order))
    prior_addon = addon_release_totals(allocations)
    snapshot = []
    for allocation in allocations:
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation).order_by('pk'))
        if any(item.posting.proposal.case.order_id != order.pk for item in main):
            raise ValidationError('后续加购主单释放不属于本单')
        snapshot.append([allocation.pk, allocation.amount, prior_addon[allocation.pk], [],
                         [[item.pk, item.amount] for item in main]])
    normalized = [[item[0], item[1], item[2] + sum(entry[1] for entry in item[4])] for item in snapshot]
    net = [[item[0], item[1] - item[2], allocation.receipt_id]
           for item, allocation in zip(normalized, allocations)]
    before = sum(item[1] for item in net)
    facts = capture(order, revision, net, timezone.now())
    required = facts['funding']['required_units']
    released = max(before - required, 0)
    parts = plan_release(allocations=normalized, amount=released)
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, payment=payment,
        required_amount=required, allocated_before=before, released_amount=released,
        allocation_snapshot=snapshot, points_followup_snapshot=facts)
    run.points_followup_hash = digest(run, facts)
    run.save(update_fields=['points_followup_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    from .addon_funding_release import verify_run
    verify_run(run, _skip_usage=True)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    for row in run.releases.select_related('allocation__receipt').order_by('allocation__receipt_id', 'pk'):
        if row.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(row)
    for receipt_id in sorted({item.receipt_id for item in allocations if item.receipt.usage_version is not None}):
        verify_allocations_locked(receipt_id)
    return run


def receipt_facts(row, *, witness_receipt_id=None):
    """Prove this release from committed tenant facts without locking an order."""
    from types import SimpleNamespace
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads, first_negotiation_release_facts
    from .models import OrderPointsFundingAllocation
    from .addon_funding_release import verify_run
    from .points_followup_receipt_usage import facts as followup_facts
    _require_current_unlocked_reads()
    run, allocation = row.run, row.allocation
    order_id = run.refund.order_id if run.refund_id else run.payment.intent.order_id if run.payment_id else None
    from .models import OrderAddonFundingReleaseRun
    from .points_team_other_addon_refund import applies as other_team_refund_applies
    team = bool(run.refund_id and (OrderAddonFundingReleaseRun.objects.filter(
        handover__payment_id=run.refund.payment_id,
        points_handover_snapshot__kind='SAME_TEAM_V1').exists() or other_team_refund_applies(run.refund)))
    invalid_owner = (allocation.funding_order_id != order_id if team else
                     allocation.redemption_id is None or allocation.redemption.order_id != order_id)
    if (not order_id or invalid_owner or not isinstance(run.points_followup_snapshot, dict)):
        raise DjangoValidationError('后续退款加购到账归属异常')
    try:
        verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        from .models import OrderPointsFollowupFundingRevision, PointsNegotiationPosting
        revision = OrderPointsFollowupFundingRevision.objects.select_related('posting').get(
            pk=run.points_followup_snapshot['followup_revision_id'])
        original_ids = [item[0] for item in revision.allocations]
        original = OrderPointsFundingAllocation.objects.filter(pk__in=original_ids).order_by('pk').first()
        if not original_ids and allocation.created_at > revision.created_at:
            original = allocation
        if not original:
            raise DjangoValidationError('后续退款的原到账分配缺失')
        second = revision.posting
        first = PointsNegotiationPosting.objects.filter(
            points_refund_id=second.proposal.points_terms.prior_refund_id).first()
        if not first:
            raise DjangoValidationError('首次退款来源缺失')
        first_negotiation_release_facts(SimpleNamespace(posting=first, allocation=original, pk=0, amount=0))
        followup_facts(SimpleNamespace(posting=second, allocation=original, pk=0, amount=0))
    except (ValidationError, KeyError, AttributeError, OrderPointsFollowupFundingRevision.DoesNotExist) as exc:
        raise DjangoValidationError('后续退款加购到账来源待核对') from exc
    return ('RELEASE', f'order:{allocation.pk}', f'points-followup-addon-release:{row.pk}', row.amount,
            _digest(['points-followup-addon-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     run.pk, run.payment_id, run.refund_id, run.points_followup_hash,
                     run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
