"""Settlement plan after the bounded third points refund; not a payout entry."""
from copy import deepcopy
from dataclasses import replace

from django.db.models import Q
from stores.db import transaction
from .settlement import SettlementError


def build_locked(order_id, *, now=None, allow_finalized=False, funding_only=False, _allow_addons=False):
    from .models import (Order, OrderAddonPayment, OrderPointsFundingAllocation,
                         OrderPointsFundingRelease, PointsRefundRoundEvidence)
    from .points_transfer_refund_access import permits
    from .points_negotiation_preview import RefundedFunding
    from .points_refund_round_evidence import _digest
    from .points_third_refund_receipt_usage import facts as receipt_facts
    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
    from .points_transfer_refunded_plan import build_from_verified_evidence
    from .financial_lock import lock_order_money
    from users.models import PassRefundRepaymentReceipt
    from users.receipt_usage_guard import isolated_allocation_state_locked

    if not transaction.get_connection().in_atomic_block or not any(
            permits(phase=phase) for phase in ('refund', 'settlement')):
        raise SettlementError('第三轮退款结算须在受控订单事务中核对')
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if (not order or not any(permits(order, phase=phase) for phase in ('refund', 'settlement'))
            or order.status not in ((Order.Status.IN_SERVICE, Order.Status.COMPLETED) if funding_only else (Order.Status.COMPLETED,))
            or (not funding_only and not order.completed_at) or (order.settled_at and not allow_finalized) or order.refund_records.count() != 3
            or (not _allow_addons and OrderAddonPayment.objects.filter(intent__order=order).exists())):
        raise SettlementError('第三轮退款结算范围尚未接通')
    lock_order_money()
    versions = list(PointsRefundRoundEvidence.objects.select_for_update().filter(order=order)
                    .select_related('posting__proposal__case', 'posting__points_refund__refund',
                                    'transfer_revision__transfer__next_segment',
                                    'transfer_revision__first_posting__points_refund',
                                    'transfer_revision__second_posting').order_by('pk')[:2])
    if len(versions) != 1:
        raise SettlementError('第三轮退款结算来源版本缺失或重复')
    version = versions[0]
    posting, transfer = version.posting, version.transfer_revision
    rounds = version.snapshot.get('rounds') if isinstance(version.snapshot, dict) else None
    if (version.evidence_hash != _digest(version.snapshot) or not isinstance(rounds, list) or len(rounds) != 3
            or rounds[-1].get('posting_id') != posting.pk or not posting.termination_processed_at
            or not isinstance(posting.points_refund.refund.benefit_effects, dict)
            or posting.points_refund.refund.benefit_effects.get('points_third_refund_termination_version') != 1):
        raise SettlementError('第三轮退款实际结案凭证异常')
    source = rounds[-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise SettlementError('第三轮退款资金计划缺失')
    current = source[1]
    rows = list(OrderPointsFundingRelease.objects.filter(posting=posting).select_related('allocation__receipt')
                .order_by('allocation__receipt_id', 'pk'))
    allocation_ids = {item[0] for item in current.get('allocations', [])}
    owner = Q(redemption__order=order) | Q(service_order=order)
    all_allocations = list(OrderPointsFundingAllocation.objects.filter(owner).order_by('pk'))
    later = [item for item in all_allocations if item.pk not in allocation_ids]
    if any(item.created_at <= posting.created_at for item in later):
        raise SettlementError('第三轮退款历史到账快照不一致')
    receipt_ids = {item.receipt_id for item in all_allocations}
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=receipt_ids).order_by('pk'))
    if len(receipts) != len(receipt_ids):
        raise SettlementError('第三轮退款结算到账来源缺失')
    try:
        for receipt in receipts:
            if receipt.usage_version is None:
                raise SettlementError('第三轮退款结算到账用途账本缺失')
            isolated_allocation_state_locked(receipt)
        for row in rows:
            receipt_facts(row)
        if not rows and all_allocations:
            from types import SimpleNamespace
            witness = OrderPointsFundingAllocation.objects.select_related('receipt').filter(
                pk__in=allocation_ids or [item.pk for item in later]).order_by('pk').first()
            if not witness:
                raise SettlementError('第三轮零释放结算缺少到账见证')
            receipt_facts(SimpleNamespace(posting=posting, allocation=witness, pk=0, amount=0))
        elif not all_allocations:
            from .points_third_refund_no_receipt import verify_locked
            verify_locked(order, posting, transfer, current)
        events = _third_addon_events_locked(order, version) if _allow_addons else []
        from .models import PointsThirdTransferRevision
        transfer_events = list(PointsThirdTransferRevision.objects.select_for_update().filter(order=order)
            .select_related('transfer__next_segment', 'third').order_by('created_at', 'pk'))
        if transfer_events:
            from .points_third_addon_receipt_usage import verify_third_transfer_run_locked
            for transfer_event in transfer_events:
                verify_third_transfer_run_locked(transfer_event)
        mixed = sorted([*events, *transfer_events], key=lambda event: (event.created_at, event.pk))
        funding = RefundedFunding(**(mixed[-1].snapshot['after'] if mixed else current['funding']))
        from .points_funding_evidence import _verified_allocations_for_locked_order
        verified = _verified_allocations_for_locked_order(order)
        if {item.pk for item in verified} != {item.pk for item in all_allocations}:
            raise SettlementError('第三轮后续到账记录已变化')
        from .models import (OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
                             PointsFollowupTransferRelease, OrderAddonFundingRelease, PointsThirdTransferRelease)
        from django.db.models import Sum
        original = {item[0]: item[2] for item in current['allocations']}
        third_releases = dict(current['releases'])
        event_releases = {}
        for event in events:
            for allocation_id, amount in event.run.releases.values_list('allocation_id', 'amount'):
                event_releases[allocation_id] = event_releases.get(allocation_id, 0) + amount
        for event in transfer_events:
            for allocation_id, amount in event.releases.values_list('allocation_id', 'amount'):
                event_releases[allocation_id] = event_releases.get(allocation_id, 0) + amount
        available = 0
        for item in verified:
            actual_released = sum(model.objects.filter(allocation=item).aggregate(total=Sum('amount'))['total'] or 0
                for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                              PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
                              OrderAddonFundingRelease, PointsThirdTransferRelease))
            expected_released = original.get(item.pk, 0) + third_releases.get(item.pk, 0) + event_releases.get(item.pk, 0)
            if actual_released != expected_released or actual_released > item.amount:
                raise SettlementError('第三轮退款后的到账释放与原版本不一致')
            available += item.amount - actual_released
        if available > funding.required_units:
            raise SettlementError('第三轮退款后到账超过整单缺口')
        if not funding_only and available < funding.required_units:
            raise SettlementError('第三轮退款后整单补资尚未足额')
        funding = replace(funding, allocated_units=available, release_units=0,
                          remaining_units=max(funding.required_units - available, 0))
        addon_history = (_addon_history_locked(order, version) if _allow_addons
                         and OrderAddonPayment.objects.filter(intent__order=order).exists() else None)
        if funding_only:
            return None, funding
        if transfer_events:
            from .points_third_addon_responsibilities import current as current_responsibilities
            from .models import OrderAddonPayment
            for event in transfer_events:
                for payment_id, source_id, _ in event.snapshot.get('pending_handover', []):
                    payment = OrderAddonPayment.objects.filter(pk=payment_id, intent__order=order).first()
                    if not payment:
                        raise SettlementError('转单待接替加购付款缺失')
                    _, parts = current_responsibilities(payment, lock=True)
                    if any(part['is_current'] and part['segment_id'] == source_id and part['basis'] > 0 for part in parts):
                        raise SettlementError('转出加购尚待确认接替')
        latest_transfer = transfer_events[-1] if transfer_events else None
        facts = deepcopy(latest_transfer.snapshot['facts'] if latest_transfer else transfer.snapshot['facts'])
        target = latest_transfer.transfer.next_segment if latest_transfer else transfer.transfer.next_segment
        targets = [transfer.transfer.next_segment] + [event.transfer.next_segment for event in transfer_events]
        for segment in targets:
            selected = [item for item in facts['segments'] if item.get('id') == segment.pk]
            if len(selected) != 1 or not segment.started_at:
                raise SettlementError('第三轮退款后的新履约段凭证缺失')
            selected[0]['started_at'] = segment.started_at.isoformat()
            selected[0]['start_source'] = segment.start_source
        history = _reconstruct_from_verified_facts(order.pk, facts,
            refund_id=transfer.first_posting.points_refund.refund_id,
            through_transfer_id=latest_transfer.transfer_id if latest_transfer else transfer.transfer_id)
        plan_funding = funding
        if addon_history is not None:
            addon_deducted = sum(posting.shares.values_list('addon_deductions__amount', flat=True)
                                 .exclude(addon_deductions__amount__isnull=True))
            addon_income = sum(item['provider_units'] for item in addon_history) - addon_deducted
            if addon_income < 0 or addon_income > funding.provider_income_units:
                raise SettlementError('第三轮加购剩余收益与资金版本不一致')
            plan_funding = replace(funding, provider_income_units=funding.provider_income_units - addon_income)
        plan, _ = build_from_verified_evidence(order, facts, history,
            [transfer.first_posting, transfer.second_posting, posting], plan_funding, now=now)
    except (KeyError, TypeError, ValueError) as exc:
        raise SettlementError('第三轮退款逐段资金版本异常') from exc
    return plan, funding


def _third_addon_events_locked(order, version):
    from .models import (OrderAddonPayment, OrderAddonRefund, OrderAddonHandover,
                         PointsThirdAddonFundingRevision)
    posting = version.posting
    events = list(PointsThirdAddonFundingRevision.objects.select_for_update().filter(order=order)
                  .select_related('run', 'payment__intent', 'payment__buyer_transaction', 'refund__payment', 'handover__payment', 'third')
                  .order_by('created_at', 'pk'))
    actual = set(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__gte=posting.created_at).values_list('pk', flat=True))
    actual_refunds = set(OrderAddonRefund.objects.filter(order=order,
        created_at__gte=posting.created_at).values_list('pk', flat=True))
    actual_handovers = set(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__gte=posting.created_at).values_list('pk', flat=True))
    if (actual != {item.payment_id for item in events if item.payment_id}
            or actual_refunds != {item.refund_id for item in events if item.refund_id}
            or actual_handovers != {item.handover_id for item in events if item.handover_id}):
        raise SettlementError('第三轮后付款缺少真实资金事件')
    previous = None
    if events:
        from .points_third_addon_receipt_usage import verify_run_locked
    for event in events:
        event_at = (event.payment.buyer_transaction.created_at if event.payment_id else
                    event.refund.created_at if event.refund_id else event.handover.accepted_at)
        if (event.third_id != version.pk or event.previous_id != (previous.pk if previous else None)
                or event_at <= posting.created_at or (previous and event_at <= previous.created_at)):
            raise SettlementError('第三轮后付款资金事件顺序异常')
        verify_run_locked(event.run)
        previous = event
    return events


def _addon_history_locked(order, version):
    """Pick the real last add-on event strictly before this refund."""
    from .models import OrderAddonPayment, OrderAddonRefund, OrderAddonHandover
    from .points_transfer_followup_addon_version import latest_run_locked
    posting, transfer = version.posting, version.transfer_revision
    events = _third_addon_events_locked(order, version)
    from rest_framework.exceptions import ValidationError
    try:
        latest = latest_run_locked(order, transfer.followup_revision, before=posting.created_at)
    except ValidationError as exc:
        raise SettlementError('第三轮加购历史资金事件待核对') from exc
    if latest and latest.created_at > transfer.created_at:
        if (latest.created_at >= posting.created_at
                or latest.points_followup_snapshot.get('post_followup_transfer_id') != transfer.pk
                or latest.points_followup_snapshot.get('post_followup_transfer_hash') != transfer.evidence_hash):
            raise SettlementError('第三轮加购资金版本与转单不一致')
        history = latest.points_followup_snapshot.get('addon_history')
    else:
        history = transfer.snapshot.get('addon_history')
    if history is None and events:
        history = []
    if not isinstance(history, list):
        raise SettlementError('第三轮加购资金历史缺失')
    history = deepcopy(history)
    for event in events:
        if event.handover_id:
            handover = event.handover
            target = next((item for item in history if item['payment_id'] == handover.payment_id), None)
            if not target or target['handover_id'] != handover.previous_id:
                raise SettlementError('第三轮后接替与原付款责任版本不一致')
            snapshot = event.snapshot
            target['provider_units'] += (sum(row[4] for row in snapshot['after_shares'])
                                         - sum(row[4] for row in snapshot['before_shares']))
            target['handover_id'] = handover.pk
            if target['provider_units'] < 0:
                raise SettlementError('第三轮后接替收益异常')
            continue
        if event.refund_id:
            refund = event.refund
            by_payment = {item['payment_id']: item for item in history}
            target = by_payment.get(refund.payment_id)
            if not target:
                raise SettlementError('第三轮后退款没有原付款历史')
            target['refund_ids'].append(refund.pk)
            target['paid_units'] -= refund.amount
            for part in refund.proposal.source_plan:
                key = {'PREPAID': 'principal_units', 'GIFT': 'gift_units',
                       'COMPENSATION': 'compensation_units'}.get(part['kind'])
                if not key:
                    raise SettlementError('第三轮后退款付款来源异常')
                target[key] -= part['amount_raw']
            target['recovery_units'] += refund.wallet_deduction + refund.deposit_deduction
            for deduction in refund.earning_deductions.all():
                actual = by_payment.get(deduction.payment_id)
                if not actual:
                    raise SettlementError('第三轮后退款承担缺少原付款')
                actual['provider_units'] -= deduction.amount
            main = dict(target.get('main_deductions', []))
            for participant in refund.proposal.participants.all():
                if participant.main_pending_deduction:
                    main[participant.account_id] = main.get(participant.account_id, 0) + participant.main_pending_deduction
            target['main_deductions'] = [list(item) for item in sorted(main.items())]
            if any(item[key] < 0 for item in history for key in
                   ('paid_units', 'principal_units', 'gift_units', 'compensation_units', 'provider_units')):
                raise SettlementError('第三轮后退款超过实际付款或收益')
            continue
        payment = event.payment
        sources = {kind: sum(row.amount for row in payment.allocations.filter(kind=kind))
                   for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        history.append({'payment_id': payment.pk, 'handover_id': None, 'refund_ids': [],
            'paid_units': payment.intent.amount, 'principal_units': sources['PREPAID'],
            'gift_units': sources['GIFT'], 'compensation_units': sources['COMPENSATION'],
            'provider_units': payment.provider_income, 'main_deductions': [],
            'inviter_units': 0, 'recovery_units': 0})
    return history


def prepare_locked(order_id, *, now=None, allow_finalized=False, _allow_addons=None):
    """Build immutable manifest inputs after verifying all three real refunds."""
    from dataclasses import asdict
    from .models import (Order, OrderAddonPayment, OrderPointsFundingAllocation, PointsRefundRoundEvidence,
                         PointsThirdTransferRevision)
    from .segment_settlement import _terms
    if _allow_addons is None:
        _allow_addons = OrderAddonPayment.objects.filter(intent__order_id=order_id).exists()
    plan, funding = build_locked(order_id, now=now, allow_finalized=allow_finalized, _allow_addons=_allow_addons)
    order = Order.objects.get(pk=order_id)
    version = PointsRefundRoundEvidence.objects.select_related(
        'posting', 'transfer_revision__first_posting', 'transfer_revision__second_posting').get(order=order)
    current = version.snapshot['rounds'][-1]['release_source'][1]
    released = dict(current['releases'])
    events = _third_addon_events_locked(order, version) if _allow_addons else []
    event_released = {}
    for event in events:
        for allocation_id, amount in event.run.releases.values_list('allocation_id', 'amount'):
            event_released[allocation_id] = event_released.get(allocation_id, 0) + amount
    transfer_events = list(PointsThirdTransferRevision.objects.filter(order=order).order_by('created_at', 'pk'))
    for event in transfer_events:
        for allocation_id, amount in event.releases.values_list('allocation_id', 'amount'):
            event_released[allocation_id] = event_released.get(allocation_id, 0) + amount
    from django.db.models import Q
    allocations = {item.pk: item for item in OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order))}
    items = []
    for allocation_id, amount, prior in current['allocations']:
        allocation = allocations.get(allocation_id)
        net = amount - prior - released.get(allocation_id, 0) - event_released.get(allocation_id, 0)
        if not allocation or allocation.amount != amount or net < 0:
            raise SettlementError('第三轮退款后的到账余额异常')
        items.append({'id': allocation.pk, 'receipt_id': allocation.receipt_id, 'amount': net})
    for allocation in allocations.values():
        if allocation.pk not in {item[0] for item in current['allocations']}:
            net = allocation.amount - event_released.get(allocation.pk, 0)
            if net < 0:
                raise SettlementError('第三轮后新增到账释放超额')
            items.append({'id': allocation.pk, 'receipt_id': allocation.receipt_id, 'amount': net})
    items.sort(key=lambda item: item['id'])
    addon_parts, addon_totals, addon_refunds = [], {}, []
    from .models import OrderAddonPayment
    if _allow_addons and OrderAddonPayment.objects.filter(intent__order=order).exists():
        from .addon_payment import verify_receipt, verify_order_debit_links
        from .addon_handover import assert_no_pending_handover
        from .points_third_addon_responsibilities import current as current_responsibilities
        from .addon_completion import verify_accepted
        from .addon_main_segment_deduction import verified_deductions
        history = _addon_history_locked(order, version)
        keys = ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                'provider_units', 'inviter_units', 'recovery_units')
        addon_totals = {key: 0 for key in keys}
        eligible = {(segment_id, person.account_id) for person in plan.persons for segment_id in person.segment_ids}
        deductions = {(row.payment_id, row.segment_id, row.addon_share_id): row.amount
                      for row in version.posting.shares.get().addon_deductions.all()}
        history_ids = [item.get('payment_id') for item in history if isinstance(item, dict)]
        actual_ids = set(OrderAddonPayment.objects.filter(intent__order=order).values_list('pk', flat=True))
        if len(history_ids) != len(history) or len(set(history_ids)) != len(history_ids) or set(history_ids) != actual_ids:
            raise SettlementError('第三轮加购付款历史不完整')
        verify_order_debit_links(order, table_exists=True, lock=True)
        for expected in history:
            payment = OrderAddonPayment.objects.select_for_update().select_related('intent').filter(
                pk=expected['payment_id'], intent__order=order).first()
            if not payment:
                raise SettlementError('第三轮加购付款凭证缺失')
            verify_receipt(payment)
            assert_no_pending_handover(payment)
            handover, responsibilities = current_responsibilities(payment, lock=True, with_share_id=True)
            refunds = list(payment.refund_records.order_by('created_at', 'pk'))
            payment_deductions = {key: amount for key, amount in deductions.items() if key[0] == payment.pk}
            net_expected = expected['provider_units'] - sum(payment_deductions.values())
            if ([item.pk for item in refunds] != expected['refund_ids']
                    or sum(part['basis'] for part in responsibilities) != expected['paid_units']
                    or net_expected < 0 or sum(part['income'] for part in responsibilities) != net_expected
                    or (handover.pk if handover else None) != expected['handover_id']):
                raise SettlementError('第三轮加购责任与实际资金历史不一致')
            addon_refunds.extend(item.pk for item in refunds)
            for key in payment_deductions:
                deductions.pop(key)
            for part in responsibilities:
                amount = part['income']
                if amount < 0 or (part['segment_id'], part['provider_id']) not in eligible:
                    raise SettlementError('第三轮加购剩余收益或履约段异常')
                endings = [event for event in (version.transfer_revision.first_posting,
                    version.transfer_revision.second_posting, version.posting)
                    if part['provider_id'] in event.proposal.affected_accounts
                    and (not event.proposal.affected_segment_ids or part['segment_id'] in event.proposal.affected_segment_ids)]
                if len(endings) > 1:
                    raise SettlementError('第三轮加购履约终止记录不唯一')
                if endings and part['is_current']:
                    verify_accepted(order, payment, endings[0], part)
                addon_parts.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                    'provider_account_id': part['provider_id'], 'provider_income_units': amount})
            for key in keys:
                addon_totals[key] += expected[key]
        if deductions:
            raise SettlementError('第三轮加购扣减没有真实份额')
        merged = {}
        for part in addon_parts:
            key = (part['payment_id'], part['segment_id'], part['provider_account_id'])
            if key not in merged:
                merged[key] = dict(part)
            else:
                merged[key]['provider_income_units'] += part['provider_income_units']
        addon_parts = list(merged.values())
        addon_totals['provider_units'] = sum(part['provider_income_units'] for part in addon_parts)
        main_deductions = verified_deductions(order)
    if (sum(item['amount'] for item in items) != funding.required_units
            or funding.net_principal_units + funding.planned_provider_external_units + funding.required_units
               != sum(person.income for person in plan.persons)
                  + sum(part['provider_income_units'] for part in addon_parts) + plan.inviter_income + plan.store_income):
        raise SettlementError('第三轮退款后的补资与逐人收益不守恒')
    calculation = asdict(funding)
    for key in ('allocated_units', 'release_units', 'remaining_units'):
        calculation.pop(key)
    calculation['additional_store_funding_units'] = funding.required_units
    terms = {'calculation': calculation, 'allocations': items,
             'refund_posting_id': version.transfer_revision.first_posting_id,
             'followup_posting_id': version.transfer_revision.second_posting_id,
             'third_posting_id': version.posting_id, 'third_version_id': version.pk}
    if addon_totals:
        terms.update(addons=addon_parts, addon_history=history, addon_totals=addon_totals,
                     addon_refund_ids=addon_refunds,
                     main_segment_deductions=[[key, value] for key, value in sorted(main_deductions.items())])
    if events:
        terms['third_addon_rounding'] = [[event.refund_id, event.refund.rounding_surplus]
                                         for event in events if event.refund_id and event.refund.rounding_surplus]
    if transfer_events:
        terms['third_transfer_versions'] = [[event.pk, event.evidence_hash] for event in transfer_events]
    return plan, _terms(order, plan, points_funding=terms)


def verify_manifest_locked(order, manifest, terms, *, finalized=False):
    """Rebuild the same third-round plan before using a real manifest."""
    from .models import PointsRefundRoundEvidence
    from .segment_settlement import _hash
    if (not transaction.get_connection().in_atomic_block or manifest.order_id != order.pk
            or manifest.plan != terms or manifest.plan_hash != _hash(terms)):
        raise SettlementError('第三轮结算清单与资金版本不一致')
    version = PointsRefundRoundEvidence.objects.select_related('posting__points_refund__refund').get(order=order)
    if (not version.posting.termination_processed_at or manifest.created_at < version.posting.termination_processed_at
            or (finalized and (not manifest.finalized_at or order.settled_at != manifest.finalized_at))
            or (not finalized and manifest.finalized_at)):
        raise SettlementError('第三轮结算清单时序异常')
    _, expected = prepare_locked(order.pk, allow_finalized=finalized)
    if expected != terms:
        raise SettlementError('第三轮结算清单的实际资金已变化')
