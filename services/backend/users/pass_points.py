"""Points quote for one immediately activated pass; no funds move here."""
from dataclasses import asdict
from collections.abc import Mapping

from django.conf import settings
from django.core import signing
from rest_framework.exceptions import ValidationError

from engagement.models import MemberProfile
from engagement.points_usage import FIELDS, current_usage, enabled as usage_enabled
from orders.points_redemption_config import CONFIG_KEY, current_rules, parse_rules
from orders.points_redemption_math import PointsRedemptionError, quote_points_redemption
from stores.context import current_store
from stores.db import transaction
from wallet.models import SystemConfig

SALT = 'users.pass-points.v1'


def available():
    return bool(getattr(settings, 'PROVIDER_PASS_POINTS_PAYMENT_ENABLED', False) and usage_enabled())


def _store():
    store = current_store()
    if store is None:
        raise ValidationError('缺少门店上下文，请重新登录')
    return store.slug


def _calculate(*, account, tier, daily_price, selected, balance, rules):
    if not rules['version']:
        raise ValidationError('本店尚未配置积分抵扣')
    try:
        quote = quote_points_redemption(payable_before_points_units=daily_price,
            available_point_hundredths=balance, selected_point_hundredths=selected,
            group_point_hundredths=rules['group_point_hundredths'], group_coin_units=rules['group_coin_units'],
            max_ratio_basis_points=rules['max_ratio_basis_points'])
    except PointsRedemptionError as exc:
        raise ValidationError(str(exc)) from None
    return {'store': _store(), 'account': account.pk, 'business_cycle': account.business_cycle,
            'tier': tier, 'daily_price': daily_price, 'selected': selected,
            'rules': rules, 'calculation': asdict(quote)}


def preview(account, tier, daily_price, selected):
    if not available():
        raise ValidationError('通行证积分抵扣暂未开放')
    issuance, switches = current_usage()
    # An absent operation row uses the model's existing enabled default;
    # the payment transaction will create and lock that row before debiting.
    if issuance is False or not all(switches[field] for field in FIELDS):
        raise ValidationError('本店积分使用暂时暂停')
    profile = MemberProfile.objects.filter(account_id=account.pk).first()
    rules = current_rules()
    terms = _calculate(account=account, tier=tier, daily_price=daily_price, selected=selected,
                       balance=profile.points_balance if profile else 0, rules=rules)
    return terms, signing.dumps(terms, salt=SALT, compress=True)


def lock_and_verify(account, tier, daily_price, token, profile):
    if not available() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('通行证积分抵扣暂不可用')
    if not isinstance(token, str) or not token or len(token) > 8192:
        raise ValidationError('积分报价无效，请重新核对')
    try:
        expected = signing.loads(token, salt=SALT, max_age=600)
    except signing.BadSignature:
        raise ValidationError('积分报价已失效，请重新核对') from None
    if not isinstance(expected, dict) or type(expected.get('selected')) is not int or expected['selected'] < 0:
        raise ValidationError('积分报价无效，请重新核对')
    from engagement.points_usage import assert_usage_allowed
    assert_usage_allowed()
    row = SystemConfig.objects.select_for_update().filter(key=CONFIG_KEY).first()
    try:
        rules = parse_rules(row.value if row else None)
        actual = _calculate(account=account, tier=tier, daily_price=daily_price, selected=expected['selected'],
                            balance=profile.points_balance, rules=rules)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    if actual != expected:
        raise ValidationError('积分余额或规则已变化，请重新核对报价')
    return actual


from rest_framework.response import Response
from rest_framework.views import APIView
from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from wallet.models import get_pass_daily_price
from wallet.services import get_wallet
from .models import EscortProfile
from .pass_rules import pass_purchase_block_reason


class ProviderPassPointsQuoteView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'wallet_write'

    def post(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩本人可查看通行证报价'}, status=403)
        from .pass_license import status as pass_license_status, refusal
        denied = refusal(pass_license_status())
        if denied is not None:
            return denied
        if not isinstance(request.data, Mapping):
            return Response({'code': 400, 'msg': '请选择有效通行证及积分数量'}, status=400)
        tier = request.data.get('tier')
        selected = request.data.get('selected_point_hundredths')
        if (not isinstance(tier, str) or tier not in dict(EscortProfile.PassTier.choices)
                or type(selected) is not int or not 0 <= selected <= 2147483647):
            return Response({'code': 400, 'msg': '请选择有效通行证及积分数量'}, status=400)
        profile = EscortProfile.objects.filter(account=request.account).first()
        if not profile:
            return Response({'code': 403, 'msg': '陪玩资料不完整，请联系客服核对'}, status=403)
        reason = pass_purchase_block_reason(profile)
        if reason:
            return Response({'code': 400, 'msg': reason}, status=400)
        price = get_pass_daily_price(tier)
        try:
            terms, token = preview(request.account, tier, price, selected)
        except (ValidationError, ValueError) as exc:
            message = str(exc.detail[0]) if isinstance(exc, ValidationError) else str(exc)
            return Response({'code': 400, 'msg': message}, status=400)
        wallet = get_wallet(account=request.account, user=request.legacy_user)
        return Response({'code': 0, 'data': {'tier': tier, 'daily_price': price,
            'selected_point_hundredths': selected, **terms['calculation'],
            'wallet_balance': wallet.balance, 'balance_sufficient': wallet.is_active and wallet.balance >= terms['calculation']['payable_units'],
            'rules': terms['rules'], 'quote_token': token}})
