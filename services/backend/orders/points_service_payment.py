"""Service-points quote and receipt helpers for the bound checkout flow.

The caller must revalidate service, discounts, coupon and order eligibility,
hold the account and wallet locks, and create the order, source allocations
and successful wallet payment in the same tenant transaction. Any exception
must abort that entire transaction. Refund and settlement are handled by the
corresponding order-scoped services.
"""
from dataclasses import asdict

from django.conf import settings
from django.core import signing
from django.db import DatabaseError
from rest_framework.exceptions import ValidationError

from club_accounts.models import ClubAccount
from club_accounts.models import LegacyAccountMap
from engagement.models import MemberProfile, OrderFundingAllocation, PointLedger
from engagement.points_usage import FIELDS, current_usage, enabled as usage_enabled, assert_usage_allowed
from engagement.services import change_points
from stores.context import current_store
from stores.db import transaction
from wallet.models import SystemConfig, Transaction, Wallet
from .models import Order, OrderPointsRedemption
from .points_redemption_config import CONFIG_KEY, current_rules, parse_rules
from .points_redemption_math import PointsRedemptionError, quote_points_redemption
from .purchase_benefits import is_experience

SALT = 'orders.service-points-receipt.v1'
PRICE_FIELDS = ('original_amount', 'boss_discount', 'promo_discount', 'coupon_discount')
MARKER = 'points_redemption_v1'


def points_receipt_table_exists():
    """Distinguish an older schema from loss of a previously migrated table."""
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
    if OrderPointsRedemption._meta.db_table in tables:
        return True
    from django.db.migrations.recorder import MigrationRecorder
    from stores.context import current_database
    if ('django_migrations' not in tables or MigrationRecorder.Migration.objects.using(current_database())
            .filter(app='orders', name='0117_orderpointsredemption').exists()):
        raise DatabaseError('积分付款凭证表缺失，须先核对数据库')
    return False


def prepare_points_display(orders):
    """Batch read-only evidence for serializers; never use for money mutations."""
    orders = list(orders)
    ids = {order.pk for order in orders if order.pk}
    if not ids:
        return
    evidence = set(PointLedger.objects.filter(order_id__in=ids, kind='ORDER_POINTS_REDEEM')
                   .values_list('order_id', flat=True))
    try:
        receipt_exists = points_receipt_table_exists()
    except DatabaseError:
        evidence.update(ids)
        receipt_exists = False
    if receipt_exists:
        evidence.update(OrderPointsRedemption.objects.filter(order_id__in=ids).values_list('order_id', flat=True))
    for order in orders:
        order._display_points_evidence = (order.pk in evidence or
            isinstance(order.funding_terms, dict) and MARKER in order.funding_terms)


def display_has_points(order):
    """Cached evidence is only suitable for a single read-only representation."""
    if hasattr(order, '_display_points_evidence'):
        return order._display_points_evidence
    try:
        return needs_integrated_processing(order)
    except DatabaseError:
        return True


def needs_integrated_processing(order):
    """Fail closed if any independent points-payment evidence exists."""
    if isinstance(order.funding_terms, dict) and MARKER in order.funding_terms:
        return True
    if not order.pk:
        return False
    terms = Order.objects.filter(pk=order.pk).values_list('funding_terms', flat=True).first()
    if isinstance(terms, dict) and MARKER in terms:
        return True
    if PointLedger.objects.filter(order_id=order.pk, kind='ORDER_POINTS_REDEEM').exists():
        return True
    # Older store schemas do not yet have the receipt table. A missing marker
    # must not turn an existing receipt into an ordinary coin-only refund.
    if not points_receipt_table_exists():
        return False
    return OrderPointsRedemption.objects.filter(order_id=order.pk).exists()


def _enabled():
    return bool(getattr(settings, 'SERVICE_ORDER_POINTS_RECEIPT_ENABLED', False) and usage_enabled())


def _store():
    store = current_store()
    if store is None:
        raise ValidationError('请从所属门店入口重新报价')
    return store.slug


def _eligible(service):
    if is_experience(service) or service.is_addon or (service.service_category_id and service.service_category.is_gift):
        raise ValidationError('此服务不支持积分抵扣')


def _calculate(account, service, price, selected, gift, compensation, balance, rules):
    _eligible(service)
    if not rules['version']:
        raise ValidationError('本店尚未配置积分抵扣')
    if (not isinstance(price, dict) or set(price) != {*PRICE_FIELDS, 'promotion_id', 'game_rounds'}
            or any(type(price[k]) is not int or price[k] < 0 for k in (*PRICE_FIELDS, 'game_rounds'))
            or price['game_rounds'] < 1 or (price['promotion_id'] is not None
                                           and (type(price['promotion_id']) is not int or price['promotion_id'] < 1))
            or any(type(v) is not int or v < 0 or v % 10 for v in (gift, compensation))):
        raise ValidationError('订单价格或支付来源无效')
    deal = price['original_amount'] - sum(price[k] for k in PRICE_FIELDS[1:])
    try:
        quote = quote_points_redemption(payable_before_points_units=deal, available_point_hundredths=balance,
            selected_point_hundredths=selected, group_point_hundredths=rules['group_point_hundredths'],
            group_coin_units=rules['group_coin_units'], max_ratio_basis_points=rules['max_ratio_basis_points'])
    except PointsRedemptionError as exc:
        raise ValidationError(str(exc)) from None
    if not quote.selected_groups or gift + compensation > quote.payable_units:
        raise ValidationError('积分或支付来源不足，请重新核对')
    return {'store': _store(), 'account': account.pk, 'business_cycle': account.business_cycle,
            'service': service.pk, 'price': price, 'selected': selected, 'gift': gift,
            'compensation': compensation, 'rules': rules, 'calculation': asdict(quote)}


def quote(account, service, price, selected, gift=0, compensation=0):
    """Sign already server-validated pricing; does not reserve any funds."""
    if not _enabled() or account.account_type != ClubAccount.AccountType.BOSS:
        raise ValidationError('服务订单积分支付暂未开放')
    try:
        issuance, switches = current_usage()
        if not issuance or not all(switches[field] for field in FIELDS):
            raise ValidationError('本店积分使用暂时暂停')
        profile = MemberProfile.objects.filter(account_id=account.pk).first()
        result = _calculate(account, service, price, selected, gift, compensation,
                            profile.points_balance if profile else 0, current_rules())
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    return result, signing.dumps(result, salt=SALT, compress=True)


def record_locked(*, order_id, payment_id, token):
    """Verify recorded wallet/source facts and debit points within caller's txn.

    Never catch this function's exception and commit the surrounding payment.
    This is not a substitute for checkout's own pricing and eligibility checks.
    """
    if not _enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('服务订单积分支付暂未开放')
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise ValidationError('积分报价无效，请重新核对')
    try:
        expected = signing.loads(token, salt=SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('积分报价已失效，请重新核对') from None
    if not isinstance(expected, dict) or type(expected.get('selected')) is not int or expected['selected'] < 0:
        raise ValidationError('积分报价无效，请重新核对')
    order = Order.objects.select_for_update().select_related('service__service_category').get(pk=order_id)
    account = ClubAccount.objects.select_for_update().get(pk=order.customer_account_id)
    if getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True:
        from .points_business_access import bind_checkout_order
        bind_checkout_order(order, account, order.service)
    wallet = Wallet.objects.select_for_update().get(account_id=account.pk)
    profile = MemberProfile.objects.select_for_update().get(account_id=account.pk)
    if (account.account_type != ClubAccount.AccountType.BOSS
            or not account.is_active or not account.can_login):
        raise ValidationError('订单账号不支持积分支付')
    if (wallet.user_id != order.customer_id or (order.customer_id is not None and not
            LegacyAccountMap.objects.select_for_update().filter(account_id=account.pk,
                legacy_user_id=order.customer_id).exists())):
        raise ValidationError('订单、钱包和历史账号归属不一致，请先核对')
    if needs_integrated_processing(order):
        raise ValidationError('订单积分抵扣已登记，请勿重复扣款')
    if (not isinstance(order.funding_terms, dict)
            or not {'vip_enabled', 'points_enabled', 'points_rate', 'birthday_multiplier'} <= order.funding_terms.keys()):
        raise ValidationError('订单缺少可核对的会员及资金快照')
    assert_usage_allowed()
    row = SystemConfig.objects.select_for_update().filter(key=CONFIG_KEY).first()
    price = {key: getattr(order, key) for key in PRICE_FIELDS}
    price.update(promotion_id=order.promotion_id, game_rounds=order.game_rounds)
    try:
        rules = parse_rules(row.value if row else None)
        actual = _calculate(account, order.service, price, expected['selected'], expected.get('gift'),
                            expected.get('compensation'), profile.points_balance, rules)
    except (ValueError, TypeError) as exc:
        raise ValidationError('积分报价或配置无效，请重新核对') from exc
    if expected != actual or order.business_cycle != account.business_cycle:
        raise ValidationError('订单价格、积分余额或规则已变化，请重新核对报价')
    calc = actual['calculation']
    payment = Transaction.objects.select_for_update().get(pk=payment_id)
    if (not wallet.is_active or order.payment_status != Order.PaymentStatus.PAID or order.amount != calc['payable_units']
            or payment.order_id != order.pk or payment.wallet_id != wallet.pk
            or payment.tx_type != Transaction.TxType.PAY or payment.status != Transaction.Status.SUCCESS
            or payment.amount != -order.amount or payment.balance_after != payment.balance_before + payment.amount
            or payment.balance_after != wallet.balance
            or payment.reversal_of_id or payment.reversals.exists()
            or Transaction.objects.filter(order=order, tx_type=Transaction.TxType.PAY,
                                          status=Transaction.Status.SUCCESS).exclude(pk=payment.pk).exists()):
        raise ValidationError('订单付款流水不一致，请先核对')
    sources = list(OrderFundingAllocation.objects.select_for_update().select_related('lot').filter(order=order))
    from engagement.funding_wallet import validate_order_funding_sources
    validate_order_funding_sources(order, sources)
    totals = {kind: sum(row.amount for row in sources if row.kind == kind)
              for kind in OrderFundingAllocation.Kind.values}
    if (not sources or any(row.lot.account_id != account.pk or row.refunded_amount for row in sources)
            or sum(totals.values()) != order.amount or totals['GIFT'] != actual['gift']
            or totals['COMPENSATION'] != actual['compensation']):
        raise ValidationError('订单资金来源不一致，请先核对')
    key = f'order:{order.pk}:points-redemption'
    if (OrderPointsRedemption.objects.filter(order=order).exists() or PointLedger.objects.filter(event_key=key).exists()
            or PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').exists()):
        raise ValidationError('订单积分抵扣已登记，请勿重复扣款')
    ledger = change_points(profile, -calc['points_hundredths'], key, 'ORDER_POINTS_REDEEM',
                           '服务订单积分抵扣', order=order)
    receipt = OrderPointsRedemption.objects.create(order=order, payment=payment, ledger=ledger,
        deal_units=price['original_amount'] - sum(price[k] for k in PRICE_FIELDS[1:]),
        paid_units=calc['payable_units'], discount_units=calc['discount_units'],
        points_hundredths=calc['points_hundredths'], group_points_hundredths=rules['group_point_hundredths'],
        group_coin_units=rules['group_coin_units'], groups=calc['selected_groups'],
        max_ratio_basis_points=rules['max_ratio_basis_points'], config_version=rules['version'])
    from .points_benefit_snapshot import capture_locked
    capture_locked(receipt)
    # Same transaction as the actual payment and receipt. The marker makes
    # old refund/settlement/benefit paths fail closed after a flag rollback;
    # it does not enable checkout or the unfinished integration.
    order.funding_terms = {**order.funding_terms, MARKER: {'receipt_id': receipt.pk}}
    order.save(update_fields=['funding_terms'])
    return receipt
