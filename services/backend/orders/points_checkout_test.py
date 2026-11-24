"""Guard for the incomplete service-points checkout on disposable test DBs."""
import os

from django.conf import settings
from django.core import signing
from rest_framework.exceptions import ValidationError

from stores.db import transaction

CHECKOUT_SALT = 'orders.service-points-checkout-test.v1'


def require_isolated_checkout(*, allow_default_principal=False, existing_order=None):
    connection = transaction.get_connection()
    database = connection.settings_dict
    safe_names = {
        ('default', ':memory:'),
        ('default', 'file:memorydb_default?mode=memory&cache=shared'),
        ('store_b', 'file:memorydb_store_b?mode=memory&cache=shared'),
    }
    module = os.environ.get('DJANGO_SETTINGS_MODULE')
    if module == 'config.ordinary_followup_mysql_test_settings':
        import re
        run_id = os.environ.get('XA_FOLLOWUP_MYSQL_RUN_ID', '')
        suffix = {'default': 'a', 'store_b': 'b'}.get(connection.alias)
        expected_host = f'xa-followup-mysql-{run_id}'
        if (getattr(settings, 'SERVICE_POINTS_CHECKOUT_TESTING', False) is True
                and getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False)
                and os.environ.get('XA_FOLLOWUP_MYSQL_ISOLATED') == '1'
                and re.fullmatch(r'[0-9a-f]{12}', run_id) and suffix
                and database.get('ENGINE') == 'django.db.backends.mysql'
                and database.get('HOST') == expected_host
                and database.get('NAME') == f'test_xa_test_followup_{run_id}_{suffix}'):
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@hostname')
                if cursor.fetchone()[0] == expected_host:
                    return
        raise ValidationError('追加售后测试数据库标识不匹配')
    principal_testing = False
    if allow_default_principal and getattr(settings, 'ORDINARY_COMPLETION_TRANSFER_INTERNAL_TESTING', False):
        principal_testing = bool(getattr(settings, 'DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED', False)
            and getattr(settings, 'ORDINARY_SERVICE_FUNDING_ENABLED', False))
        if not principal_testing and existing_order is not None:
            from .principal_segments import enabled_for_order
            principal_testing = enabled_for_order(existing_order)
    if (getattr(settings, 'SERVICE_POINTS_CHECKOUT_TESTING', False) is not True
            or module not in
                ('config.test_settings', 'config.migration_test_settings', 'config.multitenant_test_settings',
                 'config.license_quota_fast_test_settings')
            or database.get('ENGINE') != 'django.db.backends.sqlite3'
            or (connection.alias, database.get('NAME')) not in safe_names
            or not (getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False) or principal_testing)):
        raise ValidationError('服务订单积分支付尚未开放')


def assignment_enabled():
    from .segment_financial_capture import enabled as capture_enabled
    from .points_business_access import permits
    if permits(phase='checkout', kind='service') or permits(phase='assignment', kind='service'):
        return capture_enabled()
    if not getattr(settings, 'SERVICE_POINTS_ASSIGN_TESTING', False) or not capture_enabled():
        return False
    try:
        require_isolated_checkout()
    except ValidationError:
        return False
    return True


def report_enabled():
    from .points_business_access import permits
    if any(permits(phase=phase, kind=kind) for kind in ('service', 'addon') for phase in ('report', 'refund', 'settlement')):
        from .segment_financial_capture import enabled as capture_enabled
        return capture_enabled()
    return bool(getattr(settings, 'SERVICE_POINTS_REPORT_TESTING', False) and assignment_enabled())


def settlement_enabled():
    from .points_business_access import permits
    from .points_transfer_refund_access import permits as transfer_permits
    if transfer_permits(phase='settlement') or permits(phase='settlement', kind='addon'):
        from .segment_financial_capture import enabled as capture_enabled
        return capture_enabled()
    if permits(phase='settlement', kind='service'):
        from .segment_financial_capture import enabled as capture_enabled
        return capture_enabled()
    return bool(getattr(settings, 'SERVICE_POINTS_SETTLEMENT_TESTING', False)
                and getattr(settings, 'SERVICE_POINTS_FUNDING_TESTING', False) and report_enabled())


def ordinary_settlement_enabled():
    return bool((getattr(settings, 'SERVICE_ORDINARY_FUNDING_TESTING', False) and settlement_enabled())
                or ordinary_production_enabled())


def ordinary_production_enabled():
    from .principal_segments import enabled as principal_segments_enabled
    return bool(getattr(settings, 'ORDINARY_SERVICE_FUNDING_ENABLED', False)
                and (getattr(settings, 'BOSS_FUNDING_SELECTION_ENABLED', False)
                     or getattr(settings, 'DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED', False))
                and ((getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False)
                      and getattr(settings, 'SEGMENT_ECONOMICS_CAPTURE_ENABLED', False))
                     or principal_segments_enabled())
                and getattr(settings, 'PLATFORM_IDENTITY_ENABLED', False)
                and getattr(settings, 'PLATFORM_PASS_REPAYMENT_ENABLED', False))


def ordinary_refund_production_order(order, *, allow_open=False):
    """One ordinary main refund, with no add-ons or transfers."""
    from .ordinary_funding_continuation import refunded_continuation_active
    if (not ordinary_production_enabled() and not refunded_continuation_active(order)) or not order or order.is_gift_snapshot or order.source_order_id:
        return False
    from .models import NegotiationExecution, OrderOriginalShareTransfer, OrderPlatformSubsidy, OrderPrestartHandoff, OrderStatusLog
    from .points_service_payment import needs_integrated_processing
    refunds = list(order.refund_records.all()[:2])
    return bool(order.customer_account_id and order.funding_terms is not None and order.funding_allocations.exists()
        and not needs_integrated_processing(order) and len(refunds) == 1 and 0 < refunds[0].amount <= order.amount
        and NegotiationExecution.objects.filter(refund=refunds[0], termination_processed_at__isnull=False).count() == 1
        and NegotiationExecution.objects.filter(proposal__case__order=order).count() == 1
        and (allow_open or not order.aftersales.filter(status='OPEN').exists())
        and not order.addon_intents.filter(payment__isnull=False).exists() and not order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        and not OrderPrestartHandoff.objects.filter(order=order).exists()
        and not OrderPlatformSubsidy.objects.filter(order=order).exists())


def require_ordinary_funding_order(order):
    """Authorize only the verified, simple ordinary-order production slice.

    Call with the order locked in a tenant transaction for every mutation.
    """
    from .ordinary_zero_followup_capability import eligible as zero_followup_eligible
    from .negotiation_zero import marked_execution_id
    if (zero_followup_eligible(order) and not order.refund_records.exists()
            and marked_execution_id(order) is not None):
        return
    from .ordinary_funding_continuation import continuation_active
    if continuation_active(order) or ordinary_refund_production_order(order):
        return
    from .ordinary_addon_production import eligible as ordinary_addon_eligible
    if ordinary_addon_eligible(order):
        return
    if ordinary_addon_eligible(order, allow_open=True):
        from .models import OrdinaryRefundedAddonPaidRevision
        if OrdinaryRefundedAddonPaidRevision.objects.filter(settlement__order=order).exists():
            from .ordinary_refunded_addon_paid_revision import pending_locked
            if pending_locked(order):
                return
    from .models import (NegotiationExecution, OrderOriginalShareTransfer,
                         OrderPlatformSubsidy, OrderPrestartHandoff, OrderStatusLog)
    from .points_service_payment import needs_integrated_processing
    if (not ordinary_production_enabled() or not order or order.is_gift_snapshot
            or order.source_order_id or not order.customer_account_id
            or order.funding_terms is None or not order.funding_allocations.exists()
            or needs_integrated_processing(order) or order.refund_records.exists()
            or order.aftersales.exclude(status='RESOLVED').exists()
            or NegotiationExecution.objects.filter(proposal__case__order=order).exists()
            or order.status_logs.filter(action=OrderStatusLog.Action.TRANSFER).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or order.addon_intents.filter(payment__isnull=False).exists()
            or OrderPlatformSubsidy.objects.filter(order=order).exists()):
        raise ValidationError('当前订单不在普通订单补资开放范围，请联系门店核对')


def require_order_funding_access(order):
    from .points_transfer_refund_access import permits
    if any(permits(order, phase=phase) for phase in ('transfer', 'report', 'refund', 'settlement')):
        return
    from .points_business_access import permits as service_permits
    if any(service_permits(order, phase=phase, kind='service') for phase in ('assignment', 'funding', 'report', 'refund', 'settlement')):
        return
    if any(service_permits(order, phase=phase, kind='addon') for phase in ('payment', 'handover', 'funding', 'refund', 'report', 'settlement')):
        return
    from .ordinary_followup_capability import permits_funding
    if permits_funding(order):
        return
    from .ordinary_transfer_capability import permits as ordinary_transfer_permits
    if ordinary_transfer_permits(order):
        return
    from .segment_settlement import completion_transfer_order_eligible
    if completion_transfer_order_eligible(order):
        return
    try:
        require_isolated_checkout()
    except ValidationError:
        require_ordinary_funding_order(order)
    else:
        if not getattr(settings, 'SERVICE_POINTS_FUNDING_TESTING', False):
            raise ValidationError('订单补资尚未开放')


def ordinary_settlement_order(order):
    return bool(ordinary_settlement_enabled() and order and not order.is_gift_snapshot and not order.source_order_id)


def points_consumption_enabled():
    from .points_business_access import permits
    from .points_transfer_refund_access import permits as transfer_permits
    if transfer_permits(phase='settlement') or permits(phase='settlement', kind='addon'):
        from .segment_financial_capture import enabled as capture_enabled
        return capture_enabled()
    if permits(phase='settlement', kind='service'):
        return settlement_enabled()
    return bool(getattr(settings, 'SERVICE_POINTS_CONSUMPTION_TESTING', False) and settlement_enabled())


def quote_for_checkout(account, data, selected):
    from .points_service_payment import PRICE_FIELDS, quote
    price = {key: data[key] for key in PRICE_FIELDS}
    price.update(promotion_id=getattr(data.get('promotion'), 'pk', None), game_rounds=data['game_rounds'])
    calculation, token = quote(account, data['service'], price, selected,
                               gift=data['gift_balance_amount'], compensation=data['compensation_balance_amount'])
    envelope = {'receipt_quote': token, 'coupon_id': getattr(data.get('user_coupon'), 'pk', None),
                'discount_selection_token': data.get('discount_selection_token')}
    from .points_business_access import permits
    if permits(account=account, service=data.get('service'), phase='checkout', kind='service'):
        if calculation['calculation']['payable_units'] <= 0:
            raise ValidationError('积分付款当前须保留大于零的店币实付')
        envelope['formal_assignment'] = {
            'provider_ids': [person.pk for person in data.get('provider_accounts', [])],
            'personal_game_snapshots': data.get('personal_game_snapshots'),
            'commission_snapshot': data.get('commission_snapshot'),
            'dispatch_route': data.get('dispatch_route'),
            'dispatch_mode': data.get('dispatch_mode'),
            'required_provider_count': data.get('required_provider_count'),
        }
    return calculation, signing.dumps(envelope, salt=CHECKOUT_SALT, compress=True)


def verify_checkout_quote(account, data, token):
    from .points_business_access import permits
    if not permits(account=account, service=data.get('service'), phase='checkout', kind='service'):
        require_isolated_checkout()
    try:
        envelope = signing.loads(token, salt=CHECKOUT_SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('积分报价已失效，请重新核对') from None
    formal = permits(account=account, service=data.get('service'), phase='checkout', kind='service')
    keys = {'receipt_quote', 'coupon_id', 'discount_selection_token'} | ({'formal_assignment'} if formal else set())
    assignment = {'provider_ids': [person.pk for person in data.get('provider_accounts', [])],
                  'personal_game_snapshots': data.get('personal_game_snapshots'),
                  'commission_snapshot': data.get('commission_snapshot'),
                  'dispatch_route': data.get('dispatch_route'),
                  'dispatch_mode': data.get('dispatch_mode'),
                  'required_provider_count': data.get('required_provider_count')}
    if (not isinstance(envelope, dict) or set(envelope) != keys
            or (formal and envelope['formal_assignment'] != assignment)
            or envelope['coupon_id'] != getattr(data.get('user_coupon'), 'pk', None)
            or envelope['discount_selection_token'] != data.get('discount_selection_token')
            or not isinstance(envelope['receipt_quote'], str)):
        raise ValidationError('优惠券或优惠组合已变化，请重新核对')
    from .points_service_payment import SALT
    try:
        expected = signing.loads(envelope['receipt_quote'], salt=SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('积分报价已失效，请重新核对') from None
    if not isinstance(expected, dict) or type(expected.get('selected')) is not int:
        raise ValidationError('积分报价无效，请重新核对')
    actual, _ = quote_for_checkout(account, data, expected['selected'])
    if expected != actual:
        raise ValidationError('订单价格、积分余额或规则已变化，请重新核对报价')
    return actual, envelope['receipt_quote']
