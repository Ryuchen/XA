"""Preflight for the next points refund on an active service segment.

Internal only: this does not confirm a proposal or authorize payment.  The
caller holds the same order/money transaction until its proposal is stored.
"""
from dataclasses import asdict

from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def preview_locked(case_id, *, refund_equivalent_units, _allow_unsettled_addons=True):
    from .financial_lock import lock_order_money
    from .models import (AfterSale, Order, OrderAddonPayment, OrderParticipationSegment,
                         OrderSegmentPersonSettlement, OrderSegmentSettlementManifest,
                         PointsFollowupTransferRevision, OrderPointsFundingReleaseEvidence,
                         OrderOriginalShareTransfer)
    from .points_transfer_refund_access import permits
    from .points_transfer_followup_funding import plan_locked
    from .points_followup_transfer_revision import verify_locked
    from .points_funding_plan import PointsFundingPlanError
    from .points_service_refund import _plan
    from .points_negotiation_preview import funding_after_refund
    from .refund_math import provider_refund_burden, RefundCalculationError
    from engagement.confirmation import aftersale_deadline
    from users.models import EscortProfile
    from wallet.models import Wallet

    if not transaction.get_connection().in_atomic_block or not permits(phase='refund'):
        raise ValidationError('第三轮退款预核须在积分订单资金事务内执行')
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order or not permits(order, phase='refund'):
        raise ValidationError('第三轮退款订单上下文不一致')
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    lock_order_money()
    if (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED) or order.settled_at
            or order.refund_records.count() < 3 or order.source_order_id
            or Order.objects.filter(source_order_id=order.pk).exists()
            or (not _allow_unsettled_addons and OrderAddonPayment.objects.filter(intent__order=order).exists())
            or case.status != 'OPEN' or case.account_id != order.customer_account_id
            or not order.in_service_at or not case.created_at or case.created_at < order.in_service_at
            or case.created_at > timezone.now()):
        raise ValidationError('第三轮退款订单或售后状态待核对')
    if order.status == Order.Status.COMPLETED:
        deadline = aftersale_deadline(order)
        if not order.completed_at or not deadline or case.created_at >= deadline:
            raise ValidationError('第三轮售后不在有效期限内')
    from .points_round_paid_settlement import capture_locked
    paid_snapshot = capture_locked(order, case)
    from .points_round_funding import current_locked
    from .settlement import SettlementError
    try:
        state = current_locked(order)
    except SettlementError as exc:
        raise ValidationError('多轮退款资金来源待核对') from exc
    version = state['round']
    transfer_version = state['transfer']
    addon_version = state['addon']
    affected = case.affected_segment_ids
    if (not isinstance(affected, list) or not affected or len(set(affected)) != len(affected)
            or any(type(pk) is not int or pk <= 0 for pk in affected)):
        raise ValidationError('须明确选择受影响的履约段')
    segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        pk__in=affected, attribution__order=order).order_by('provider_account_id', 'pk'))
    closed = state['closed_segment_ids']
    if (len(segments) != len(affected) or any(not segment.started_at
            or segment.accepted_at > segment.started_at or segment.started_at > case.created_at
            or segment.pk in closed for segment in segments)):
        raise ValidationError('受影响履约段已终止或缺少履约事实')
    retained_weights = {}
    for segment in segments:
        if segment.released_at:
            transfer = OrderOriginalShareTransfer.objects.select_for_update().filter(
                previous_segment=segment, previous_segment__attribution__order=order).first()
            if (order.status != Order.Status.COMPLETED or not transfer
                    or transfer.created_at != segment.released_at or transfer.created_at > case.created_at
                    or transfer.retained_share <= 0):
                raise ValidationError('转出履约段的留存份额或售后时点待核对')
            retained_weights[segment.pk] = transfer.retained_share
        else:
            retained_weights[segment.pk] = segment.original_share
    accounts = sorted({segment.provider_account_id for segment in segments})
    if sorted(case.affected_provider_account_ids or []) != accounts:
        raise ValidationError('受影响陪玩与所选履约段不一致')
    incomes = state['segment_income']
    if any(type(incomes.get(segment.pk)) is not int or incomes[segment.pk] < 0
           or not retained_weights[segment.pk] for segment in segments):
        raise ValidationError('受影响段实际收益待核对')
    weights = {account_id: sum(retained_weights[segment.pk] for segment in segments
                                if segment.provider_account_id == account_id) for account_id in accounts}
    try:
        prior = state['funding']
        receipt, assets, sources = _plan(order, None, refund_equivalent_units)
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units,
                                        original_price_weights=weights)
    except (PointsFundingPlanError, RefundCalculationError) as exc:
        raise ValidationError('原付款、历史补资或承担待核对') from exc
    from club_accounts.models import LegacyAccountMap
    account_state = {}
    for account_id in accounts:
        own = [segment for segment in segments if segment.provider_account_id == account_id]
        assignments = order.providers.select_for_update().filter(provider_account_id=account_id,
                                                                     settled_at__isnull=True)
        if order.status != Order.Status.COMPLETED:
            assignments = assignments.filter(released_at__isnull=True)
        assignments = list(assignments)
        profiles = list(EscortProfile.objects.select_for_update().filter(account_id=account_id))
        if (len(assignments) != 1 or len(profiles) != 1 or not assignments[0].provider_id
                or profiles[0].user_id != assignments[0].provider_id or profiles[0].deposit_paid < 0
                or any(assignments[0].provider_business_cycle != item.provider_business_cycle for item in own)
                or not LegacyAccountMap.objects.filter(account_id=account_id,
                    legacy_user_id=assignments[0].provider_id).exists()):
            raise ValidationError('陪玩账号或业务周期待核对')
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id=account_id) | Q(user_id=assignments[0].provider_id)).order_by('pk'))
        if (len(wallets) > 1 or (wallets and (not wallets[0].is_active or wallets[0].balance < 0
                or wallets[0].account_id not in (None, account_id)
                or wallets[0].user_id not in (None, assignments[0].provider_id)))):
            raise ValidationError('陪玩钱包待核对')
        account_state[account_id] = {'wallet': wallets[0].balance if wallets else 0,
                                     'deposit': profiles[0].deposit_paid}
    selected_ids = {item.pk for item in segments}
    # Other earned stints belonging to the same affected person can fund the
    # refund without changing their original termination agreement.
    earning_segments = list(OrderParticipationSegment.objects.select_for_update().filter(
        pk__in=incomes, attribution__order=order, provider_account_id__in=accounts,
        started_at__isnull=False).order_by('provider_account_id', 'pk'))
    for item in earning_segments:
        if (type(incomes.get(item.pk)) is not int or incomes[item.pk] < 0
                or item.provider_business_cycle != next(segment.provider_business_cycle for segment in segments
                    if segment.provider_account_id == item.provider_account_id)):
            raise ValidationError('本人其他未结收益的履约周期待核对')
    earning_ids = {item.pk for item in earning_segments}
    if not selected_ids.issubset(earning_ids):
        raise ValidationError('受影响段收益来源待核对')
    addon_rows = []
    pending_handovers = []
    if _allow_unsettled_addons:
        from .addon_payment import verify_order_debit_links, verify_receipt
        from .points_third_handover_invalidation import permits_for_round_locked
        from .points_third_addon_responsibilities import current as current_responsibilities
        from .models import OrderAddonSettlement
        payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                        .select_related('intent').order_by('pk'))
        if payments:
            verify_order_debit_links(order, table_exists=True, lock=True)
        for payment in payments:
            verify_receipt(payment)
            pending = list(payment.handovers.select_for_update().filter(status='PENDING').order_by('pk'))
            previous_handover, responsibilities = current_responsibilities(payment, lock=True, with_share_id=True)
            for handover in pending:
                if handover.target_segment_id not in selected_ids:
                    if any(part['segment_id'] in earning_ids and part['income'] > 0 for part in responsibilities):
                        raise ValidationError('待确认接替涉及本人可能承担的加购收益，请先处理')
                    continue
                if not permits_for_round_locked(order, handover):
                    raise ValidationError('加购负责人变更正在等待确认，请先处理')
                active = [part for part in responsibilities if part['is_current']]
                if ((previous_handover.pk if previous_handover else None) != handover.previous_id
                        or len(active) != 1 or active[0]['segment_id'] != handover.source_segment_id
                        or active[0]['basis'] != handover.source_basis_before):
                    raise ValidationError('待确认接替的原责任已变化')
                decisions = [[row.pk, row.account_id, row.role, row.accepted,
                              row.decided_at.isoformat() if row.decided_at else None]
                             for row in handover.decisions.select_for_update().order_by('pk')]
                pending_handovers.append([handover.pk, payment.pk, handover.source_segment_id,
                    handover.target_segment_id, handover.previous_id, handover.source_basis_before,
                    handover.created_at.isoformat(), decisions])
            for part in responsibilities:
                if part['provider_id'] not in accounts or not part['income']:
                    continue
                from .models import OrderSegmentAddonSettlement
                if (OrderAddonSettlement.objects.filter(payment=payment, segment_id=part['segment_id']).exists()
                        or OrderSegmentAddonSettlement.objects.filter(payment=payment, segment_id=part['segment_id']).exists()):
                    continue
                if part['segment_id'] not in earning_ids:
                    raise ValidationError('本人其他未结履约段的加购收益须先核对')
                addon_rows.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                    'addon_share_id': part.get('share_id'), 'income_before': part['income']})
    segment_pending_plan, addon_pending_plan, provider_terms, provider_recoveries = [], [], [], []
    for account_id in accounts:
        own_segments = sorted((item for item in earning_segments if item.provider_account_id == account_id),
                              key=lambda item: (item.pk not in selected_ids, item.pk))
        own_addons = sorted((item for item in addon_rows if item['segment_id'] in
                             {segment.pk for segment in own_segments}),
                            key=lambda row: (row['payment_id'], row['segment_id'], row['addon_share_id'] or 0))
        total_income = sum(incomes[item.pk] for item in own_segments) + sum(row['income_before'] for row in own_addons)
        amount = burden.amounts[account_id]
        pending = min(amount, total_income)
        remaining = pending
        for item in own_segments:
            take = min(incomes[item.pk], remaining)
            if take:
                segment_pending_plan.append({'account_id': account_id, 'segment_id': item.pk, 'amount': take})
                remaining -= take
        for row in own_addons:
            take = min(row['income_before'], remaining)
            if take:
                addon_pending_plan.append({**row, 'amount': take})
                remaining -= take
        wallet = min(amount - pending, account_state[account_id]['wallet'])
        deposit = min(amount - pending - wallet, account_state[account_id]['deposit'])
        provider_terms.append({'account_id': account_id, 'original_income_units': total_income,
            'burden_units': amount, 'retained_units': max(total_income - amount, 0),
            'additional_units': max(amount - total_income, 0)})
        provider_recoveries.append({'account_id': account_id, 'pending_units': pending,
            'wallet_available_before': account_state[account_id]['wallet'],
            'deposit_paid_before': account_state[account_id]['deposit'], 'wallet_units': wallet,
            'deposit_units': deposit, 'shortfall_units': amount - pending - wallet - deposit})
    pending = sum(item['pending_units'] for item in provider_recoveries)
    wallet = sum(item['wallet_units'] for item in provider_recoveries)
    deposit = sum(item['deposit_units'] for item in provider_recoveries)
    shortfall = sum(item['shortfall_units'] for item in provider_recoveries)
    amount = sum(burden.amounts.values())
    rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    funding = funding_after_refund(principal_units=prior.net_principal_units, gift_units=prior.net_gift_units,
        compensation_units=prior.net_compensation_units, returned_prepaid_units=sources.prepaid_units,
        returned_gift_units=sources.gift_units, returned_compensation_units=sources.compensation_units,
        provider_income_units=prior.provider_income_units, pending_deduction_units=pending,
        provider_external_units=prior.planned_provider_external_units + wallet + deposit,
        inviter_rate=rate, allocated_units=prior.allocated_units,
        rounding_reserved_units=prior.rounding_reserved_units + burden.rounding_to_store_units)
    # Preserve each actual allocation and every already-recorded release.  The
    # caller holds the order/money locks; the shared receipt verifier obtains
    # the receipt locks before any new allocation or release can be written.
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    allocation_rows = state['allocations']
    try:
        if sum(amount - released for _, amount, released in allocation_rows) != prior.allocated_units:
            raise ValidationError('历史分配与实际释放不一致')
        release_parts = plan_release(allocations=allocation_rows, amount=funding.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('到账分配或释放待核对') from exc
    pending_handovers.sort(key=lambda item: item[0])
    return {'order_id': order.pk, 'case_id': case.pk, 'prior_version_id': version.transfer_revision_id,
        'segment_id': segments[0].pk if len(segments) == 1 else None,
        'account_id': accounts[0] if len(accounts) == 1 else None, 'redemption_id': receipt.pk,
        'prior_refund_id': version.posting.points_refund_id,
        'prior_round_id': version.pk, 'prior_round_hash': version.evidence_hash,
        'prior_funding': asdict(prior), 'paid_snapshot': paid_snapshot,
        'pending_handovers': sorted(pending_handovers, key=lambda row: row[0]),
        'previous_transfer_id': transfer_version.pk if transfer_version else None,
        'previous_transfer_hash': transfer_version.evidence_hash if transfer_version else None,
        'latest_transfer_version_id': transfer_version.pk if transfer_version else None,
        'latest_transfer_version_hash': transfer_version.evidence_hash if transfer_version else None,
        'previous_addon_id': addon_version.pk if addon_version else None,
        'previous_addon_hash': addon_version.evidence_hash if addon_version else None,
        'refund_equivalent_units': refund_equivalent_units,
        'coin_units': assets.coin_units, 'points_hundredths': assets.point_hundredths,
        'is_final': assets.is_final, 'returned_prepaid_units': sources.prepaid_units,
        'returned_gift_units': sources.gift_units, 'returned_compensation_units': sources.compensation_units,
        'segment_pending_plan': segment_pending_plan,
        **({'addon_pending_plan': addon_pending_plan} if _allow_unsettled_addons and payments else {}),
        'provider_terms': provider_terms, 'provider_recoveries': provider_recoveries,
        'sources': asdict(sources), 'burden_units': amount, 'pending_units': pending,
        'wallet_available_before': sum(row['wallet_available_before'] for row in provider_recoveries),
        'deposit_paid_before': sum(row['deposit_paid_before'] for row in provider_recoveries),
        'wallet_units': wallet, 'deposit_units': deposit, 'shortfall_units': shortfall,
        'provider_recovery_preview': {'pending_units': pending, 'wallet_units': wallet,
            'deposit_units': deposit, 'shortfall_units': shortfall},
        'estimated_sufficient': shortfall == 0,
        'rounding_to_store_units': burden.rounding_to_store_units, 'funding': asdict(funding),
        'allocations': allocation_rows,
        'releases': sorted([[part.allocation_id, part.amount] for part in release_parts])}


def available_for_case(case, *, refund_units=10):
    """Expose only a real single-person plan with sufficient funding sources."""
    try:
        result = preview_locked(case.pk, refund_equivalent_units=refund_units)
        if result['deposit_units']:
            from wallet.refund_deposit import enabled as deposit_enabled
            from wallet.refund_recovery_order import enabled as recovery_enabled
            if not deposit_enabled() or not recovery_enabled():
                return False
        return not result['shortfall_units']
    except ValidationError:
        return False


