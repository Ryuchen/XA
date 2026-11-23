"""Isolated first add-on refund facts for a receipt-local usage ledger.

The rebuilding function performs current reads only: callers must hold the
receipt lock, never acquire another order's locks from this module.
"""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict

from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from stores.context import current_store
from stores.db import transaction
from .models import (OrderAddonPayment, OrderOriginalShareTransfer, OrderPointsFundingAllocation,
                     OrderPrestartHandoff, OrderSegmentFinancialSnapshot)
from .points_business_access import _scope as _points_scope
from .points_checkout_test import require_isolated_checkout


_history_reads = ContextVar('points_addon_history_reads', default=None)


@contextmanager
def reuse_history_locked(order, receipt_ids):
    """Share historical facts while reading one locked order and its locked receipts."""
    connection = transaction.get_connection()
    if not connection.in_atomic_block or order._state.db != connection.alias:
        raise RuntimeError('加购历史复用须在本单事务内执行')
    state = (connection, tuple(connection.atomic_blocks), current_store(), _points_scope.get(),
             order.pk, frozenset(receipt_ids), {})
    token = _history_reads.set(state)
    try:
        yield
    finally:
        _history_reads.reset(token)


def _read_history(rebuild, record, *, read_receipt_id, _event_history):
    state = _history_reads.get()
    if state is None:
        return rebuild(record, read_receipt_id=read_receipt_id, _event_history=_event_history)
    connection, blocks, store, capability, locked_order_id, receipts, results = state
    if (read_receipt_id not in receipts or record._state.db != connection.alias
            or transaction.get_connection() is not connection or tuple(connection.atomic_blocks) != blocks
            or current_store() is not store or _points_scope.get() != capability):
        return rebuild(record, read_receipt_id=read_receipt_id, _event_history=_event_history)
    order_id = record.order_id if rebuild is _refund_facts else record.intent.order_id
    if order_id != locked_order_id:
        return rebuild(record, read_receipt_id=read_receipt_id, _event_history=_event_history)
    key = (rebuild, record.pk, read_receipt_id, _event_history)
    if key in results:
        return deepcopy(results[key])
    result = rebuild(record, read_receipt_id=read_receipt_id, _event_history=_event_history)
    results[key] = deepcopy(result)
    return result


def _prestart_snapshot_at(order, at):
    """Resolve the occupied seats at this event, without locking other orders."""
    if not at or OrderPrestartHandoff.objects.filter(order=order, created_at=at).exists():
        raise ValidationError('改派与加购事件时序无法区分')
    event = OrderPrestartHandoff.objects.filter(order=order, created_at__lt=at).order_by('-pk').first()
    if not event:
        return None
    from .points_prestart_release import capture
    return capture(order, event)


def _prestart_segments_at(order, at):
    snapshot = _prestart_snapshot_at(order, at)
    return snapshot['segments'] if snapshot is not None else None


def _prior_addon_events(order, at, *, read_receipt_id=None):
    """Bind each earlier payment and refund to its own frozen source facts."""
    from .models import OrderAddonRefund
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=at).select_related('buyer_transaction').order_by('pk'))
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=at).order_by('pk'))
    if (OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=at).count()
            + OrderAddonRefund.objects.filter(order=order, created_at=at).count() > 1):
        raise ValidationError('加购资金事件时间无法区分')
    events = [(item.buyer_transaction.created_at, 'payment', item.pk, item) for item in payments]
    events += [(item.created_at, 'refund', item.pk, item) for item in refunds]
    if any(not item[0] for item in events) or len({item[0] for item in events}) != len(events):
        raise ValidationError('加购资金事件顺序无法区分')
    result = []
    for created, kind, pk, item in sorted(events, key=lambda value: value[0]):
        if kind == 'payment':
            from .models import OrderAddonFundingReleaseRun, OrderPointsTransferFollowupFundingRevision
            run = OrderAddonFundingReleaseRun.objects.filter(payment=item,
                points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
            if run:
                snapshot = run.points_followup_snapshot
                revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
                    pk=snapshot.get('followup_revision_id'), posting__points_refund__redemption__order=order).first()
                if not revision or snapshot.get('addon_payment_id') != item.pk:
                    raise ValidationError('此前加购付款的资金版本异常')
                from .points_transfer_followup_addon_version import _verify_prior_run
                from .points_transfer_followup_addon_payment import facts as followup_payment_facts
                _verify_prior_run(run, receipt_id=read_receipt_id)
                source = followup_payment_facts(item, revision, receipt_id=read_receipt_id)
                if source != snapshot.get('addon_payment_facts'):
                    raise ValidationError('此前加购付款与资金版本不一致')
            else:
                source = payment_facts(item, read_receipt_id=read_receipt_id, _event_history=False)
        else:
            source = facts(item, read_receipt_id=read_receipt_id, _event_history=False)
        result.append([kind, pk, created.isoformat(), source])
    return result


def eligible(order):
    from .points_business_access import permits
    formal = permits(order, phase='refund', kind='addon')
    if not formal and not getattr(settings, 'SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING', False):
        return False
    if not formal:
        require_isolated_checkout()
    from .points_service_payment import needs_integrated_processing
    payments = OrderAddonPayment.objects.filter(intent__order=order)
    payment_rows = list(payments.select_related('buyer_transaction'))
    prestart_after_payment = any(not payment.buyer_transaction.created_at
        or OrderPrestartHandoff.objects.filter(order=order,
            created_at__gte=payment.buyer_transaction.created_at).exists() for payment in payment_rows)
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    from .points_transfer_refund_access import permits as transfer_permits
    from .points_transfer_refund_access import (independent_refund_after_handovers,
        first_handover_refund, single_post_refund_handover, post_refund_handover_history,
        _followup_transfer_handover_scope)
    from .models import PointsNegotiationPosting, OrderAddonFundingReleaseRun
    second = (PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order)
              .order_by('-points_refund__refund_id').first() if order.refund_records.count() == 2 else None)
    postsecond_payment = bool(second and any(payment.buyer_transaction.created_at > second.created_at
        and OrderAddonFundingReleaseRun.objects.filter(payment=payment,
            points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists() for payment in payment_rows))
    from .points_transfer_followup_addon_version import refund_preflight_active
    from .models import OrderAddonHandover
    pending_ids = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                       .values_list('payment_id', flat=True))
    pinned_refund = any(refund_preflight_active(order, payment) for payment in payment_rows)
    pending_scope = frozenset(pending_ids) if pinned_refund and pending_ids else None
    post_transfer_history = bool(formal and transferred and transfer_permits(order, phase='refund')
        and _followup_transfer_handover_scope(order,
            pending_payment_id=pending_scope))
    transfer_followup = bool(formal and transferred and order.refund_records.count() == 2
                             and transfer_permits(order, phase='refund')
                             and (post_refund_handover_history(order, pending_payment_id=pending_scope)
                                  or postsecond_payment or post_transfer_history))
    after_transfer_refund = (formal and transferred and transfer_permits(order, phase='refund')
                             and (order.refund_records.count() == 1 or transfer_followup))
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('pk'))
    supported_handover = formal and (independent_refund_after_handovers(order, transfers)
                                     or first_handover_refund(order, transfers)
                                     or single_post_refund_handover(order, pending_payment_id=pending_scope)
                                     or post_refund_handover_history(order, pending_payment_id=pending_scope)
                                     or post_transfer_history)
    if formal and transfer_followup and postsecond_payment:
        supported_handover = supported_handover or OrderAddonFundingReleaseRun.objects.filter(
            handover__payment__intent__order=order, handover__status='ACCEPTED',
            handover__accepted_at__gt=second.created_at,
            points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
            points_followup_snapshot__second_posting_id=second.pk).exists()
        if not supported_handover:
            from .points_transfer_followup_addon_version import refund_preflight_active
            from .points_followup_handover_invalidation import may_refund_pending_target, permits_locked
            supported_handover = any(refund_preflight_active(order, payment)
                and may_refund_pending_target(order, payment) for payment in payment_rows)
            if not supported_handover:
                targets = [payment for payment in payment_rows if refund_preflight_active(order, payment)]
                from .models import OrderAddonHandover
                pending = OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')\
                    .select_related('payment__intent', 'source_segment', 'target_segment')
                supported_handover = any(permits_locked(order, handover, target)
                    for target in targets for handover in pending if handover.payment_id != target.pk)
    if formal and transferred and order.refund_records.count() == 1:
        from .points_voluntary_handover_event import entry_matches
        from .points_transfer_addon_liability import first_refund_active
        supported_handover = supported_handover or entry_matches(order) or first_refund_active(order)
    if formal and transferred and not order.refund_records.exists() and not supported_handover:
        from .points_transfer_pending_handover_events import permits_other_refund
        supported_handover = any(permits_other_refund(order, transfers, payment.pk) for payment in payment_rows)
        if not supported_handover:
            from .points_handover_refund_invalidation import provisional_structure
            supported_handover = provisional_structure(order)
    team_followup = False
    paid_plain_followup = False
    if formal and not transferred and order.refund_records.count() <= 2:
        from .models import OrderAddonFundingReleaseRun, OrderPointsFollowupFundingRevision
        team_runs = OrderAddonFundingReleaseRun.objects.filter(handover__payment__intent__order=order,
            points_handover_snapshot__kind='SAME_TEAM_V1')
        supported_handover = bool(team_runs.exists())
        team_followup = supported_handover and order.refund_records.count() == 2
        if order.refund_records.count() == 2 and not payments.filter(handovers__isnull=False).exists():
            paid_plain_followup = OrderPointsFollowupFundingRevision.objects.filter(
                posting__points_refund__redemption__order=order, paid_person__isnull=False).exists()
    provider_count = order.providers.count()
    providers_valid = provider_count in (1, 2) or (formal and transferred
                                                   and 1 <= provider_count <= 3 * order.required_provider_count)
    if (not providers_valid and formal and not transferred and not prestart_after_payment
            and order.required_provider_count == 2 and OrderPrestartHandoff.objects.filter(order=order).exists()):
        from .points_service_participation import service_snapshots
        snapshots = service_snapshots(order)
        providers_valid = len(snapshots) == 2 and len({row.segment.provider_account_id for row in snapshots}) == 2
    return bool(needs_integrated_processing(order) and not order.is_gift_snapshot and not order.source_order_id
        and (not order.refund_records.exists() or (formal and order.refund_records.count() == 1)
             or team_followup or transfer_followup or paid_plain_followup)
        and providers_valid
        and bool(payment_rows)
        and (supported_handover or not payments.filter(handovers__isnull=False).exists())
        and (not transferred or (formal and (not order.refund_records.exists() or after_transfer_refund)))
        and not prestart_after_payment
        and (formal or order.addon_refunds.count() <= 1))


def prelock(order):
    """The caller holds order and money locks; take receipts before wallet facts."""
    from .points_business_access import permits
    formal = permits(order, phase='refund', kind='addon')
    if not formal and not getattr(settings, 'SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING', False):
        return False
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('加购到账预锁必须在门店事务中执行')
    from users.models import PassRefundRepaymentReceipt
    ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('加购退款到账不存在')
    from users.receipt_usage_guard import isolated_allocation_state_locked
    from django.core.exceptions import ValidationError as DjangoValidationError
    involved = False
    for receipt in receipts:
        if receipt.usage_version is not None:
            involved = True
            if not eligible(order):
                from .models import OrderAddonFundingReleaseRun
                if not OrderAddonFundingReleaseRun.objects.filter(refund__order=order,
                        points_addon_snapshot__isnull=False).exists():
                    raise ValidationError('该加购退款到账用途尚未接入')
            try:
                isolated_allocation_state_locked(receipt)
            except DjangoValidationError as exc:
                raise ValidationError('加购退款到账用途凭证异常') from exc
    return involved


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=True, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def facts(refund, *, read_receipt_id=None, _event_history=True):
    """Rebuild the narrow frozen facts without SELECT FOR UPDATE."""
    return _read_history(_refund_facts, refund, read_receipt_id=read_receipt_id, _event_history=_event_history)


def _refund_facts(refund, *, read_receipt_id, _event_history):
    from .addon_payment import verify_receipt
    from .points_funding_plan import plan_service_funding, PointsFundingPlanError
    from .settlement import ProviderShare
    order, payment, proposal = refund.order, refund.payment, refund.proposal
    if proposal.cross_pending_plan is not None:
        from .addon_refund_share_allocation import verify as verify_share_allocations
        from .addon_refund_cross_plan import verify_posted
        verify_share_allocations(refund)
        verify_posted(refund)
    formal = (getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True
              and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
              and transaction.get_connection().vendor == 'mysql')
    if not formal and not getattr(settings, 'SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING', False):
        raise ValidationError('加购退款到账用途尚未开放')
    if formal:
        from users.receipt_usage_sources import _require_current_unlocked_reads
        _require_current_unlocked_reads()
    else:
        require_isolated_checkout()
    # Scope is checked under the order lock at posting. Later unpaid intents
    # are not money events and must not invalidate an earlier frozen refund.
    if order.is_gift_snapshot or order.source_order_id:
        raise ValidationError('加购退款到账用途范围已变化')
    from .points_team_other_addon_refund import applies as team_other_applies, facts as team_other_facts
    if team_other_applies(refund):
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            return team_other_facts(refund, receipt_id=read_receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('同单接替前加购退款凭证异常') from exc
    if payment.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists():
        raise ValidationError('加购接替与退款时序无法区分')
    if payment.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).exists():
        if not formal:
            raise ValidationError('接替后退款须在正式事务中核验')
        from .points_addon_handover_refund_facts import funding_facts
        return funding_facts(refund, receipt_id=read_receipt_id)
    if (proposal.payment_id != payment.pk or payment.intent.order_id != order.pk or proposal.status != 'ACCEPTED'
            or proposal.amount != refund.amount
            or (not formal and (refund.main_pending_deduction or refund.wallet_deduction
                or refund.deposit_deduction or refund.addon_pending_deduction != refund.amount + refund.rounding_surplus))):
        raise ValidationError('加购退款资金承担尚未接入')
    verify_receipt(payment)
    participants = list(proposal.participants.order_by('pk'))
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    if (len(participants) != 1 or participants[0].account_id != payment.intent.provider_id
            or set((a, b, c) for a, b, c, _ in decisions) != {
                (payment.intent.buyer_id, 'BOSS', True), (participants[0].account_id, 'PROVIDER', True)}
            or not proposal.accepted_at or any(not decided for _, _, _, decided in decisions)
            or proposal.accepted_at != max(decided for _, _, _, decided in decisions)):
        raise ValidationError('加购退款双方确认凭证异常')
    # The target has never been handed over, but its provider may also have
    # pending income from a different payment handed over after refund two.
    # Validate that cross-payment burden before it enters the follow-up
    # funding version; the writer and settlement use these same deductions.
    cross = list(refund.earning_deductions.exclude(payment=payment).select_related(
        'payment__intent', 'segment__attribution', 'participant').order_by('pk'))
    from .models import OrderAddonFundingReleaseRun
    followup_cross = any(OrderAddonFundingReleaseRun.objects.filter(
        handover__payment_id=row.payment_id, handover__status='ACCEPTED',
        handover__accepted_at__lt=refund.created_at,
        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').exists() for row in cross)
    if followup_cross:
        from django.core.exceptions import ValidationError as DjangoValidationError
        person = participants[0]
        deductions = list(refund.earning_deductions.select_related(
            'payment__intent', 'segment__attribution', 'participant').order_by('pk'))
        earlier_own = list(payment.refund_records.filter(created_at__lt=refund.created_at)
                           .select_related('proposal').order_by('created_at', 'pk'))
        basis = payment.intent.amount - sum(item.amount for item in earlier_own)
        income = payment.provider_income - sum(payment.refund_earning_deductions.filter(
            refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
        rounding = 0
        for prior in earlier_own:
            for prior_person in prior.proposal.participants.all():
                for entry in prior_person.share_plan:
                    if (not isinstance(entry, dict) or entry.get('segment_id') != payment.intent.segment_id
                            or prior_person.account_id != person.account_id):
                        raise ValidationError('此前退款履约份额异常')
                    rounding += entry['burden'] - entry['refund_basis']
        burden = ((refund.amount + 9) // 10) * 10
        capacity = max(0, min(basis, basis - rounding))
        expected_entry = {'segment_id': payment.intent.segment_id, 'basis': basis, 'income': income,
                          'refund_basis': refund.amount, 'burden_capacity': capacity,
                          'burden': min(burden, capacity)}
        amounts = (person.addon_pending_deduction, person.main_pending_deduction,
                   person.wallet_deduction, person.deposit_deduction)
        if (sum(row.amount for row in deductions) != refund.addon_pending_deduction
                or person.addon_pending_deduction != refund.addon_pending_deduction
                or not 0 < refund.amount <= basis or income < 0
                or person.basis != basis or person.burden != burden
                or person.excess_burden != burden - expected_entry['burden']
                or person.share_plan != [expected_entry]
                or any(type(value) is not int or value < 0 for value in amounts)
                or sum(amounts) != burden or refund.rounding_surplus != burden - refund.amount
                or amounts != (refund.addon_pending_deduction, refund.main_pending_deduction,
                               refund.wallet_deduction, refund.deposit_deduction)
                or len({(row.payment_id, row.segment_id) for row in deductions}) != len(deductions)
                or any(row.amount <= 0 or row.participant_id != person.pk
                       or row.payment.intent.order_id != order.pk
                       or row.segment.attribution.order_id != order.pk
                       or row.segment.provider_account_id != person.account_id for row in deductions)):
            raise ValidationError('跨加购退款逐段承担凭证异常')
        for row in deductions:
            other = row.payment
            if row.payment_id == payment.pk:
                used = sum(other.refund_earning_deductions.filter(
                    refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                if (row.segment_id != payment.intent.segment_id or
                        row.amount > payment.provider_income - used):
                    raise ValidationError('退款付款自身的待结收益不足')
                continue
            if not other.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).exists():
                from .models import OrderAddonSettlement, OrderSegmentAddonSettlement
                if (not other.buyer_transaction.created_at or other.buyer_transaction.created_at >= refund.created_at
                        or other.intent.order_id != order.pk or other.intent.segment_id != row.segment_id
                        or other.intent.provider_id != person.account_id
                        or other.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()
                        or other.refund_earning_deductions.filter(refund__created_at=refund.created_at)
                            .exclude(refund=refund).exists()
                        or OrderAddonSettlement.objects.filter(payment=other, segment_id=row.segment_id,
                            settled_at__lte=refund.created_at).exists()
                        or OrderSegmentAddonSettlement.objects.filter(payment=other, segment_id=row.segment_id,
                            settled_at__lte=refund.created_at).exists()):
                    raise ValidationError('跨加购原付款或未结收益来源异常')
                verify_receipt(other)
                used = sum(other.refund_earning_deductions.filter(
                    refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
                if row.amount > other.provider_income - used:
                    raise ValidationError('跨加购原付款剩余收益不足')
                continue
            from .points_addon_handover_refund_facts import followup_cross_income
            try:
                available = followup_cross_income(refund, row, receipt_id=read_receipt_id)
            except DjangoValidationError as exc:
                raise ValidationError('跨加购接替收益凭证异常') from exc
            if row.amount > available:
                raise ValidationError('跨加购接替收益不足')
    elif cross:
        # For independent purchases, prove every source against the actual
        # earlier debit and the same provider's still-unsettled segment.
        # Other handover combinations retain their own existing verifier.
        from .models import OrderAddonSettlement, OrderSegmentAddonSettlement
        seen = set()
        for row in cross:
            other = row.payment
            if other.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).exists():
                continue
            key = (other.pk, row.segment_id)
            if (key in seen or row.amount <= 0 or row.participant_id != participants[0].pk
                    or other.intent.order_id != order.pk or not other.buyer_transaction.created_at
                    or other.buyer_transaction.created_at >= refund.created_at
                    or other.intent.segment_id != row.segment_id
                    or other.intent.provider_id != participants[0].account_id
                    or row.segment.attribution.order_id != order.pk
                    or row.segment.provider_account_id != participants[0].account_id
                    or other.handovers.filter(status='ACCEPTED', accepted_at=refund.created_at).exists()
                    or other.refund_earning_deductions.filter(refund__created_at=refund.created_at)
                        .exclude(refund=refund).exists()
                    or OrderAddonSettlement.objects.filter(payment=other, segment_id=row.segment_id,
                        settled_at__lte=refund.created_at).exists()
                    or OrderSegmentAddonSettlement.objects.filter(payment=other, segment_id=row.segment_id,
                        settled_at__lte=refund.created_at).exists()):
                raise ValidationError('跨加购退款收益来源或结算状态异常')
            seen.add(key)
            verify_receipt(other)
            prior = list(other.refund_earning_deductions.filter(
                refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
            if row.amount > other.provider_income - sum(prior):
                raise ValidationError('跨加购退款超过本人未结收益')
    allocations = list(payment.allocations.order_by('pk'))
    source_plan = proposal.source_plan
    if (not isinstance(source_plan, list) or any(not isinstance(x, dict)
            or type(x.get('allocation_id')) is not int or type(x.get('amount_raw')) is not int
            or x['amount_raw'] <= 0 for x in source_plan)
            or sum(x['amount_raw'] for x in source_plan) != refund.amount):
        raise ValidationError('加购退款来源凭证异常')
    returned = {x['allocation_id']: x['amount_raw'] for x in source_plan}
    all_refunds = list(payment.refund_records.select_related('proposal').order_by('created_at', 'pk'))
    if (refund not in all_refunds or any(item.pk != refund.pk and item.created_at == refund.created_at
                                         for item in all_refunds)):
        raise ValidationError('加购退款时间顺序无法确认')
    cumulative, all_returned = {}, {}
    earlier = []
    source_rows = {row.pk: row for row in allocations}
    for item in all_refunds:
        entries = item.proposal.source_plan
        if (not isinstance(entries, list) or any(not isinstance(entry, dict)
                or type(entry.get('allocation_id')) is not int
                or type(entry.get('amount_raw')) is not int or entry['amount_raw'] <= 0
                for entry in entries) or len({entry['allocation_id'] for entry in entries}) != len(entries)
                or sum(entry['amount_raw'] for entry in entries) != item.amount):
            raise ValidationError('加购逐笔退款来源异常')
        for entry in entries:
            key, amount = entry['allocation_id'], entry['amount_raw']
            if key not in source_rows or source_rows[key].kind != entry.get('kind'):
                raise ValidationError('加购逐笔退款来源类别异常')
            all_returned[key] = all_returned.get(key, 0) + amount
            if item.created_at <= refund.created_at:
                cumulative[key] = cumulative.get(key, 0) + amount
        if item.created_at < refund.created_at:
            earlier.append(item)
    if (len(returned) != len(source_plan) or any(row.refunded_amount != all_returned.get(row.pk, 0)
            or row.refunded_amount > row.amount for row in allocations)
            or set(all_returned) - {row.pk for row in allocations}
            or any(x.get('kind') != next((r.kind for r in allocations if r.pk == x.get('allocation_id')), None)
                   or type(x.get('amount_raw')) is not int or x['amount_raw'] <= 0 for x in source_plan)):
        raise ValidationError('加购退款来源与实际退回不一致')
    redemption = order.points_redemption
    original = redemption.payment
    spent = redemption.ledger
    from .points_service_payment import MARKER
    from wallet.models import Transaction
    from engagement.models import PointLedger
    if (original.order_id != order.pk or original.wallet.account_id != order.customer_account_id
            or original.tx_type != 'PAY' or original.status != 'SUCCESS' or original.amount != -redemption.paid_units
            or redemption.paid_units != order.amount or redemption.deal_units != redemption.paid_units + redemption.discount_units
            or redemption.deal_units != order.original_amount - order.boss_discount - order.promo_discount - order.coupon_discount
            or not isinstance(order.funding_terms, dict) or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}
            or redemption.points_hundredths != redemption.groups * redemption.group_points_hundredths
            or redemption.discount_units != redemption.groups * redemption.group_coin_units
            or original.balance_after != original.balance_before + original.amount or original.reversal_of_id
            or original.reversals.exists() or spent.order_id != order.pk or spent.account_id != order.customer_account_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{order.pk}:points-redemption'
            or PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').exclude(pk=spent.pk).exists()
            or Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').exclude(pk=original.pk).exists()):
        raise ValidationError('主单积分付款凭证异常')
    main_sources = list(order.funding_allocations.order_by('pk'))
    if (not main_sources or any(row.lot.account_id != order.customer_account_id for row in main_sources)):
        raise ValidationError('主单付款来源异常')
    from engagement.funding_wallet import validate_order_funding_sources
    validate_order_funding_sources(order, main_sources)
    sources = {kind: sum(row.amount for row in main_sources if row.kind == kind)
               for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        created_at__lt=refund.created_at).order_by('pk'))
    prestart_segments = _prestart_segments_at(order, refund.created_at)
    transfer_main = None
    if transfers:
        from .points_transfer_refund_evidence import _facts_current_read, _facts_without_receipt_locked
        from .points_transfer_receipt_usage import historical_plan, facts as transfer_release_facts
        from .models import OrderPointsTransferFundingRelease
        allocation = OrderPointsFundingAllocation.objects.filter(redemption=redemption,
            created_at__lte=refund.created_at).order_by('pk').first()
        if not allocation and read_receipt_id:
            allocation = OrderPointsFundingAllocation.objects.filter(redemption=redemption,
                receipt_id=read_receipt_id, created_at__gt=refund.created_at).order_by('pk').first()
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at=refund.created_at).exists():
            raise ValidationError('加购退款前转单或到账凭证缺失')
        historical = (_facts_current_read(order, read_receipt_id or allocation.receipt_id)
                      if read_receipt_id or allocation else _facts_without_receipt_locked(order))
        transfer_main, lines = historical_plan(historical, redemption, sources, transfers[-1].pk)
        for part in OrderPointsTransferFundingRelease.objects.filter(allocation__redemption=redemption,
                revision__created_at__lt=refund.created_at).select_related('revision__order', 'allocation__redemption'):
            from types import SimpleNamespace
            transfer_release_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=read_receipt_id or allocation.receipt_id))
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
            created_at__lte=refund.created_at).select_related('segment').order_by('segment__slot', 'pk'))
    else:
        if prestart_segments is not None:
            snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment_id__in=prestart_segments,
                created_at__lte=refund.created_at).select_related('segment').order_by('segment__slot', 'pk'))
            if {item.segment_id for item in snapshots} != set(prestart_segments):
                raise ValidationError('加购退款的改派席位快照不完整')
        elif OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                     created_at__gt=refund.created_at).exists():
            snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
                created_at__lte=refund.created_at).select_related('segment').order_by('segment__slot', 'pk'))
        else:
            from .points_service_participation import service_snapshots
            snapshots = service_snapshots(order)
    matching = [item for item in snapshots if item.segment_id == payment.intent.segment_id
                and item.segment.provider_account_id == participants[0].account_id]
    if (not snapshots or (not transfers and len(snapshots) not in (1, 2)) or len(matching) != 1):
        raise ValidationError('主单收益快照异常')
    share = matching[0]
    available_income = (sum(line[4] for line in lines if line[1] == participants[0].account_id)
                        if transfers else share.income_at_entry)
    if refund.main_pending_deduction > available_income:
        raise ValidationError('加购退款超过本人主单待结算收益')
    provider_tx = refund.provider_transaction
    if refund.wallet_deduction:
        if (not provider_tx or provider_tx.order_id is not None or provider_tx.wallet.account_id != participants[0].account_id
                or provider_tx.tx_type != 'REFUND' or provider_tx.status != 'SUCCESS'
                or provider_tx.amount != -refund.wallet_deduction
                or provider_tx.balance_after != provider_tx.balance_before + provider_tx.amount
                or provider_tx.operator_account_id != refund.operator_id or provider_tx.operator_id != refund.legacy_operator_id
                or provider_tx.reversal_of_id or provider_tx.reversals.exists()):
            raise ValidationError('加购退款钱包承担凭证异常')
    elif provider_tx:
        raise ValidationError('加购退款存在多余钱包扣款')
    deficits = list(refund.deposit_deficits.select_related('profile', 'source_store_transaction__wallet').order_by('pk'))
    if sum(item.amount for item in deficits) != refund.deposit_deduction:
        raise ValidationError('加购退款押金缺口合计异常')
    for deficit in deficits:
        source = deficit.source_store_transaction
        if (deficit.source_kind != 'DIRECT' or deficit.addon_refund_id != refund.pk
                or deficit.account_id != participants[0].account_id or deficit.profile.account_id != deficit.account_id
                or deficit.direct_key != f'addon:{refund.pk}:{deficit.profile_id}'
                or not source or source.order_id != order.pk or source.tx_type != 'DEPOSIT_INCOME'
                or not source.wallet.user_id or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or source.status != 'SUCCESS' or source.amount != -deficit.amount
                or source.balance_after != source.balance_before + source.amount
                or source.operator_account_id != refund.operator_id or source.operator_id != refund.legacy_operator_id
                or source.reversal_of_id or source.reversals.exists()):
            raise ValidationError('加购退款押金扣款凭证异常')
    try:
        main = transfer_main or plan_service_funding(deal_units=redemption.deal_units, point_discount_units=redemption.discount_units,
            shares=[ProviderShare(item.incoming_basis, item.commission_type, item.commission_rate,
                                  item.commission_fixed, item.income_at_entry) for item in snapshots],
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            principal_units=sources['PREPAID'], gift_units=sources['GIFT'], compensation_units=sources['COMPENSATION'])
    except PointsFundingPlanError as exc:
        raise ValidationError('主单收益或支付构成异常') from exc
    addon_principal = sum(row.amount - cumulative.get(row.pk, 0) for row in allocations if row.kind == 'PREPAID')
    addon_income = payment.provider_income - sum(item.addon_pending_deduction for item in [*earlier, refund])
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=refund.created_at).count() > 1:
        # A refund may have consumed another payment's pending earnings.
        # Replay only deductions which existed at this refund boundary.
        from .points_transfer_zero_addon_income import remaining_for_payment
        addon_income = remaining_for_payment(refund)
    if addon_income < 0 or payment.inviter_rate or payment.inviter_income:
        raise ValidationError('加购收益或推荐佣金异常')
    if (refund.buyer_transaction.reversal_of_id or refund.buyer_transaction.reversals.exists()
            or refund.buyer_transaction.operator_account_id != refund.operator_id
            or refund.buyer_transaction.operator_id != refund.legacy_operator_id):
        raise ValidationError('加购退款流水操作人或撤销状态异常')
    required = max(main.additional_store_funding_units - main.store_income_units + addon_income - addon_principal
                   - sum(item.main_pending_deduction + item.wallet_deduction + item.deposit_deduction
                         for item in [*earlier, refund]), 0)
    combined_history = None
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=refund.created_at).count() > 1:
        from .addon_funding_history import combined_at
        try:
            segment_shares = (lines if transfers else [(item.segment_id, item.segment.provider_account_id,
                item.incoming_basis, item.commission_fixed, item.income_at_entry) for item in snapshots])
            combined_history = combined_at(order, main, refund.created_at, segment_shares=segment_shares,
                                            include_refund_id=refund.pk)
            required = combined_history['additional_store_funding_units']
        except PointsFundingPlanError as exc:
            raise ValidationError('多项加购退款资金历史待核对') from exc
    def tx_fields(tx):
        return [tx.pk, tx.wallet_id, tx.order_id, tx.tx_type, tx.status, tx.amount,
                tx.balance_before, tx.balance_after, tx.operator_id, tx.operator_account_id, tx.reversal_of_id]
    result = {'order_id': order.pk, 'refund_id': refund.pk, 'payment_id': payment.pk,
        'refund_at': refund.created_at.isoformat(), 'payment_marker': order.funding_terms[MARKER],
        'redemption': [redemption.pk, redemption.deal_units, redemption.paid_units, redemption.discount_units,
                       redemption.points_hundredths, redemption.ledger_id, redemption.groups,
                       redemption.group_points_hundredths, redemption.group_coin_units,
                       redemption.max_ratio_basis_points, redemption.config_version],
        'spent': [spent.pk, spent.account_id, spent.order_id, spent.kind, spent.amount, spent.event_key],
        'main_payment': tx_fields(original), 'main_sources': [[x.pk, x.lot_id, x.kind, x.amount,
            x.lot.kind, x.lot.source_key] for x in main_sources],
        'share': [share.pk, share.segment_id, share.segment.provider_account_id, share.incoming_basis,
                  share.commission_type, share.commission_rate, share.commission_fixed, share.income_at_entry],
        'main_plan': asdict(main), 'addon_payment': tx_fields(payment.buyer_transaction),
        'addon_terms': [payment.intent_id, payment.intent.amount, payment.intent.buyer_id,
            payment.intent.provider_id, payment.intent.segment_id, payment.intent.principal, payment.intent.gift,
            payment.intent.compensation, payment.intent.commission_snapshot, payment.principal,
            payment.inviter_rate, payment.commission_fee, payment.provider_income, payment.inviter_income,
            payment.store_income, payment.funding_required],
        'addon_refund': tx_fields(refund.buyer_transaction),
        'addon_sources': [[x.pk, x.lot_id, x.kind, x.amount, cumulative.get(x.pk, 0),
                           x.lot.kind, x.lot.source_key] for x in allocations],
        'proposal': [proposal.pk, proposal.amount, proposal.source_plan, proposal.explanation,
                     proposal.case_id, proposal.case_opened_at.isoformat(), proposal.case_request_id,
                     proposal.accepted_at.isoformat()],
        'decisions': [[a, b, c, d.isoformat()] for a, b, c, d in decisions],
        'participants': [[p.pk, p.account_id, p.basis, p.burden, p.excess_burden, p.share_plan,
                          p.addon_pending_deduction, p.main_pending_deduction, p.wallet_deduction,
                          p.deposit_deduction] for p in participants],
        'deductions': [list(x) for x in refund.earning_deductions.order_by('pk').values_list(
            'pk', 'payment_id', 'participant_id', 'segment_id', 'amount')],
        'provider_transaction': tx_fields(provider_tx) if provider_tx else None,
        'deposit_sources': [[item.pk, item.profile_id, item.account_id, item.amount, item.direct_key,
                             tx_fields(item.source_store_transaction)] for item in deficits],
        'addon_income': addon_income, 'addon_principal': addon_principal, 'required': required}
    if proposal.cross_pending_plan is not None:
        result['cross_pending_plan'] = proposal.cross_pending_plan
    if len(snapshots) > 1:
        result['main_shares'] = [[item.pk, item.segment_id, item.segment.provider_account_id,
            item.incoming_basis, item.commission_type, item.commission_rate,
            item.commission_fixed, item.income_at_entry] for item in snapshots]
    if earlier:
        result['prior_refunds'] = [[item.pk, item.created_at.isoformat(), item.proposal_id,
            item.amount, item.proposal.source_plan, item.addon_pending_deduction,
            item.main_pending_deduction, item.wallet_deduction, item.deposit_deduction]
            for item in earlier]
    if transfers:
        result['transfer_id'] = transfers[-1].pk
        result['segment_shares'] = [list(line) for line in lines]
    prestart = _prestart_snapshot_at(order, refund.created_at)
    if prestart is not None:
        result['prestart'] = prestart
    if combined_history is not None:
        result['addon_history'] = combined_history['addon_history']
        result['prior_addon_events'] = (_prior_addon_events(order, refund.created_at, read_receipt_id=read_receipt_id)
                                        if _event_history else [])
    from .models import OrderAddonHandover
    handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__lt=refund.created_at).select_related('payment__intent', 'source_segment__attribution',
        'target_segment__attribution').order_by('accepted_at', 'pk'))
    if OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                                          accepted_at=refund.created_at).exists():
        raise ValidationError('加购接替与退款时序无法区分')
    if handovers:
        from .addon_handover import verify_handover
        history = []
        for handover in handovers:
            followup_run = OrderAddonFundingReleaseRun.objects.filter(handover=handover,
                points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1').first()
            if followup_run:
                from .points_transfer_followup_handover_scope import verify_event
                from .points_transfer_followup_addon_version import _verify_prior_run
                rows = verify_event(handover.payment, handover, before=refund.created_at,
                                    receipt_id=read_receipt_id)
                if followup_run.created_at >= refund.created_at:
                    raise ValidationError('加购接替资金版本晚于退款')
                _verify_prior_run(followup_run, receipt_id=read_receipt_id)
            else:
                rows = verify_handover(handover.payment, handover, as_of=refund.created_at,
                                       receipt_id=read_receipt_id)
            decisions = list(handover.decisions.order_by('account_id').values_list(
                'account_id', 'role', 'accepted', 'decided_at'))
            history.append([handover.pk, handover.payment_id, handover.accepted_at.isoformat(),
                handover.source_segment_id, handover.target_segment_id,
                [[a, b, c, at.isoformat()] for a, b, c, at in decisions],
                [[row.pk, row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current]
                 for row in rows]])
        result['prior_handovers'] = history
    return result



def payment_facts(payment, *, read_receipt_id=None, _event_history=True):
    """Rebuild original main/add-on payment facts by receipt-local current read."""
    return _read_history(_payment_facts, payment, read_receipt_id=read_receipt_id, _event_history=_event_history)


def _payment_facts(payment, *, read_receipt_id, _event_history):
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_payment import verify_receipt
    from .points_funding_plan import plan_service_funding, PointsFundingPlanError
    from .settlement import ProviderShare
    from .points_service_payment import MARKER
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import PointLedger
    from wallet.models import Transaction
    _require_current_unlocked_reads()
    order, intent, tx = payment.intent.order, payment.intent, payment.buyer_transaction
    from .models import OrderAddonRefund
    if (tx.created_at and (OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at=tx.created_at).exclude(pk=payment.pk).exists()
            or OrderAddonRefund.objects.filter(order=order, created_at=tx.created_at).exists())):
        raise ValidationError('加购付款与其他资金事件时间无法区分')
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                  created_at__lt=tx.created_at).exists():
        return _transferred_payment_facts(payment, read_receipt_id=read_receipt_id, _event_history=_event_history)
    if (order.is_gift_snapshot or order.source_order_id
            or order.required_provider_count not in (1, 2)
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=tx.created_at).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                         created_at__lte=tx.created_at).exists()
            or OrderPrestartHandoff.objects.filter(order=order, created_at__gte=tx.created_at).exists()
            or payment.inviter_rate or payment.inviter_income or not tx.created_at):
        raise ValidationError('积分加购付款资金范围已变化')
    verify_receipt(payment)
    redemption, original, spent = order.points_redemption, order.points_redemption.payment, order.points_redemption.ledger
    if (not isinstance(order.funding_terms, dict) or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}
            or redemption.paid_units != order.amount or redemption.deal_units != redemption.paid_units + redemption.discount_units
            or redemption.deal_units != order.original_amount - order.boss_discount - order.promo_discount - order.coupon_discount
            or redemption.points_hundredths != redemption.groups * redemption.group_points_hundredths
            or redemption.discount_units != redemption.groups * redemption.group_coin_units
            or original.order_id != order.pk or original.wallet.account_id != order.customer_account_id
            or original.tx_type != 'PAY' or original.status != 'SUCCESS' or original.amount != -redemption.paid_units
            or original.balance_after != original.balance_before + original.amount or original.reversal_of_id
            or original.reversals.exists() or spent.order_id != order.pk or spent.account_id != order.customer_account_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{order.pk}:points-redemption'
            or PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').exclude(pk=spent.pk).exists()
            or Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').exclude(pk=original.pk).exists()):
        raise ValidationError('积分加购原主单付款凭证异常')
    main_sources = list(order.funding_allocations.order_by('pk'))
    if not main_sources or any(row.lot.account_id != order.customer_account_id for row in main_sources):
        raise ValidationError('积分加购主单付款来源异常')
    validate_order_funding_sources(order, main_sources)
    sources = {kind: sum(row.amount for row in main_sources if row.kind == kind)
               for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    from .models import OrderSegmentFinancialSnapshot
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution__order=order, created_at__lte=tx.created_at)
        .select_related('segment', 'segment__attribution').order_by('segment__slot', 'pk'))
    selected_segments = _prestart_segments_at(order, tx.created_at)
    if selected_segments is not None:
        snapshots = [item for item in snapshots if item.segment_id in selected_segments]
        if {item.segment_id for item in snapshots} != set(selected_segments):
            raise ValidationError('加购付款的改派席位快照不完整')
    if (len(snapshots) != order.required_provider_count
            or len({item.segment.slot for item in snapshots}) != len(snapshots)):
        raise ValidationError('积分加购付款时的履约席位不完整')
    matching = [item for item in snapshots if item.segment_id == intent.segment_id
                and item.segment.provider_account_id == intent.provider_id]
    if len(snapshots) not in (1, 2) or len(matching) != 1:
        raise ValidationError('积分加购主单收益凭证异常')
    share = matching[0]
    try:
        main = plan_service_funding(deal_units=redemption.deal_units, point_discount_units=redemption.discount_units,
            shares=[ProviderShare(item.incoming_basis, item.commission_type, item.commission_rate,
                                  item.commission_fixed, item.income_at_entry) for item in snapshots],
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            principal_units=sources['PREPAID'], gift_units=sources['GIFT'], compensation_units=sources['COMPENSATION'])
    except PointsFundingPlanError as exc:
        raise ValidationError('积分加购主单资金计算异常') from exc
    allocations = list(payment.allocations.order_by('pk'))
    if (sum(row.amount for row in allocations) != intent.amount
            or sum(row.amount for row in allocations if row.kind == 'PREPAID') != intent.principal):
        raise ValidationError('积分加购付款来源合计异常')
    required = max(main.additional_store_funding_units - main.store_income_units
                   + payment.provider_income - intent.principal, 0)
    prior_payments = OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=tx.created_at).exists()
    history = None
    if prior_payments:
        from .addon_funding_history import combined_at
        try:
            history = combined_at(order, main, tx.created_at, exclude_payment_id=payment.pk,
                segment_shares=[(item.segment_id, item.segment.provider_account_id, item.incoming_basis,
                                 item.commission_fixed, item.income_at_entry) for item in snapshots])
        except PointsFundingPlanError as exc:
            raise ValidationError('此前加购付款或退款来源待核对') from exc
        required = max(history['additional_store_funding_units'] - history['store_income_units']
                       + payment.provider_income - intent.principal, 0)
    def tx_fields(row):
        return [row.pk, row.wallet_id, row.order_id, row.tx_type, row.status, row.amount,
                row.balance_before, row.balance_after, row.operator_id, row.operator_account_id,
                row.reversal_of_id, row.created_at.isoformat()]
    from .models import PointsNegotiationPosting, OrderProvider
    prior_main = list(PointsNegotiationPosting.objects.filter(points_refund__redemption__order=order,
        created_at__lt=tx.created_at).order_by('created_at', 'pk'))
    postmain = None
    if prior_main:
        if len(prior_main) != 1:
            raise ValidationError('该加购付款的主单退款历史尚未接入')
        posting = prior_main[0]
        assignment = OrderProvider.objects.filter(order=order, provider_account_id=intent.provider_id).first()
        if (not posting.termination_in_service or not posting.termination_processed_at
                or posting.termination_processed_at >= tx.created_at or not assignment
                or not share.segment.started_at or share.segment.started_at > tx.created_at
                or (share.segment.released_at and share.segment.released_at <= tx.created_at)
                or (assignment.released_at and assignment.released_at <= tx.created_at)
                or (assignment.settled_at and assignment.settled_at <= tx.created_at)):
            raise ValidationError('加购付款时负责人不属于仍在履约的陪玩')
        from .points_negotiation_execution import verify_participants
        verify_participants(posting)
        postmain = {'posting_id': posting.pk, 'segment_id': share.segment_id,
                    'account_id': intent.provider_id, 'termination_at': posting.termination_processed_at.isoformat()}
    result = {'version': 1, 'order_id': order.pk, 'payment_id': payment.pk,
        'main_payment': tx_fields(original),
        'redemption': [redemption.pk, redemption.deal_units, redemption.paid_units, redemption.discount_units,
                       redemption.points_hundredths, redemption.ledger_id, redemption.groups,
                       redemption.group_points_hundredths, redemption.group_coin_units,
                       redemption.max_ratio_basis_points, redemption.config_version],
        'main_sources': [[x.pk, x.lot_id, x.kind, x.amount, x.lot.kind, x.lot.source_key] for x in main_sources],
        'share': [share.pk, share.segment_id, share.segment.provider_account_id, share.incoming_basis,
                  share.commission_type, share.commission_rate, share.commission_fixed, share.income_at_entry],
        'main_plan': asdict(main), 'addon_payment': tx_fields(tx),
        'addon_terms': [intent.pk, intent.amount, intent.buyer_id, intent.provider_id, intent.segment_id,
                        intent.principal, intent.gift, intent.compensation, intent.commission_snapshot,
                        payment.commission_fee, payment.provider_income, payment.inviter_rate,
                        payment.inviter_income, payment.store_income, payment.funding_required],
        'addon_sources': [[x.pk, x.lot_id, x.kind, x.amount, x.lot.kind, x.lot.source_key] for x in allocations],
        'required': required}
    if len(snapshots) > 1:
        result['main_shares'] = [[item.pk, item.segment_id, item.segment.provider_account_id,
            item.incoming_basis, item.commission_type, item.commission_rate,
            item.commission_fixed, item.income_at_entry] for item in snapshots]
    if postmain is not None:
        result['postmain'] = postmain
    prestart = _prestart_snapshot_at(order, tx.created_at)
    if prestart is not None:
        result['prestart'] = prestart
    if history is not None:
        result['prior_addon_history'] = history['addon_history']
        result['prior_addon_events'] = (_prior_addon_events(order, tx.created_at, read_receipt_id=read_receipt_id)
                                        if _event_history else [])
    return result


def _transferred_payment_facts(payment, *, read_receipt_id=None, _event_history=True):
    """Rebuild a post-transfer purchase under the already-held receipt lock."""
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_payment import verify_receipt
    from .points_transfer_refund_evidence import _facts_current_read, _facts_without_receipt_locked
    from .points_transfer_receipt_usage import historical_plan, facts as transfer_release_facts
    from .models import OrderPointsTransferFundingRelease
    from engagement.funding_wallet import validate_order_funding_sources
    from rest_framework.exceptions import ValidationError as RestValidationError
    from dataclasses import asdict
    _require_current_unlocked_reads()
    order, intent, tx = payment.intent.order, payment.intent, payment.buyer_transaction
    if (order.is_gift_snapshot or order.source_order_id
            or order.refund_records.filter(created_at=tx.created_at).exists()
            or payment.refund_records.filter(created_at__lte=tx.created_at).exists()
            or payment.handovers.filter(status='ACCEPTED', accepted_at__lte=tx.created_at).exists()
            or OrderPrestartHandoff.objects.filter(order=order, created_at__gte=tx.created_at).exists()
            or payment.inviter_rate or payment.inviter_income):
        raise ValidationError('转单后积分加购付款范围尚未接入')
    from .points_service_payment import MARKER
    redemption, original, spent = order.points_redemption, order.points_redemption.payment, order.points_redemption.ledger
    if (not isinstance(order.funding_terms, dict) or order.funding_terms.get(MARKER) != {'receipt_id': redemption.pk}
            or original.order_id != order.pk or original.wallet.account_id != order.customer_account_id
            or original.tx_type != 'PAY' or original.status != 'SUCCESS' or original.amount != -redemption.paid_units
            or original.balance_after != original.balance_before + original.amount or original.reversal_of_id
            or original.reversals.exists() or spent.order_id != order.pk or spent.account_id != order.customer_account_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{order.pk}:points-redemption'):
        raise ValidationError('转单后积分加购主单付款异常')
    verify_receipt(payment)
    main_sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, main_sources)
    except RestValidationError as exc:
        raise ValidationError('转单后积分加购付款来源异常') from exc
    paid = {kind: sum(row.amount for row in main_sources if row.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    allocation = OrderPointsFundingAllocation.objects.filter(redemption=redemption,
        created_at__lte=tx.created_at).order_by('pk').first()
    if not allocation:
        if read_receipt_id:
            allocation = OrderPointsFundingAllocation.objects.filter(redemption=redemption,
                receipt_id=read_receipt_id, created_at__gt=tx.created_at).order_by('pk').first()
            if not allocation:
                raise ValidationError('转单后加购到账见证不属于本单或时序无效')
    main_refunds = list(order.refund_records.filter(created_at__lt=tx.created_at).order_by('created_at', 'pk'))
    postmain = None
    if main_refunds:
        from .models import PointsNegotiationPosting
        if len(main_refunds) != 1:
            raise ValidationError('加购付款的主单退款版本待核对')
        posting = PointsNegotiationPosting.objects.filter(points_refund__refund=main_refunds[0]).select_related(
            'proposal', 'points_refund').first()
        selected = set(posting.proposal.affected_segment_ids or []) if posting else set()
        if (not posting or not posting.termination_processed_at or posting.termination_processed_at >= tx.created_at
                or intent.segment.attribution.order_id != order.pk
                or intent.segment.provider_account_id != intent.provider_id
                or (intent.segment.released_at and intent.segment.released_at <= tx.created_at)
                or (intent.provider_id in posting.proposal.affected_accounts
                    and (not selected or intent.segment_id in selected))):
            raise ValidationError('加购付款时主单退款或履约段凭证异常')
        evidence = getattr(posting, 'funding_release_evidence', None)
        if not evidence:
            raise ValidationError('加购付款缺少主单退款资金凭证')
        if allocation:
            from types import SimpleNamespace
            from .points_transfer_refund_receipt_usage import facts as refund_facts
            ids = [item[0] for item in evidence.allocations]
            original_allocation = (OrderPointsFundingAllocation.objects.filter(pk__in=ids).first()
                                   if ids else allocation)
            if not original_allocation:
                raise ValidationError('加购付款缺少主单退款到账来源')
            refund_facts(SimpleNamespace(posting=posting, allocation=original_allocation, pk=0, amount=0,
                read_receipt_id=read_receipt_id or allocation.receipt_id, history_before=tx.created_at))
        else:
            from .points_negotiation_termination import verify_termination_locked
            from .points_transfer_refund_release import verify_release_locked
            verify_termination_locked(posting)
            verify_release_locked(evidence)
        postmain = [posting.pk, posting.points_refund_id, main_refunds[0].pk,
                    posting.termination_processed_at.isoformat(), intent.segment_id]
    transfers = list(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
        created_at__lt=tx.created_at).order_by('pk'))
    if not transfers or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
            created_at=tx.created_at).exists():
        raise ValidationError('转单与加购付款时间无法区分')
    _prestart_segments_at(order, tx.created_at)
    history = (_facts_current_read(order, read_receipt_id or allocation.receipt_id)
               if read_receipt_id or allocation else _facts_without_receipt_locked(order))
    main, lines = historical_plan(history, redemption, paid, transfers[-1].pk)
    segment = next((item for item in history['segments'] if item['id'] == intent.segment_id), None)
    from django.utils.dateparse import parse_datetime
    started = parse_datetime(segment['started_at']) if segment and segment.get('started_at') else None
    if (len([line for line in lines if line[0] == intent.segment_id and line[1] == intent.provider_id and line[2] > 0]) != 1
            or not started or started > tx.created_at):
        raise ValidationError('转单后加购负责人不属于付款时履约段')
    prior = list(OrderPointsTransferFundingRelease.objects.filter(allocation__redemption=redemption,
        revision__created_at__lt=tx.created_at).select_related('revision__order', 'allocation__redemption').order_by('pk'))
    for item in prior:
        from types import SimpleNamespace
        transfer_release_facts(SimpleNamespace(revision=item.revision, allocation=item.allocation,
            pk=item.pk, amount=item.amount, read_receipt_id=read_receipt_id or allocation.receipt_id))
    allocations = list(payment.allocations.select_related('lot').order_by('pk'))
    required = max(main.additional_store_funding_units - main.store_income_units
                   + payment.provider_income - intent.principal, 0)
    history_before = None
    if OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__lt=tx.created_at).exists():
        from .addon_funding_history import combined_at
        from .points_funding_plan import PointsFundingPlanError
        try:
            history_before = combined_at(order, main, tx.created_at, exclude_payment_id=payment.pk,
                                         segment_shares=lines)
        except PointsFundingPlanError as exc:
            raise ValidationError('此前转单加购来源待核对') from exc
        required = max(history_before['additional_store_funding_units'] - history_before['store_income_units']
                       + payment.provider_income - intent.principal, 0)
    result = {'version': 2, 'order_id': order.pk, 'payment_id': payment.pk, 'transfer_id': transfers[-1].pk,
        'main_payment': [original.pk, original.wallet_id, original.amount, original.balance_before, original.balance_after],
        'redemption': [redemption.pk, redemption.deal_units, redemption.paid_units, redemption.discount_units,
                       redemption.points_hundredths, spent.pk],
        'main_sources': [[row.pk, row.lot_id, row.kind, row.amount, row.lot.kind, row.lot.source_key] for row in main_sources],
        'segment_shares': [list(line) for line in lines], 'main_plan': asdict(main),
        'addon_payment': [tx.pk, tx.wallet_id, tx.amount, tx.balance_before, tx.balance_after,
                          tx.operator_id, tx.operator_account_id, tx.created_at.isoformat()],
        'addon_terms': [intent.pk, intent.amount, intent.buyer_id, intent.provider_id, intent.segment_id,
                        intent.principal, intent.gift, intent.compensation, intent.commission_snapshot,
                        payment.provider_income, payment.funding_required],
        'addon_sources': [[row.pk, row.lot_id, row.kind, row.amount, row.lot.kind, row.lot.source_key] for row in allocations],
        'prior_transfer_releases': [[row.pk, row.allocation_id, row.revision_id, row.amount] for row in prior],
        'required': required}
    if postmain is not None:
        result['postmain'] = postmain
    prestart = _prestart_snapshot_at(order, tx.created_at)
    if prestart is not None:
        result['prestart'] = prestart
    if history_before is not None:
        result['prior_addon_history'] = history_before['addon_history']
        result['prior_addon_events'] = (_prior_addon_events(order, tx.created_at, read_receipt_id=read_receipt_id)
                                        if _event_history else [])
    return result
