"""Preflight for a third points refund on a genuinely new service segment.

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
                         PointsFollowupTransferRevision, OrderPointsFundingReleaseEvidence)
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
            or order.refund_records.count() != 2 or order.source_order_id
            or Order.objects.filter(source_order_id=order.pk).exists()
            or (not _allow_unsettled_addons and OrderAddonPayment.objects.filter(intent__order=order).exists())
            or OrderSegmentPersonSettlement.objects.filter(manifest__order=order).exists()
            or OrderSegmentSettlementManifest.objects.filter(order=order).exists()
            or case.status != 'OPEN' or case.account_id != order.customer_account_id
            or not order.in_service_at or not case.created_at or case.created_at < order.in_service_at
            or case.created_at > timezone.now()):
        raise ValidationError('第三轮退款订单或售后状态待核对')
    if order.status == Order.Status.COMPLETED:
        deadline = aftersale_deadline(order)
        if not order.completed_at or not deadline or case.created_at >= deadline:
            raise ValidationError('第三轮售后不在有效期限内')
    versions = list(PointsFollowupTransferRevision.objects.select_for_update().filter(order=order)
                    .select_related('transfer__next_segment').order_by('pk')[:2])
    if len(versions) != 1:
        raise ValidationError('第三轮退款缺少唯一真实转单资金版本')
    version = verify_locked(versions[0])
    if not OrderPointsFundingReleaseEvidence.objects.filter(posting=version.first_posting).exists():
        raise ValidationError('第三轮退款缺少首轮实际到账资金版本')
    segment = OrderParticipationSegment.objects.select_for_update().filter(
        pk=version.transfer.next_segment_id, attribution__order=order).first()
    if (not segment or not segment.started_at or segment.released_at
            or segment.accepted_at > segment.started_at or segment.started_at > case.created_at
            or case.affected_segment_ids != [segment.pk]
            or case.affected_provider_account_ids != [segment.provider_account_id]):
        raise ValidationError('第三轮售后须明确选择仍在履约的新转入段')
    from .aftersale_segment_scope import closed_segments_locked
    closed = closed_segments_locked(order)
    if closed is None or segment.pk in closed:
        raise ValidationError('已协商终止履约段不能重开')
    from .points_transfer_refund_access import _verify_prestart_history
    from .models import OrderOriginalShareTransfer
    transfers = list(OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).select_related('previous_segment').order_by('pk'))
    _verify_prestart_history(order, transfers)
    counts = {}
    for item in transfers:
        counts[item.previous_segment.slot] = counts.get(item.previous_segment.slot, 0) + 1
    if any(count > 2 for count in counts.values()):
        raise ValidationError('同一席位转单次数超过上限')
    incomes = version.snapshot.get('segment_income') if isinstance(version.snapshot, dict) else None
    if (not isinstance(incomes, list) or any(not isinstance(item, list) or len(item) != 2 for item in incomes)
            or len({item[0] for item in incomes}) != len(incomes)):
        raise ValidationError('转单逐段收益版本异常')
    income = dict(incomes).get(segment.pk)
    if type(income) is not int or income < 0 or not segment.original_share:
        raise ValidationError('新转入段实际收益待核对')
    try:
        prior = plan_locked(order.pk, allow_in_service=True)
        receipt, assets, sources = _plan(order, None, refund_equivalent_units)
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units,
            original_price_weights={segment.provider_account_id: segment.original_share})
    except (PointsFundingPlanError, RefundCalculationError) as exc:
        raise ValidationError('第三轮原付款、历史补资或承担待核对') from exc
    assignments = list(order.providers.select_for_update().filter(provider_account_id=segment.provider_account_id,
                                                                  released_at__isnull=True))
    profiles = list(EscortProfile.objects.select_for_update().filter(account_id=segment.provider_account_id))
    if (len(assignments) != 1 or len(profiles) != 1 or not assignments[0].provider_id
            or profiles[0].user_id != assignments[0].provider_id
            or profiles[0].deposit_paid < 0
            or assignments[0].provider_business_cycle != segment.provider_business_cycle):
        raise ValidationError('第三轮陪玩账号或钱包归属待核对')
    from club_accounts.models import LegacyAccountMap
    if not LegacyAccountMap.objects.filter(account_id=segment.provider_account_id,
                                            legacy_user_id=assignments[0].provider_id).exists():
        raise ValidationError('第三轮陪玩业务账号与历史身份不一致')
    wallets = list(Wallet.objects.select_for_update().filter(
        Q(account_id=segment.provider_account_id) | Q(user_id=assignments[0].provider_id)).order_by('pk'))
    if (len(wallets) > 1 or (wallets and (not wallets[0].is_active or wallets[0].balance < 0
            or wallets[0].account_id not in (None, segment.provider_account_id)
            or wallets[0].user_id not in (None, assignments[0].provider_id)))):
        raise ValidationError('第三轮陪玩钱包待核对')
    amount = burden.amounts[segment.provider_account_id]
    addon_rows = []
    if _allow_unsettled_addons:
        from .addon_payment import verify_order_debit_links, verify_receipt
        from .addon_handover import assert_no_pending_handover, current_responsibilities
        from .models import OrderAddonSettlement
        payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                        .select_related('intent').order_by('pk'))
        if payments:
            verify_order_debit_links(order, table_exists=True, lock=True)
        for payment in payments:
            verify_receipt(payment)
            assert_no_pending_handover(payment)
            _, responsibilities = current_responsibilities(payment, lock=True, with_share_id=True)
            for part in responsibilities:
                if part['provider_id'] != segment.provider_account_id or not part['income']:
                    continue
                if OrderAddonSettlement.objects.filter(payment=payment, segment_id=part['segment_id']).exists():
                    continue
                # Closed segments have an already-confirmed retained amount;
                # they cannot be silently reopened by this proposal.
                if part['segment_id'] in closed:
                    continue
                if part['segment_id'] != segment.pk:
                    raise ValidationError('本人其他未结履约段的加购收益须先核对')
                addon_rows.append({'payment_id': payment.pk, 'segment_id': part['segment_id'],
                    'addon_share_id': part.get('share_id'), 'income_before': part['income']})
        if payments:
            other_incomes = [item for item in version.snapshot['segment_income'] if item[0] != segment.pk and item[1] > 0]
            other_segments = {item['id']: item for item in version.snapshot['facts']['segments']}
            if any(other_segments.get(pk, {}).get('account_id') == segment.provider_account_id and pk not in closed
                   for pk, _ in other_incomes):
                raise ValidationError('本人其他未结履约段的主单收益须先核对')
    total_income = income + sum(item['income_before'] for item in addon_rows)
    pending = min(amount, total_income)
    main_pending = min(pending, income)
    addon_pending_plan = []
    remaining_pending = pending - main_pending
    for item in addon_rows:
        deduction = min(item['income_before'], remaining_pending)
        if deduction:
            addon_pending_plan.append({**item, 'amount': deduction})
            remaining_pending -= deduction
    wallet = min(amount - pending, wallets[0].balance if wallets else 0)
    deposit = min(amount - pending - wallet, profiles[0].deposit_paid)
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
    from .points_funding_evidence import verified_order_allocations_locked
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .models import (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                         PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
                         OrderAddonFundingRelease)
    try:
        allocations = verified_order_allocations_locked(order)
        allocation_rows = []
        for allocation in allocations:
            released = sum(model.objects.filter(allocation=allocation).aggregate(total=Sum('amount'))['total'] or 0
                for model in (OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                              PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
                              OrderAddonFundingRelease))
            allocation_rows.append([allocation.pk, allocation.amount, released])
        if sum(amount - released for _, amount, released in allocation_rows) != prior.allocated_units:
            raise ValidationError('第三轮历史分配与实际释放不一致')
        release_parts = plan_release(allocations=allocation_rows, amount=funding.release_units)
    except (PointsFundingPlanError, PointsFundingAllocationError) as exc:
        raise ValidationError('第三轮到账分配或释放待核对') from exc
    return {'order_id': order.pk, 'case_id': case.pk, 'prior_version_id': version.pk,
        'segment_id': segment.pk, 'account_id': segment.provider_account_id, 'redemption_id': receipt.pk,
        'prior_refund_id': version.second_posting.points_refund_id,
        'refund_equivalent_units': refund_equivalent_units,
        'coin_units': assets.coin_units, 'points_hundredths': assets.point_hundredths,
        'is_final': assets.is_final, 'returned_prepaid_units': sources.prepaid_units,
        'returned_gift_units': sources.gift_units, 'returned_compensation_units': sources.compensation_units,
        'segment_pending_plan': ([{'account_id': segment.provider_account_id,
            'segment_id': segment.pk, 'amount': main_pending}] if main_pending else []),
        **({'addon_pending_plan': addon_pending_plan} if _allow_unsettled_addons and payments else {}),
        'provider_terms': [{'account_id': segment.provider_account_id, 'original_income_units': total_income,
            'burden_units': amount, 'retained_units': max(total_income - amount, 0),
            'additional_units': max(amount - total_income, 0)}],
        'sources': asdict(sources), 'burden_units': amount, 'pending_units': pending,
        'wallet_available_before': wallets[0].balance if wallets else 0,
        'deposit_paid_before': profiles[0].deposit_paid,
        'wallet_units': wallet, 'deposit_units': deposit, 'shortfall_units': amount - pending - wallet - deposit,
        'provider_recovery_preview': {'pending_units': pending, 'wallet_units': wallet,
            'deposit_units': deposit, 'shortfall_units': amount - pending - wallet - deposit},
        'estimated_sufficient': amount == pending + wallet + deposit,
        'rounding_to_store_units': burden.rounding_to_store_units, 'funding': asdict(funding),
        'allocations': allocation_rows,
        'releases': sorted([[part.allocation_id, part.amount] for part in release_parts])}


def available_for_case(case, *, refund_units=10):
    """Recognize only the third-round slice with an executable funding plan."""
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
