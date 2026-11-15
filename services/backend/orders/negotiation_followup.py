"""Locked calculation for a later scoped negotiation; does not post money.

The first implementation covers the already-verified two-person continuation:
one person stopped in the previous negotiation and the other is still serving.
The result is only valid inside this transaction and is not a funds reservation.
"""
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from club_accounts.models import LegacyAccountMap
from users.models import EscortProfile
from .models import Order, OrderOriginalShareTransfer
from .negotiation_proposals import _lock_case, _staff, enabled
from .negotiation_termination import continuing_team
from .refund_math import provider_refund_burden
from .settlement import SettlementError, compute_provider_income


def followup_enabled():
    """Integration gate remains off unless explicitly enabled in isolated tests."""
    from django.conf import settings
    return bool(getattr(settings, 'NEGOTIATION_FOLLOWUP_ENABLED', False)
                and getattr(settings, 'NEGOTIATION_REFUND_EXECUTION_ENABLED', False))


def completed_followup_preview_enabled():
    """Only opt into the separate completed-order preview in isolated tests."""
    import os
    from pathlib import Path
    from django.conf import settings
    connection = transaction.get_connection()
    database = connection.settings_dict
    allowed_databases = {('default', ':memory:'), ('default', 'file:memorydb_default?mode=memory&cache=shared'),
                         ('store_b', 'file:memorydb_store_b?mode=memory&cache=shared')}
    module = os.environ.get('DJANGO_SETTINGS_MODULE')
    memory_test = (module in ('config.test_settings', 'config.migration_test_settings', 'config.multitenant_test_settings')
                   and (connection.alias, database.get('NAME')) in allowed_databases)
    file_name = os.environ.get('XA_ASGI_NETWORK_TEST_DB', '')
    file_test = False
    if module == 'config.asgi_network_test_settings' and os.environ.get('XA_NEGOTIATION_COMPLETED_BROWSER') == '1' and file_name:
        expected = Path(file_name).resolve()
        file_test = (connection.alias == 'default' and expected == Path(database.get('NAME', '')).resolve()
                     and expected.parent == (Path(settings.BASE_DIR).parent.parent / '.tools').resolve()
                     and expected.name.startswith('asgi-reservation-') and expected.suffix == '.sqlite3')
    return (followup_enabled() and getattr(settings, 'NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED', False) is True
            and database.get('ENGINE') == 'django.db.backends.sqlite3' and (memory_test or file_test))


def completed_followup_execution_enabled():
    from django.conf import settings
    return (completed_followup_preview_enabled()
            and getattr(settings, 'NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING', False) is True)


@transaction.atomic
def preview_followup(case_id, *, actor, refund_units):
    """Re-read the case and prior receipt, then calculate the remaining share.

    No proposal, confirmation, refund, settlement or wallet reservation is
    created. Callers must perform a fresh calculation under the same locks
    immediately before any future mutation.
    """
    if not enabled() or not _staff(actor) or not actor.is_active or not actor.can_login:
        raise ValidationError('当前无法核对追加协商方案')
    return _preview_followup_locked(case_id, refund_units=refund_units)


def _preview_followup_locked(case_id, *, refund_units):
    """Internal calculation; the authorized caller must already hold a transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('追加协商核对必须在门店事务内执行')
    if type(refund_units) is not int or refund_units < 0 or refund_units % 10:
        raise ValidationError('退款金额须为非负整数店币')
    order, case = _lock_case(case_id)
    if order.settled_at or order.consumption_confirmed_at:
        raise ValidationError('订单已有最终结算或实销，追加协商须先核对')
    # A completed prior receipt proves the mutex should already exist.  A
    # read-only estimate must not silently repair missing accounting state.
    from .financial_lock import lock_existing_order_money
    try:
        lock_existing_order_money()
    except SettlementError as exc:
        raise ValidationError('订单资金互斥记录待核对') from exc
    completion = None
    if order.status == Order.Status.COMPLETED:
        if not completed_followup_preview_enabled():
            raise ValidationError('完成后的追加协商尚未接通，请联系客服核对')
        from .negotiation_completed_followup import inspect_completed_followup
        completion = inspect_completed_followup(case.pk)
    receipt = continuing_team(order, verify=True, allow_completed=completion is not None)
    if not receipt:
        raise ValidationError('原协商或继续履约凭证待核对')
    if (case.status != 'OPEN' or case.account_id != order.customer_account_id or not case.client_request_id
            or not case.created_at or case.created_at < receipt.termination_processed_at
            or case.created_at > timezone.now()):
        raise ValidationError('本次售后时间或账号待核对')
    rows = list(order.providers.select_for_update().order_by('pk'))
    active = [row for row in rows if row.released_at is None]
    if (len(rows) != 2 or len(active) != 1 or not active[0].provider_id or not active[0].provider_account_id
            or case.affected_provider_account_ids != [active[0].provider_account_id]
            or len({row.provider_account_id for row in rows}) != 2
            or any(not row.provider_id or not row.provider_account_id for row in rows)
            or active[0].settled_at or (completion is None and any(row.settled_at for row in rows))):
        raise ValidationError('当前履约人员或收益状态待核对')
    provider = active[0]
    if (not LegacyAccountMap.objects.select_for_update().filter(account_id=provider.provider_account_id,
            legacy_user_id=provider.provider_id).exists()
            or not EscortProfile.objects.select_for_update().filter(account_id=provider.provider_account_id,
                                                                    user_id=provider.provider_id).exists()):
        raise ValidationError('当前陪玩账号归属待核对')
    if (sum(row.settlement_base for row in rows) != order.amount
            or sum(row.provider_income for row in rows) != order.provider_income
            or order.provider_income + order.inviter_commission + order.shop_income != order.amount
            or any(row.provider_income != compute_provider_income(row.settlement_base, row.commission_type,
                                                                  row.commission_rate, row.commission_fixed)
                   for row in rows)):
        raise ValidationError('订单收益或抽成快照待核对')
    refunds = list(order.refund_records.select_for_update().order_by('pk'))
    if receipt.zero_order_id:
        if refunds or order.payment_status != Order.PaymentStatus.PAID:
            raise ValidationError('前次零退款后的资金记录待核对')
        prior = 0
    else:
        if len(refunds) != 1 or refunds[0].pk != receipt.refund_id:
            raise ValidationError('前次协商退款及后续记录待核对')
        prior = receipt.refund.amount
        expected = Order.PaymentStatus.REFUNDED if prior == order.amount else Order.PaymentStatus.PARTIALLY_REFUNDED
        if order.payment_status != expected:
            raise ValidationError('前次协商退款状态待核对')
    remaining = order.amount - prior
    if remaining < 0 or refund_units > remaining:
        raise ValidationError('退款金额不能超过订单剩余可退金额')
    payments = list(order.transactions.select_for_update().filter(
        tx_type='PAY', status='SUCCESS', reversal_of__isnull=True).select_related('wallet'))
    if (len(payments) != 1 or payments[0].amount != -order.amount
            or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount
            or payments[0].reversals.exists()):
        raise ValidationError('原付款凭证待核对')
    if completion is None and order.transactions.select_for_update().filter(
            Q(tx_type__in=['INCOME', 'SHOP_INCOME', 'GIFT']), status='SUCCESS', reversal_of__isnull=True).exists():
        raise ValidationError('订单已有收益流水，追加协商须先核对')
    if (order.source_order_id or Order.objects.filter(source_order_id=order.pk, is_gift_snapshot=False).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('关联服务或转单后的追加协商尚待核对')
    from .direct_payment_models import DirectPaymentIntent
    from .points_service_payment import needs_integrated_processing
    from .models import OrderPlatformSubsidy
    if (DirectPaymentIntent.objects.filter(order=order).exists() or needs_integrated_processing(order)
            or OrderPlatformSubsidy.objects.filter(order=order).exists()):
        raise ValidationError('该订单资金类型的追加协商尚待核对')
    result = provider_refund_burden(refund_equivalent_units=refund_units,
                                    original_price_weights={provider.provider_account_id: 1}) if refund_units else None
    burden = result.amounts[provider.provider_account_id] if result else 0
    retained = max(provider.provider_income - burden, 0)
    additional = max(burden - provider.provider_income, 0)
    rounding = result.rounding_to_store_units if result else 0
    previous_shares = list(receipt.shares.select_for_update().order_by('pk'))
    if (len(previous_shares) != 1 or previous_shares[0].account_id == provider.provider_account_id
            or previous_shares[0].assignment.order_id != order.pk):
        raise ValidationError('前次协商收益归属待核对')
    previous = previous_shares[0]
    recovered = 0 if receipt.zero_order_id else receipt.refund.recovered_amount
    referral_rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
    if order.inviter_commission != order.amount * referral_rate // 100:
        raise ValidationError('主单推荐佣金快照待核对')
    inviter_income = (order.amount - prior - refund_units) * referral_rate // 100
    store_income = order.shop_income + order.inviter_commission - inviter_income
    if (previous.retained_income + retained + inviter_income + store_income
            + receipt.rounding_to_store_units + rounding
            != order.amount - prior - refund_units + recovered + additional):
        raise ValidationError('追加协商后的资金分配待核对')
    result = {'prior_execution_id': receipt.pk, 'remaining_refundable_units': remaining, 'refund_units': refund_units,
            'rounding_to_store_units': rounding,
            'provider_terms': [{'account_id': provider.provider_account_id,
                                'original_income_units': provider.provider_income,
                                'burden_units': burden, 'retained_units': retained,
                                'additional_units': additional}],
            'projected_ledger': {'previous_refund_units': prior, 'previous_recovered_units': recovered,
                'previous_retained_units': previous.retained_income, 'current_retained_units': retained,
                'current_additional_units': additional, 'store_income_units': store_income,
                'inviter_income_units': inviter_income,
                'rounding_units': receipt.rounding_to_store_units + rounding,
                'customer_net_units': order.amount - prior - refund_units}}
    if completion is not None:
        result['completion_evidence'] = {'completed_at':completion['completed_at'].isoformat(),
            'completion_log_id':completion['completion_log_id']}
    return result
