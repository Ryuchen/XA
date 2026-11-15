"""Funding facts for add-on events after two refunds of a transferred order.

This module only reconstructs facts. Its caller must hold the order, funding
and receipt locks and verify the previous follow-up revision before mutation.
"""
from dataclasses import asdict
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from rest_framework.exceptions import ValidationError

_preflight = ContextVar('transfer_followup_addon_preflight', default=None)
_verification_cache = ContextVar('transfer_followup_addon_verification_cache', default=None)


@contextmanager
def _verification_scope():
    if _verification_cache.get() is not None:
        yield
        return
    token = _verification_cache.set({})
    try:
        yield
    finally:
        _verification_cache.reset(token)


@contextmanager
def preflight_locked(order, *, payment_id=None, intent_id=None, handover_id=None, allow_paid=False):
    """Pin the verified previous whole-order funding version before mutation."""
    from stores.db import transaction
    from .models import OrderPointsTransferFollowupFundingRevision, OrderSegmentSettlementManifest
    from .points_transfer_followup_funding import plan_locked
    target = payment_id or intent_id or handover_id
    if (not transaction.get_connection().in_atomic_block
            or sum(bool(value) for value in (payment_id, intent_id, handover_id)) != 1
            or type(target) is not int or target <= 0):
        raise RuntimeError('转单后续加购预核必须在订单事务内')
    from .points_voluntary_handover_event import entry_matches
    voluntary_refund = bool(payment_id and entry_matches(order, payment_id))
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    paid = order.providers.filter(settled_at__isnull=False).exists()
    paid_payment = bool(allow_paid and intent_id and not payment_id and not handover_id)
    paid_handover = bool(handover_id and (manifest or paid))
    paid_refund = False
    if payment_id and (manifest or paid) and not voluntary_refund:
        from .models import PointsPaidHandoverEvidence
        evidence = PointsPaidHandoverEvidence.objects.filter(manifest=manifest,
            handover__payment_id=payment_id, settlement_revision__isnull=False).order_by('-pk').first() if manifest else None
        if evidence:
            from .points_followup_paid_handover import verify_current_read
            verify_current_read(evidence.funding_run)
            paid_refund = True
        elif manifest and isinstance(manifest.plan, dict):
            from .models import OrderAddonPayment
            from .addon_payment import verify_receipt
            target = OrderAddonPayment.objects.filter(pk=payment_id, intent__order=order).first()
            addons = manifest.plan.get('points_funding', {}).get('addons', [])
            if target and isinstance(addons, list) and any(
                    isinstance(item, dict) and item.get('payment_id') == payment_id for item in addons):
                verify_receipt(target)
                paid_refund = True
    if voluntary_refund and (manifest or paid):
        from .points_voluntary_paid_evidence import capture_before_locked
        if not manifest or manifest.finalized_at or not capture_before_locked(order):
            raise ValidationError('转单后续加购退款已付清单待核对')
    if paid_payment and (manifest or paid):
        from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
        from .segment_addon_settlement_revision import verify_revisions
        snapshot = _prior_settlement(order)
        if not manifest or manifest.finalized_at or not snapshot:
            raise ValidationError('转单后续加购付款已付清单待核对')
        verify_revisions(manifest)
        _verify_prior_settlement(order, snapshot)
    if paid_handover or paid_refund:
        from .points_transfer_refund_evidence import _prior_settlement, _verify_prior_settlement
        from .segment_addon_settlement_revision import verify_revisions
        snapshot = _prior_settlement(order)
        if not manifest or manifest.finalized_at or not snapshot or snapshot.get('manifest_id') != manifest.pk:
            raise ValidationError('已付加购事件的清单待核对')
        verify_revisions(manifest)
        _verify_prior_settlement(order, snapshot)
    plan_locked(order.pk, allow_recorded=bool((voluntary_refund or paid_payment or paid_handover or paid_refund) and manifest),
                allow_in_service=bool(intent_id or handover_id or payment_id))
    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
        posting__points_refund__redemption__order=order).first()
    if (not revision or order.settled_at
            or (not (voluntary_refund or paid_payment or paid_handover or paid_refund) and (paid or manifest))):
        raise ValidationError('转单后续加购的原资金版本待核对')
    token = _preflight.set((transaction.get_connection().alias, order.pk, revision.pk, revision.evidence_hash,
                            'refund' if payment_id else 'payment' if intent_id else 'handover', target))
    try:
        yield revision
    finally:
        _preflight.reset(token)


def _verified_revision(order):
    from stores.db import transaction
    from .models import OrderPointsTransferFollowupFundingRevision
    value = _preflight.get()
    if not value or value[:2] != (transaction.get_connection().alias, order.pk):
        raise ValidationError('转单后续加购缺少锁内预核')
    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(pk=value[2], evidence_hash=value[3]).first()
    if not revision:
        raise ValidationError('转单后续加购预核版本已变化')
    return revision


def handover_preflight_active(order, handover):
    """Match only the handover pinned by the current order transaction."""
    from stores.db import transaction
    state = _preflight.get()
    return bool(state and state[:2] == (transaction.get_connection().alias, order.pk)
                and state[4:] == ('handover', handover.pk))


def refund_preflight_active(order, payment):
    """Match the precise add-on payment pinned by this refund transaction."""
    from stores.db import transaction
    state = _preflight.get()
    return bool(transaction.get_connection().in_atomic_block and payment.intent.order_id == order.pk
                and state and state[:2] == (transaction.get_connection().alias, order.pk)
                and state[4:] == ('refund', payment.pk))


def _verify_prior_run(run, *, receipt_id):
    if receipt_id is None:
        from .addon_funding_release import verify_run
        verify_run(run)
        return
    from types import SimpleNamespace
    from django.db.models import Q
    from .models import OrderPointsFundingAllocation
    part = run.releases.select_related('allocation__redemption', 'run').order_by('pk').first()
    if part:
        receipt_facts(part, witness_receipt_id=receipt_id)
        return
    order_id = (run.refund.order_id if run.refund_id else run.payment.intent.order_id if run.payment_id
                else run.handover.payment.intent.order_id if run.handover_id else None)
    witness = OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order_id) | Q(service_order_id=order_id),
        receipt_id=receipt_id).order_by('pk').first()
    if not witness:
        raise ValidationError('零释放加购退款到账见证缺失')
    receipt_facts(SimpleNamespace(pk=0, amount=0, allocation=witness, run=run),
                  witness_receipt_id=receipt_id)


def latest_run_locked(order, revision, *, before=None, receipt_id=None):
    """Verify committed add-on events up to an optional immutable boundary."""
    with _verification_scope():
        return _latest_run_locked(order, revision, before=before, receipt_id=receipt_id)


def _latest_run_locked(order, revision, *, before=None, receipt_id=None):
    from .models import OrderAddonFundingReleaseRun, OrderAddonRefund, OrderAddonPayment, OrderAddonHandover
    from .addon_funding_release import verify_run
    refunds = list(OrderAddonRefund.objects.filter(order=order,
        created_at__gt=revision.posting.created_at).order_by('created_at', 'pk'))
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__gt=revision.posting.created_at).select_related('buyer_transaction'))
    events = [(row.created_at, 'refund', row) for row in refunds]
    events += [(row.buyer_transaction.created_at, 'payment', row) for row in payments]
    events += [(row.accepted_at, 'handover', row) for row in OrderAddonHandover.objects.filter(
        payment__intent__order=order, status='ACCEPTED', accepted_at__gt=revision.posting.created_at)]
    if before is not None:
        if any(event_at == before for event_at, _, _ in events):
            raise ValidationError('加购与转单事件时间无法区分')
        events = [event for event in events if event[0] < before]
    if not events:
        return None
    if len({row[0] for row in events}) != len(events):
        raise ValidationError('转单后续加购退款时间无法区分')
    previous = None
    for event_at, kind, event in sorted(events, key=lambda row: row[0]):
        run = OrderAddonFundingReleaseRun.objects.filter(**{kind: event}).first()
        if (not run or not isinstance(run.points_followup_snapshot, dict)
                or run.points_followup_snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'
                or run.points_followup_snapshot.get('followup_revision_id') != revision.pk
                or run.created_at < event_at
                or (previous and (previous.pk >= run.pk or previous.created_at >= event_at))):
            raise ValidationError('转单后续加购资金事件缺失或顺序异常')
        if receipt_id is None:
            verify_run(run)
        else:
            _verify_prior_run(run, receipt_id=receipt_id)
        previous = run
    return previous


def capture(order, revision, net_allocations, at, *, receipt_id=None, _historical=False):
    from stores.db import transaction
    from .models import PointsNegotiationPosting
    from .points_negotiation_preview import funding_after_refund
    from .points_transfer_followup_scope import combine_addon
    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts

    second = revision.posting
    terms = getattr(second.proposal, 'points_terms', None)
    first = PointsNegotiationPosting.objects.filter(
        points_refund_id=terms.prior_refund_id if terms else None).first()
    refunds_at = (order.refund_records.filter(created_at__lt=at) if _historical and at
                  else order.refund_records.all())
    if (not at or not first or second.created_at >= at or refunds_at.count() != 2
            or (_historical and order.refund_records.filter(created_at=at).exists())
            or first.points_refund.redemption_id != second.points_refund.redemption_id
            or first.points_refund.redemption.order_id != order.pk
            or second.points_refund.redemption.order_id != order.pk
            or set(refunds_at.values_list('pk', flat=True)) !=
               {first.points_refund.refund_id, second.points_refund.refund_id}
            or not isinstance(second.participants, dict)
            or not isinstance(second.participants.get('facts'), dict)):
        raise ValidationError('转单后续退款的加购原始凭证异常')
    from .models import PointsFollowupTransferRevision, OrderPointsFundingAllocation
    post_transfer = PointsFollowupTransferRevision.objects.filter(order=order, created_at__lt=at).first()
    if post_transfer:
        if receipt_id is None:
            state = _preflight.get()
            if state and state[:2] == (transaction.get_connection().alias, order.pk):
                from .points_followup_transfer_revision import _digest as post_transfer_digest
                if (post_transfer.followup_revision_id != revision.pk
                        or post_transfer.evidence_hash != post_transfer_digest(order.pk, post_transfer.transfer_id,
                            post_transfer.first_posting_id, post_transfer.second_posting_id,
                            post_transfer.followup_revision_id, post_transfer.snapshot)):
                    raise ValidationError('加购预核的转单资金版本已变化')
            else:
                from .points_followup_transfer_revision import verify_locked as verify_post_transfer
                verify_post_transfer(post_transfer)
        else:
            from types import SimpleNamespace
            from django.db.models import Q
            from .points_followup_transfer_receipt_usage import facts as post_transfer_facts
            part = post_transfer.releases.select_related('allocation__redemption', 'revision__order',
                'revision__transfer__previous_segment__attribution').order_by('pk').first()
            if part:
                part.read_receipt_id = receipt_id
                post_transfer_facts(part)
            else:
                ids = [row[0] for row in post_transfer.snapshot.get('allocations', [])]
                witness = (OrderPointsFundingAllocation.objects.filter(pk__in=ids).order_by('pk').first()
                    if ids else OrderPointsFundingAllocation.objects.filter(
                        Q(redemption__order=order) | Q(service_order=order), receipt_id=receipt_id,
                        created_at__gt=post_transfer.transfer.created_at).order_by('pk').first())
                if not witness:
                    raise ValidationError('新转单加购资金到账见证缺失')
                post_transfer_facts(SimpleNamespace(revision=post_transfer, allocation=witness,
                    pk=0, amount=0, read_receipt_id=receipt_id))
        facts = post_transfer.snapshot['facts']
    else:
        facts = second.participants['facts']
    transfers = facts.get('transfers')
    if not isinstance(transfers, list) or not transfers:
        raise ValidationError('转单后续退款缺少履约历史')
    if receipt_id is not None:
        from types import SimpleNamespace
        from django.db.models import Q
        from .models import OrderPointsFundingAllocation
        from .points_transfer_followup_receipt_usage import facts as followup_facts
        from .points_transfer_receipt_usage import historical_plan
        ids = [item[0] for item in revision.allocations]
        witness = (OrderPointsFundingAllocation.objects.filter(pk__in=ids).order_by('pk').first()
                   if ids else OrderPointsFundingAllocation.objects.filter(
                       Q(redemption__order=order) | Q(service_order=order),
                       receipt_id=receipt_id, created_at__gt=revision.created_at).order_by('pk').first())
        if not witness:
            raise ValidationError('转单后续加购原到账见证缺失')
        followup_facts(SimpleNamespace(posting=second, allocation=witness, pk=0, amount=0,
                                      read_receipt_id=receipt_id))
        sources = list(order.funding_allocations.order_by('pk'))
        paid = {kind: sum(item.amount for item in sources if item.kind == kind)
                for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        funding, lines = historical_plan(facts, second.points_refund.redemption, paid, transfers[-1]['id'])
        history = SimpleNamespace(funding=funding, segment_shares=lines)
    else:
        history = _reconstruct_from_verified_facts(order.pk, facts,
            refund_id=first.points_refund.refund_id, through_transfer_id=transfers[-1]['id'])
    original = history.funding
    proofs = [first.points_refund, second.points_refund]
    shares = [*first.shares.all(), *second.shares.all()]
    if (not shares or any(type(item) is not list or len(item) != 3
            or any(type(value) is not int or value < 0 for value in item) for item in net_allocations)):
        raise ValidationError('转单后续加购的到账依据异常')
    main = funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units,
        returned_prepaid_units=sum(item.prepaid_units for item in proofs),
        returned_gift_units=sum(item.gift_units for item in proofs),
        returned_compensation_units=sum(item.compensation_units for item in proofs),
        provider_income_units=original.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=sum(item[1] for item in net_allocations),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    deductions = [(item.account_id, item.pending_deduction,
        set(item.posting.proposal.affected_segment_ids or []),
        list(item.segment_deductions.values_list('segment_id', 'amount')) or None) for item in shares]
    funding, addon_history = combine_addon(order, main, at, history.segment_shares,
                                            facts['segments'], deductions)
    from .models import OrderAddonRefund, OrderAddonPayment, OrderAddonHandover
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__gt=second.created_at,
        created_at__lt=at).select_related('payment', 'proposal', 'buyer_transaction__wallet').order_by('created_at', 'pk'))
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__gt=second.created_at, buyer_transaction__created_at__lt=at)
        .select_related('intent__segment__attribution', 'intent__operator', 'buyer_transaction__wallet').order_by('pk'))
    events = ([(item.created_at, 'refund', item) for item in refunds]
              + [(item.buyer_transaction.created_at, 'payment', item) for item in payments])
    handovers = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__gt=second.created_at, accepted_at__lt=at).select_related(
            'payment__intent', 'source_segment__attribution', 'target_segment__attribution'))
    events += [(item.accepted_at, 'handover', item) for item in handovers]
    if not events or len({item[0] for item in events}) != len(events):
        raise ValidationError('转单后续加购退款事件范围待核对')
    events.sort(key=lambda item: item[0])
    event_at, event_kind, event = events[-1]
    from django.core.exceptions import ValidationError as DjangoValidationError
    event_data = {}
    if event_kind == 'payment':
        from .points_transfer_followup_addon_payment import facts as payment_facts
        event_data = {'addon_payment_id': event.pk,
                      'addon_payment_facts': payment_facts(event, revision, receipt_id=receipt_id)}
    elif event_kind == 'handover':
        from .points_transfer_followup_handover_scope import verify_event
        verified_shares = verify_event(event.payment, event, before=at, receipt_id=receipt_id)
        event_data = {'addon_handover_id': event.pk,
            'addon_handover_shares': [[row.pk, row.segment_id, row.basis, row.income,
                                       row.commission_snapshot, row.is_current] for row in verified_shares]}
    else:
        has_handover = event.payment.handovers.filter(status='ACCEPTED', accepted_at__lt=event.created_at).exists()
        try:
            if has_handover:
                from .points_addon_handover_refund_facts import first_refund_facts
                event_facts = first_refund_facts(event, receipt_id=receipt_id)
            else:
                from .models import PointsPostRefundTransferRevision
                versions = PointsPostRefundTransferRevision.objects.filter(order=order,
                    created_at__lt=event.created_at)
                attempts = list(event.payment.handovers.filter(created_at__lte=event.created_at).order_by('version'))
                pending_snapshot = revision.pending_handover_snapshot
                preserved = {item.get('handover_id') for item in pending_snapshot if isinstance(item, dict)
                    and item.get('payment_id') == event.payment_id} if isinstance(pending_snapshot, list) else set()
                if ([row.version for row in attempts] != list(range(1, len(attempts) + 1))
                        or any(row.status not in ('REJECTED', 'SUPERSEDED') or row.previous_id
                               or row.created_at == second.created_at
                               for row in attempts)
                        or any(not isinstance(version.snapshot, dict)
                            or not isinstance(version.snapshot.get('pending_handover', []), list)
                            or any(isinstance(row, list) and row and row[0] == event.payment_id
                                   for row in version.snapshot.get('pending_handover', [])) and not preserved
                            for version in versions)):
                    raise ValidationError('未接替付款存在历史转单接替责任')
                from .points_transfer_followup_handover_scope import _verify_rejected
                for index, attempt in enumerate(attempts):
                    boundary = attempts[index + 1].created_at if index + 1 < len(attempts) else event.created_at
                    if attempt.created_at < second.created_at and second.created_at < boundary:
                        boundary = second.created_at
                    if attempt.status == 'REJECTED':
                        _verify_rejected(event.payment, attempt, before=boundary)
                    else:
                        from .models import PointsHandoverRefundInvalidation
                        from .points_followup_handover_invalidation import capture as invalidation_capture, digest as invalidation_digest
                        record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt)\
                            .select_related('handover__payment__intent', 'refund__payment__intent').first()
                        if (not record or (attempt.created_at <= second.created_at and attempt.pk not in preserved)
                                or record.created_at < record.refund.created_at
                                or (index + 1 < len(attempts) and record.created_at >= attempts[index + 1].created_at)
                                or not isinstance(record.snapshot, dict) or record.evidence_hash != invalidation_digest(record.snapshot)):
                            raise ValidationError('退款导致首个接替失效的事实异常')
                        if record.refund_id == event.pk:
                            if invalidation_capture(attempt, event) != record.snapshot:
                                raise ValidationError('当前退款导致接替失效的事实异常')
                        else:
                            from .models import OrderAddonFundingReleaseRun
                            from .points_followup_handover_invalidation import verify as verify_invalidation
                            run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first()
                            if (record.refund.created_at >= event.created_at or not run
                                    or run.created_at >= event.created_at
                                    or (index + 1 < len(attempts)
                                        and run.created_at >= attempts[index + 1].created_at)):
                                raise ValidationError('此前失效退款的时间顺序异常')
                            verify_invalidation(record, receipt_id=receipt_id)
                from .points_addon_receipt_usage import facts as original_refund_facts
                event_facts = original_refund_facts(event, read_receipt_id=receipt_id)
        except DjangoValidationError as exc:
            raise ValidationError('转单后续加购退款确认凭证异常') from exc
        source_rows = {row.pk: row for row in event.payment.allocations.all()}
        source_plan = event.proposal.source_plan
        tx = event.buyer_transaction
        if (not isinstance(source_plan, list) or not source_plan
                or any(not isinstance(item, dict) or type(item.get('allocation_id')) is not int
                    or type(item.get('amount_raw')) is not int or item['amount_raw'] <= 0
                    or item['allocation_id'] not in source_rows
                    or source_rows[item['allocation_id']].kind != item.get('kind') for item in source_plan)
                or len({item['allocation_id'] for item in source_plan}) != len(source_plan)
                or sum(item['amount_raw'] for item in source_plan) != event.amount
                or not tx or tx.wallet.account_id != order.customer_account_id or tx.tx_type != 'REFUND'
                or tx.status != 'SUCCESS' or tx.amount != event.amount
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()
                or tx.operator_account_id != event.operator_id or tx.operator_id != event.legacy_operator_id):
            raise ValidationError('转单后续加购退款来源或老板流水异常')
        event_data = {'addon_refund_id': event.pk, 'addon_refund_facts': event_facts,
            'addon_refund_sources': source_plan,
            'buyer_transaction': [tx.pk, tx.wallet_id, tx.amount, tx.balance_before, tx.balance_after,
                                  tx.operator_id, tx.operator_account_id]}
    from .addon_main_segment_deduction import verified_deductions
    main_deductions = verified_deductions(order, before=at)
    result = {'kind': 'TRANSFER_FOLLOWUP_V1', 'first_posting_id': first.pk,
            'second_posting_id': second.pk, 'followup_revision_id': revision.pk,
            'followup_revision_hash': revision.evidence_hash, 'captured_at': at.isoformat(),
            'addon_history': addon_history,
            **event_data,
            'main_segment_deductions': [[key, value] for key, value in sorted(main_deductions.items())],
            'funding': asdict(funding)}
    if post_transfer:
        result['post_followup_transfer_id'] = post_transfer.pk
        result['post_followup_transfer_hash'] = post_transfer.evidence_hash
    if len(events) > 1:
        from .models import OrderAddonFundingReleaseRun
        prior_versions = []
        for earlier_at, earlier_kind, earlier in events[:-1]:
            run = OrderAddonFundingReleaseRun.objects.filter(**{earlier_kind: earlier}).first()
            if (not run or not isinstance(run.points_followup_snapshot, dict)
                    or run.points_followup_snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'
                    or run.points_followup_snapshot.get('followup_revision_id') != revision.pk
                    or run.created_at >= event_at):
                raise ValidationError('转单后续加购此前资金版本缺失')
            _verify_prior_run(run, receipt_id=receipt_id)
            prior_versions.append([earlier.pk, run.pk, run.points_followup_hash] if not payments and not handovers else
                                  [earlier_kind, earlier.pk, run.pk, run.points_followup_hash])
        result['prior_addon_events' if payments or handovers else 'prior_addon_versions'] = prior_versions
    return result


def digest(run, snapshot):
    payload = [run.refund_id, run.required_amount, run.allocated_before,
               run.released_amount, run.allocation_snapshot, snapshot]
    if run.payment_id:
        payload.append(['payment', run.payment_id])
    if run.handover_id:
        payload.append(['handover', run.handover_id])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(run, net_allocations, *, receipt_id=None):
    with _verification_scope():
        return _verify(run, net_allocations, receipt_id=receipt_id)


def _verify(run, net_allocations, *, receipt_id=None):
    from django.utils.dateparse import parse_datetime
    from .models import OrderPointsTransferFollowupFundingRevision
    snapshot = run.points_followup_snapshot
    if (not isinstance(snapshot, dict) or snapshot.get('kind') != 'TRANSFER_FOLLOWUP_V1'
            or run.points_followup_hash != digest(run, snapshot)
            or sum(bool(value) for value in (run.refund_id, run.payment_id, run.handover_id)) != 1):
        raise ValidationError('转单后续加购资金版本异常')
    try:
        cache_key = (run.pk, receipt_id, tuple(tuple(item) for item in net_allocations))
    except TypeError as exc:
        raise ValidationError('转单后续加购到账依据异常') from exc
    cache = _verification_cache.get()
    if cache_key in cache:
        return cache[cache_key]
    revision = OrderPointsTransferFollowupFundingRevision.objects.filter(
        pk=snapshot.get('followup_revision_id')).select_related('posting__proposal__case__order').first()
    at = parse_datetime(snapshot.get('captured_at', ''))
    event_at = (run.refund.created_at if run.refund_id else run.payment.buyer_transaction.created_at
                if run.payment_id else run.handover.accepted_at)
    event_order_id = (run.refund.order_id if run.refund_id else run.payment.intent.order_id
                      if run.payment_id else run.handover.payment.intent.order_id)
    if (not revision or revision.evidence_hash != snapshot.get('followup_revision_hash') or not at
            or not event_at or at <= revision.posting.created_at or at < event_at or at > run.created_at
            or event_order_id != revision.posting.proposal.case.order_id
            or capture(revision.posting.proposal.case.order, revision, net_allocations, at,
                       receipt_id=receipt_id, _historical=True) != snapshot
            or snapshot['funding']['required_units'] != run.required_amount):
        raise ValidationError('转单后续加购资金事实不一致')
    cache[cache_key] = snapshot
    return snapshot


def receipt_facts(row, *, witness_receipt_id=None):
    """Rebuild this event under the existing receipt lock using current reads."""
    from types import SimpleNamespace
    from django.core.exceptions import ValidationError as DjangoValidationError
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _addon_release_facts, _digest, _require_current_unlocked_reads
    from .models import (OrderAddonFundingRelease, OrderPointsFundingAllocation, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
                         PointsFollowupTransferRelease)
    from .points_transfer_receipt_usage import facts as transfer_facts
    from .points_post_refund_transfer_receipt_usage import facts as post_facts
    from .points_transfer_refund_receipt_usage import facts as first_facts
    from .points_transfer_followup_receipt_usage import facts as followup_facts
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    run, allocation = row.run, row.allocation
    receipt_id = witness_receipt_id or allocation.receipt_id
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    if run.refund_id:
        from .points_voluntary_paid_evidence import verify_current_read
        try:
            verify_current_read(run)
        except ValidationError as exc:
            raise DjangoValidationError('转单后续加购退款的已付流水异常') from exc
    if run.payment_id:
        from .points_voluntary_paid_evidence import verify_payment_current_read
        try:
            verify_payment_current_read(run)
        except ValidationError as exc:
            raise DjangoValidationError('转单后续加购付款的已付流水异常') from exc
    if run.handover_id:
        from .points_followup_paid_handover import verify_current_read
        try:
            verify_current_read(run, receipt_id=receipt_id)
        except ValidationError as exc:
            raise DjangoValidationError('转单后续加购接替的已付流水异常') from exc
    event_order_id = (run.refund.order_id if run.refund_id else run.payment.intent.order_id if run.payment_id
                      else run.handover.payment.intent.order_id if run.handover_id else None)
    if not event_order_id or allocation.funding_order_id != event_order_id:
        raise DjangoValidationError('转单后续加购到账归属异常')
    if not isinstance(run.allocation_snapshot, list):
        raise DjangoValidationError('转单后续加购到账快照缺失')
    normalized, net, sources = [], [], []
    for item in run.allocation_snapshot:
        if (not isinstance(item, list) or len(item) not in (6, 7) or any(type(item[i]) is not int for i in range(3))
                or not all(isinstance(group, list) for group in item[3:])):
            raise DjangoValidationError('转单后续加购到账快照异常')
        original = OrderPointsFundingAllocation.objects.filter(pk=item[0]).first()
        if not original or original.funding_order_id != event_order_id or original.amount != item[1]:
            raise DjangoValidationError('转单后续加购原到账不一致')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation=original, run_id__lt=run.pk)
                       .select_related('run', 'allocation').order_by('pk'))
        if sum(entry.amount for entry in earlier) != item[2]:
            raise DjangoValidationError('转单后续加购此前释放不一致')
        sources.extend(_addon_release_facts(entry, witness_receipt_id=receipt_id) for entry in earlier)
        transfers = list(OrderPointsTransferFundingRelease.objects.filter(allocation=original,
            revision__created_at__lt=run.created_at).select_related('revision__order', 'allocation__redemption').order_by('pk'))
        main = list(OrderPointsFundingRelease.objects.filter(allocation=original, created_at__lt=run.created_at)
                    .select_related('posting').order_by('pk'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=original,
            revision__created_at__lt=run.created_at).select_related('revision__order', 'revision__transfer',
                'allocation__redemption').order_by('pk'))
        followup_transfers = list(PointsFollowupTransferRelease.objects.filter(allocation=original,
            revision__created_at__lt=run.created_at).select_related('revision__order',
                'revision__transfer__previous_segment__attribution', 'allocation__redemption').order_by('pk'))
        if ([[entry.pk, entry.amount] for entry in transfers] != item[3]
                or [[entry.pk, entry.amount] for entry in main] != item[4]
                or [[entry.pk, entry.amount] for entry in post] != item[5]
                or ([[entry.pk, entry.amount] for entry in followup_transfers] != item[6]
                    if len(item) == 7 else bool(followup_transfers))):
            raise DjangoValidationError('转单后续加购前序释放不一致')
        for entry in transfers:
            sources.append(transfer_facts(SimpleNamespace(revision=entry.revision, allocation=original,
                pk=entry.pk, amount=entry.amount, read_receipt_id=receipt_id)))
        for entry in main:
            if hasattr(entry.posting, 'transfer_followup_funding_revision'):
                verifier = followup_facts
            elif isinstance(entry.posting.participants, list):
                from users.receipt_usage_sources import first_negotiation_release_facts
                verifier = first_negotiation_release_facts
            else:
                verifier = first_facts
            sources.append(verifier(SimpleNamespace(posting=entry.posting, allocation=original,
                pk=entry.pk, amount=entry.amount, read_receipt_id=receipt_id,
                history_before=run.created_at)))
        for entry in post:
            sources.append(post_facts(SimpleNamespace(revision=entry.revision, allocation=original,
                pk=entry.pk, amount=entry.amount, read_receipt_id=receipt_id)))
        from .points_followup_transfer_receipt_usage import facts as followup_transfer_facts
        for entry in followup_transfers:
            sources.append(followup_transfer_facts(SimpleNamespace(revision=entry.revision, allocation=original,
                pk=entry.pk, amount=entry.amount, read_receipt_id=receipt_id)))
        used = item[2] + sum(entry.amount for entry in transfers + main + post + followup_transfers)
        if used > original.amount:
            raise DjangoValidationError('转单后续加购释放超出原分配')
        normalized.append([original.pk, original.amount, used])
        net.append([original.pk, original.amount - used, original.receipt_id])
    try:
        before = sum(amount - used for _, amount, used in normalized)
        amount = max(before - run.required_amount, 0)
        expected = plan_release(allocations=normalized, amount=amount)
        verify(run, net, receipt_id=receipt_id)
    except (ValidationError, PointsFundingAllocationError, TypeError) as exc:
        raise DjangoValidationError('转单后续加购到账凭证异常') from exc
    synthetic = row.pk == 0 and row.amount == 0
    if (before != run.allocated_before or amount != run.released_amount
            or [(item.allocation_id, item.amount) for item in run.releases.order_by('-allocation_id')]
               != [(item.allocation_id, item.amount) for item in expected]
            or (synthetic and (run.released_amount != 0 or run.releases.exists()))
            or (not synthetic and not run.releases.filter(pk=row.pk, allocation=allocation, amount=row.amount).exists())):
        raise DjangoValidationError('转单后续加购释放金额不一致')
    payload = ['points-transfer-followup-addon-release', row.pk, row.amount, allocation.pk,
                     allocation.receipt_id, run.pk, run.refund_id, run.points_followup_hash,
                     run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot, sources]
    if run.payment_id:
        payload.append(['payment', run.payment_id])
    if run.handover_id:
        payload.append(['handover', run.handover_id])
    return ('RELEASE', f'order:{allocation.pk}', f'points-transfer-followup-addon-release:{row.pk}', row.amount,
            _digest(payload))


def record_locked(order, refund=None, *, payment=None, handover=None):
    """Write one payment or refund release within its locked order transaction."""
    from django.utils import timezone
    from stores.db import transaction
    from .models import (OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderPointsFundingRelease,
                         OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
                         PointsFollowupTransferRelease, PointsFollowupTransferRevision)
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_funding_allocation import plan_release
    from .addon_funding_release import addon_release_totals
    if (not transaction.get_connection().in_atomic_block or sum(bool(x) for x in (refund, payment, handover)) != 1
            or (refund and refund.order_id != order.pk)
            or (payment and payment.intent.order_id != order.pk)
            or (handover and (handover.payment.intent.order_id != order.pk or handover.status != 'ACCEPTED'))):
        raise ValidationError('转单后续加购退款必须在原订单事务内')
    revision = _verified_revision(order)
    state = _preflight.get()
    if ((refund and (state[4] != 'refund' or refund.payment_id != state[5]))
            or (payment and (state[4] != 'payment' or payment.intent_id != state[5]))
            or (handover and (state[4] != 'handover' or handover.pk != state[5]))):
        raise ValidationError('转单后续退款与预核加购不一致')
    existing = (OrderAddonFundingReleaseRun.objects.filter(refund=refund).first() if refund else
        OrderAddonFundingReleaseRun.objects.filter(payment=payment).first() if payment else
        OrderAddonFundingReleaseRun.objects.filter(handover=handover).first())
    if existing:
        raise ValidationError('转单后续加购资金事件已存在，请按原凭证核对')
    allocations = verified_order_allocations_locked(order)
    previous = addon_release_totals(allocations)
    rows = []
    for allocation in allocations:
        transfers = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation).order_by('pk'))
        main = list(OrderPointsFundingRelease.objects.filter(allocation=allocation).order_by('pk'))
        post = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation).order_by('pk'))
        if (any(row.revision.order_id != order.pk for row in transfers + post)
                or any(row.posting.proposal.case.order_id != order.pk for row in main)):
            raise ValidationError('转单后续加购释放来源不属于本单')
        row = [allocation.pk, allocation.amount, previous[allocation.pk],
            [[item.pk, item.amount] for item in transfers], [[item.pk, item.amount] for item in main],
            [[item.pk, item.amount] for item in post]]
        if PointsFollowupTransferRevision.objects.filter(order=order).exists():
            followup_transfers = list(PointsFollowupTransferRelease.objects.filter(allocation=allocation)
                                      .select_related('revision').order_by('pk'))
            if any(item.revision.order_id != order.pk for item in followup_transfers):
                raise ValidationError('后续转单释放来源不属于本单')
            row.append([[item.pk, item.amount] for item in followup_transfers])
        rows.append(row)
    normalized = [[row[0], row[1], row[2] + sum(item[1] for group in row[3:] for item in group)]
                  for row in rows]
    if any(used > amount for _, amount, used in normalized):
        raise ValidationError('转单后续加购此前释放超过原分配')
    net = [[row[0], row[1] - row[2], allocation.receipt_id]
           for row, allocation in zip(normalized, allocations)]
    snapshot = capture(order, revision, net, timezone.now())
    before = sum(row[1] for row in net)
    required = snapshot['funding']['required_units']
    amount = max(before - required, 0)
    parts = plan_release(allocations=normalized, amount=amount)
    receipts_by_allocation = {row.pk: row.receipt for row in allocations}
    if any(receipts_by_allocation[part.allocation_id].usage_version is None for part in parts):
        raise ValidationError('转单后续加购释放缺少到账用途账本')
    run = OrderAddonFundingReleaseRun.objects.create(refund=refund, payment=payment, handover=handover, required_amount=required,
        allocated_before=before, released_amount=amount, allocation_snapshot=rows,
        points_followup_snapshot=snapshot)
    run.points_followup_hash = digest(run, snapshot)
    run.save(update_fields=['points_followup_hash'])
    OrderAddonFundingRelease.objects.bulk_create([
        OrderAddonFundingRelease(run=run, allocation_id=part.allocation_id, amount=part.amount) for part in parts])
    from .addon_funding_release import verify_run
    verify_run(run, _skip_usage=True)
    from users.receipt_usage_sources import record_first_addon_release_locked, verify_allocations_locked
    touched = set()
    for release in run.releases.select_related('allocation__receipt', 'allocation__redemption', 'run').order_by(
            'allocation__receipt_id', 'pk'):
        if release.allocation.receipt.usage_version is not None:
            record_first_addon_release_locked(release)
            touched.add(release.allocation.receipt_id)
    for receipt_id in sorted(touched):
        verify_allocations_locked(receipt_id)
    return run
