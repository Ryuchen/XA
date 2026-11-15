"""Verify a different provider's later aftersale on a transferred points order.

This calculation is read-only and is not consent to, or execution of, a
refund. All balances and provider recovery remain conditional until posted.
"""
from dataclasses import asdict

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from club_accounts.models import LegacyAccountMap
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import Transaction, Wallet
from .models import (AfterSale, Order, OrderPointsFundingRelease, OrderSegmentSettlementManifest)
from .points_checkout_test import require_isolated_checkout
from .points_funding_allocation import PointsFundingAllocationError
from .points_funding_plan import PointsFundingPlanError
from .refund_math import RefundCalculationError, provider_refund_burden
from .settlement import SettlementError


def internal_agreement_enabled():
    """Exercise consent only in disposable tests until posting is connected."""
    from .points_transfer_refund_access import permits
    if permits(phase='refund') or permits(phase='settlement'):
        return True
    if not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_INTERNAL_AGREEMENT_TESTING', False):
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    from django.db import connections
    directory = connections['default'].settings_dict
    if (directory.get('ENGINE') != 'django.db.backends.sqlite3'
            or directory.get('NAME') not in (':memory:', 'file:memorydb_default?mode=memory&cache=shared')):
        return False
    from .points_negotiation_preview import agreement_enabled
    return bool(agreement_enabled() and getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_PREVIEW_TESTING', False))


def preview_locked(case_id, *, refund_equivalent_units):
    from .points_transfer_refund_access import permits
    formal = permits(phase='refund')
    if not formal:
        require_isolated_checkout()
    if ((not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_FOLLOWUP_PREVIEW_TESTING', False))
            or not transaction.get_connection().in_atomic_block
            or type(case_id) is not int or case_id <= 0
            or type(refund_equivalent_units) is not int or refund_equivalent_units <= 0):
        raise ValidationError('转单后的追加售后核对尚未开放')
    order_id = AfterSale.objects.filter(pk=case_id).values_list('order_id', flat=True).first()
    if not order_id:
        raise ValidationError('售后不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    if formal and not permits(order, phase='refund'):
        raise ValidationError('转单追加退款资金上下文与订单不一致')
    case = AfterSale.objects.select_for_update().get(pk=case_id, order=order)
    from .financial_lock import lock_order_money
    lock_order_money()
    scope = case.affected_provider_account_ids
    from engagement.confirmation import aftersale_deadline
    deadline = aftersale_deadline(order)
    in_service = order.status == Order.Status.IN_SERVICE
    if (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.settled_at or order.refund_records.count() != 1
            or order.source_order_id or Order.objects.filter(source_order_id=order.pk).exists()
            or case.status != 'OPEN' or case.account_id != order.customer_account_id
            or not order.customer_id or not order.in_service_at
            or not case.created_at or case.created_at < order.in_service_at or case.created_at > timezone.now()
            or (not in_service and (not order.completed_at or not deadline or case.created_at >= deadline))
            or not isinstance(scope, list) or not scope
            or any(type(account) is not int or account <= 0 for account in scope)
            or len(set(scope)) != len(scope)
            or not LegacyAccountMap.objects.filter(account_id=order.customer_account_id,
                                                   legacy_user_id=order.customer_id).exists()):
        raise ValidationError('订单、售后期限或受影响陪玩待核对')
    from .negotiation_aftersale_scope import closed_provider_accounts
    closed_accounts = closed_provider_accounts(order)
    selected_segments = set(case.affected_segment_ids or [])
    if set(scope) & closed_accounts:
        from .aftersale_segment_scope import closed_segments_locked
        closed_segments = closed_segments_locked(order)
        if not selected_segments or not closed_segments or selected_segments & closed_segments:
            raise ValidationError('已确认的服务范围不能重开')
    from .points_transfer_refund_continuation import verify_locked
    from .points_transfer_refund_evidence import reconstruct_funding_locked, verify_transfer_revisions_after_refund_locked
    from .points_transfer_refunded_plan import build_refunded_plan
    from .points_funding_evidence import verified_order_allocations_locked, verify_order_funding
    from .points_service_refund import _plan as payment_refund_plan
    try:
        posting = verify_locked(order)
        if formal:
            from .points_transfer_followup_scope import supported_addon, addon_chain_before
            from .models import OrderAddonPayment
            if OrderAddonPayment.objects.filter(intent__order=order).exists() and not supported_addon(order, posting):
                addon_chain_before(order, posting, timezone.now())
        plan, current = build_refunded_plan(order, allow_in_service=in_service)
        funding = verify_order_funding(order.pk, allow_finalized=True)
        allocations = verified_order_allocations_locked(order)
        receipt, assets, sources = payment_refund_plan(order, None, refund_equivalent_units)
        from .models import PointsPostRefundTransferRevision
        version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
        if version:
            from .points_post_refund_transfer import verify_locked as verify_transfer_version
            from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
            verify_transfer_version(version)
            facts = version.snapshot['facts']
            history = _reconstruct_from_verified_facts(order.pk, facts,
                refund_id=posting.points_refund.refund_id, through_transfer_id=version.transfer_id)
        else:
            facts = posting.participants['facts']
            history = reconstruct_funding_locked(order.pk, posting.participants,
                refund_id=posting.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'])
        released = verify_transfer_revisions_after_refund_locked(order.pk, posting.participants,
            refund_id=posting.points_refund.refund_id)
    except (ValidationError, PointsFundingPlanError, SettlementError, RefundCalculationError, KeyError, IndexError) as exc:
        raise ValidationError('首次退款、履约或补资依据待核对') from exc
    if (receipt.pk != posting.points_refund.redemption_id or posting.points_refund.is_final
            or funding.required != current.required_units):
        raise ValidationError('首次退款后的资金依据不一致')
    persons = {item.account_id: item for item in plan.persons}
    rows_by_account = {account: [row for row in facts['segments']
        if row['account_id'] == account and row['started_at']
        and (not selected_segments or row['id'] in selected_segments)] for account in scope}
    if (len(persons) != len(plan.persons) or any(account not in persons or not rows_by_account[account]
            for account in scope) or any(not row['started_at'] and row['original_share'] > 0
            for row in facts['segments'] if row['account_id'] in scope)):
        raise ValidationError('本次陪玩的履约份额待核对')
    records = {}
    manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
    if manifest:
        from .models import OrderSegmentPersonSettlement
        if not OrderSegmentPersonSettlement.objects.filter(manifest=manifest).exists():
            raise ValidationError('追加退款前的结算清单缺少实际已付人员')
    if manifest:
        from .segment_settlement import _hash, _points_transfer_terms, _terms, _validate_records
        try:
            current_plan, points_terms = _points_transfer_terms(order)
            expected = _terms(order, current_plan, points_funding=points_terms) if points_terms is not None else None
            prior = facts.get('prior_settlement')
            original_manifest = (prior and manifest.pk == prior['manifest_id'] and manifest.plan == prior['plan']
                                 and manifest.plan_hash == prior['plan_hash'])
            if (manifest.finalized_at or (not original_manifest and
                    (expected is None or manifest.plan != expected or manifest.plan_hash != _hash(expected)))):
                raise SettlementError('结算版本不一致')
            records = _validate_records(order, manifest, plan)
        except (SettlementError, KeyError, TypeError) as exc:
            raise ValidationError('已结算陪玩或结算版本待核对') from exc
    elif (order.providers.filter(settled_at__isnull=False).exists()
          or order.transactions.filter(tx_type__in=(Transaction.TxType.INCOME, Transaction.TxType.SHOP_INCOME),
                                       status=Transaction.Status.SUCCESS).exists()):
        raise ValidationError('订单存在无法归属的收益流水')
    if set(scope) & set(records):
        raise ValidationError('受影响陪玩的收益已结算，请核对售后时序')
    from .models import OrderAddonPayment
    has_paid_addons = OrderAddonPayment.objects.filter(intent__order=order).exists()
    paid_addon_units = 0
    if manifest and has_paid_addons:
        from .points_transfer_refund_evidence import _prior_settlement
        from .points_transfer_paid_addons import verify as verify_paid_addons
        prior_snapshot = _prior_settlement(order)
        paid_addon_units = verify_paid_addons(order, prior_snapshot, cutoff=timezone.now())
    try:
        burden = provider_refund_burden(refund_equivalent_units=refund_equivalent_units,
            original_price_weights={account: sum(row['original_share'] for row in rows_by_account[account])
                                    for account in scope})
    except RefundCalculationError as exc:
        raise ValidationError('追加退款的陪玩承担待核对') from exc
    from .addon_main_segment_deduction import verified_deductions
    prior_addon = verified_deductions(order)
    recoveries, provider_terms, segment_pending_plan = [], [], []
    for account in sorted(scope):
        assignment = order.providers.select_for_update().filter(provider_account_id=account).first()
        profile = EscortProfile.objects.select_for_update().filter(account_id=account).first()
        if (not assignment or not profile or profile.user_id != assignment.provider_id or profile.deposit_paid < 0
                or not LegacyAccountMap.objects.filter(account_id=account, legacy_user_id=assignment.provider_id).exists()):
            raise ValidationError('陪玩身份或押金归属待核对')
        wallets = list(Wallet.objects.select_for_update().filter(
            Q(account_id=account) | Q(user_id=assignment.provider_id)).order_by('pk'))
        if len(wallets) > 1 or (wallets and (not wallets[0].is_active or wallets[0].balance < 0
                or wallets[0].account_id not in (None, account)
                or wallets[0].user_id not in (None, assignment.provider_id))):
            raise ValidationError('陪玩钱包归属或余额待核对')
        target_income = (sum(line[4] - prior_addon.get(line[0], 0) for line in history.segment_shares
                             if line[1] == account and line[0] in selected_segments)
                         if selected_segments else persons[account].income)
        if target_income < 0:
            raise ValidationError('追加退款的履约段剩余收益异常')
        amount = burden.amounts[account]
        pending = min(amount, target_income)
        if len(rows_by_account[account]) > 1 and pending:
            from .addon_main_segment_deduction import remaining_locked
            remaining = pending
            for segment_id, available_income in remaining_locked(order, account):
                if selected_segments and segment_id not in selected_segments:
                    continue
                used = min(remaining, available_income)
                if used:
                    segment_pending_plan.append({'account_id': account, 'segment_id': segment_id, 'amount': used})
                    remaining -= used
                if not remaining:
                    break
            if remaining:
                raise ValidationError('本人所选履约段可用收益不足')
        available = min(amount - pending, wallets[0].balance if wallets else 0)
        deposit = min(amount - pending - available, profile.deposit_paid)
        shortfall = amount - pending - available - deposit
        if formal and shortfall:
            raise ValidationError('陪玩待结算收益、可用余额及押金不足，本次不能退款')
        provider_terms.append({'account_id': account, 'original_income_units': target_income,
            'burden_units': amount, 'retained_units': target_income - pending, 'additional_units': amount - pending})
        recoveries.append({'account_id': account, 'pending_units': pending, 'wallet_units': available,
                           'deposit_units': deposit, 'shortfall_units': shortfall})
    pending = sum(item['pending_units'] for item in recoveries)
    additional = sum(item['additional_units'] for item in provider_terms)
    shortfall = sum(item['shortfall_units'] for item in recoveries)
    shares = list(posting.shares.all())
    first = posting.points_refund
    from .points_negotiation_preview import funding_after_refund
    initial = history.funding
    projected = funding_after_refund(principal_units=initial.principal_units, gift_units=initial.gift_units,
        compensation_units=initial.compensation_units, returned_prepaid_units=first.prepaid_units + sources.prepaid_units,
        returned_gift_units=first.gift_units + sources.gift_units,
        returned_compensation_units=first.compensation_units + sources.compensation_units,
        provider_income_units=initial.provider_income_units,
        pending_deduction_units=sum(row.pending_deduction for row in shares) + pending,
        provider_external_units=sum(row.wallet_deduction + row.deposit_deduction for row in shares) + additional,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=funding.allocated,
        rounding_reserved_units=posting.rounding_to_store_units + burden.rounding_to_store_units)
    if has_paid_addons:
        from .points_transfer_followup_scope import combine_addon
        pending_lines = [(row.account_id, row.pending_deduction,
                          set(row.posting.proposal.affected_segment_ids or [])) for row in shares]
        pending_lines.extend((item['account_id'], item['pending_units'], selected_segments,
            [(part['segment_id'], part['amount']) for part in segment_pending_plan
             if part['account_id'] == item['account_id']] or None) for item in recoveries)
        projected, _ = combine_addon(order, projected, timezone.now(), history.segment_shares,
                                     facts['segments'], pending_lines)
    release_rows = list(OrderPointsFundingRelease.objects.filter(allocation__in=allocations))
    if any(row.posting_id != posting.pk for row in release_rows):
        raise ValidationError('订单存在无法归属的退款补资释放')
    from .models import PointsPostRefundTransferRelease
    from .addon_funding_release import addon_release_totals
    addon_released = addon_release_totals(allocations)
    revision_rows = [(row.pk, row.receipt_id, row.amount,
                      released.get(row.pk, 0) + sum(part.amount for part in release_rows if part.allocation_id == row.pk)
                      + sum(PointsPostRefundTransferRelease.objects.filter(allocation=row).values_list('amount', flat=True))
                      + addon_released.get(row.pk, 0))
                     for row in allocations]
    reservation = [(pk, amount - used) for pk, _, amount, used in revision_rows if amount > used]
    from .points_funding_revision import plan_funding_revision, plan_unpaid_funding_revision
    try:
        if records:
            revision = plan_funding_revision(allocations=revision_rows, original_reservation=reservation,
                previous_required=current.required_units,
                paid_provider_units=sum(row.amount for row in records.values()) + paid_addon_units,
                projected=projected)
        else:
            revision = plan_unpaid_funding_revision(allocations=revision_rows, original_reservation=None,
                previous_required=current.required_units, projected=projected)
    except PointsFundingAllocationError as exc:
        raise ValidationError('追加退款的补资预留待核对') from exc
    return {'order_id': order.pk, 'case_id': case.pk, 'account_id': scope[0], 'redemption_id': receipt.pk,
        'prior_refund_id': first.pk, 'refund_equivalent_units': refund_equivalent_units,
        'coin_units': assets.coin_units, 'points_hundredths': assets.point_hundredths,
        'returned_prepaid_units': sources.prepaid_units, 'returned_gift_units': sources.gift_units,
        'returned_compensation_units': sources.compensation_units, 'is_final': assets.is_final,
        'provider_terms': provider_terms,
        **({'segment_pending_plan': segment_pending_plan} if segment_pending_plan else {}),
        'provider_recovery_preview': {'pending_units': pending,
            'wallet_units': sum(item['wallet_units'] for item in recoveries),
            'deposit_units': sum(item['deposit_units'] for item in recoveries), 'shortfall_units': shortfall,
            'providers': recoveries},
        'rounding_to_store_units': burden.rounding_to_store_units, 'settled_other_provider_units': revision.paid_provider_units,
        'current_required_units': current.required_units,
        'conditional_funding': asdict(projected), 'conditional_funding_revision': asdict(revision),
        'estimated_sufficient': shortfall == 0, 'execution_available': False}
