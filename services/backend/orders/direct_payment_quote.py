"""Test-only server-owned fee quote and immutable contract verification.

An estimate is not a channel settlement fee. The settings-only policy is not
a production configuration surface; payment endpoints remain unavailable.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from django.conf import settings

from .payment_fee_quote import (PaymentFeeQuoteError, ROUND_DOWN, SERVICE_AMOUNT,
                                quote_payment_fee)

MAX_ORDER_FEN = 2147483647
# Test-only compatibility with the current ledger convention: ten internal
# units per 兴安币, ten 兴安币 per yuan. Production must verify and version
# this exchange rate rather than silently treating internal units as cents.
TEST_INTERNAL_UNITS_PER_YUAN = 100
FEN_PER_YUAN = 100


def _conversion_policy():
    from .direct_payment import DirectPaymentError
    raw = getattr(settings, 'WECHAT_DIRECT_TEST_UNIT_CONVERSION', None)
    if raw is None:
        raw = {'version': 'TEST_CURRENT_100_V1', 'units_per_yuan': TEST_INTERNAL_UNITS_PER_YUAN}
    if (not isinstance(raw, dict) or set(raw) != {'version', 'units_per_yuan'}
            or not isinstance(raw['version'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', raw['version'])
            or type(raw['units_per_yuan']) is not int or not 1 <= raw['units_per_yuan'] <= 1000000):
        raise DirectPaymentError('测试金额兑换配置无效')
    return dict(raw)


def _convert(units, units_per_yuan, fen_per_yuan):
    from .direct_payment import DirectPaymentError
    if type(units) is not int or units <= 0:
        raise DirectPaymentError('服务金额无效')
    if (type(units_per_yuan) is not int or not 1 <= units_per_yuan <= 1000000
            or fen_per_yuan != FEN_PER_YUAN):
        raise DirectPaymentError('兑换比例无效')
    fen, remainder = divmod(units * fen_per_yuan, units_per_yuan)
    if remainder or fen > MAX_ORDER_FEN:
        raise DirectPaymentError('服务金额无法精确换算为分')
    return fen


def service_units_to_fen(units):
    policy = _conversion_policy()
    return _convert(units, policy['units_per_yuan'], FEN_PER_YUAN)


@dataclass(frozen=True)
class DirectQuote:
    policy_id: str
    service_units: int
    conversion_version: str
    units_per_yuan: int
    fen_per_yuan: int
    conversion_rounding: str
    service_fen: int
    fee_fen: int
    total_fen: int
    rate_numerator: int
    rate_denominator: int
    fee_basis: str
    rounding: str
    quote_hash: str
    configured: bool


def quote_direct_payment(*, service_units, appid, mchid):
    from .direct_payment import DirectPaymentError, _require_test_domain
    _require_test_domain()
    if not isinstance(appid, str) or not appid or not isinstance(mchid, str) or not mchid:
        raise DirectPaymentError('支付商户配置无效')
    conversion = _conversion_policy()
    service_fen = _convert(service_units, conversion['units_per_yuan'], FEN_PER_YUAN)
    raw = getattr(settings, 'WECHAT_DIRECT_TEST_FEE_POLICY', None)
    # Any explicit fee *or conversion* policy requires the buyer's confirmed
    # total and hash. Only the unchanged default test contract uses its legacy
    # expected_amount confirmation.
    configured = raw is not None or getattr(settings, 'WECHAT_DIRECT_TEST_UNIT_CONVERSION', None) is not None
    if raw is None:
        policy = {'policy_id': 'UNCONFIGURED_ZERO_V1', 'rate_numerator': 0, 'rate_denominator': 1,
                  'fee_basis': SERVICE_AMOUNT, 'rounding': ROUND_DOWN}
    elif isinstance(raw, dict) and set(raw) == {'policy_id', 'rate_numerator', 'rate_denominator', 'fee_basis', 'rounding'}:
        policy = dict(raw)
    else:
        raise DirectPaymentError('测试手续费配置不完整')
    if not isinstance(policy['policy_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', policy['policy_id']):
        raise DirectPaymentError('测试手续费策略版本无效')
    try:
        result = quote_payment_fee(service_fen=service_fen, rate_numerator=policy['rate_numerator'],
                                   rate_denominator=policy['rate_denominator'], fee_basis=policy['fee_basis'],
                                   rounding=policy['rounding'])
    except PaymentFeeQuoteError as exc:
        raise DirectPaymentError('测试手续费配置或金额无效') from exc
    if result.payment_total_fen > MAX_ORDER_FEN:
        raise DirectPaymentError('支付总额超出支持范围')
    values = {'policy_id': policy['policy_id'], 'service_units': service_units,
              'conversion_version': conversion['version'], 'units_per_yuan': conversion['units_per_yuan'],
              'fen_per_yuan': FEN_PER_YUAN, 'conversion_rounding': 'EXACT', 'service_fen': result.service_fen,
              'fee_fen': result.estimated_fee_fen, 'total_fen': result.payment_total_fen,
              'rate_numerator': policy['rate_numerator'], 'rate_denominator': policy['rate_denominator'],
              'fee_basis': policy['fee_basis'], 'rounding': policy['rounding']}
    material = {'contract_version': 'FEE_QUOTE_V2', 'appid': appid, 'mchid': mchid, **values}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return DirectQuote(**values, quote_hash=digest, configured=configured)


def snapshot_values(quote):
    data = asdict(quote)
    data.pop('configured')
    return data


def quote_matches_intent(intent, order):
    """Old test intents have no quote and retain the legacy T=S contract."""
    from .direct_payment import DirectPaymentError, _require_test_domain
    from .direct_payment_quote_models import DirectPaymentQuote
    # Until production provenance exists, even a saved PAID staging intent
    # must not authorize assignment or grabbing outside an isolated test DB.
    try:
        _require_test_domain()
    except DirectPaymentError:
        return False
    # Caller may have selected the one-to-one quote with the intent. A cached
    # None means it was selected and is absent; do not issue an N+1 query.
    if 'fee_quote' in intent._state.fields_cache:
        snapshot = intent._state.fields_cache['fee_quote']
    else:
        snapshot = DirectPaymentQuote.objects.filter(intent=intent).first()
    if snapshot is None:
        # Equal numbers do not prove the historical conversion contract.
        return False
    if (snapshot.contract_version != 'FEE_QUOTE_V2' or snapshot.service_units != order.amount
            or snapshot.conversion_rounding != 'EXACT' or not snapshot.conversion_version
            or snapshot.total_fen != intent.amount_fen or snapshot.total_fen != snapshot.service_fen + snapshot.fee_fen):
        return False
    try:
        if _convert(snapshot.service_units, snapshot.units_per_yuan, snapshot.fen_per_yuan) != snapshot.service_fen:
            return False
    except DirectPaymentError:
        return False
    values = {key: getattr(snapshot, key) for key in ('policy_id', 'service_units', 'conversion_version',
        'units_per_yuan', 'fen_per_yuan', 'conversion_rounding', 'service_fen', 'fee_fen', 'total_fen',
                                                      'rate_numerator', 'rate_denominator', 'fee_basis', 'rounding')}
    material = {'contract_version': snapshot.contract_version, 'appid': intent.appid, 'mchid': intent.mchid, **values}
    digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        calculated = quote_payment_fee(service_fen=snapshot.service_fen, rate_numerator=snapshot.rate_numerator,
            rate_denominator=snapshot.rate_denominator, fee_basis=snapshot.fee_basis, rounding=snapshot.rounding)
    except PaymentFeeQuoteError:
        return False
    return (digest == snapshot.quote_hash and calculated.estimated_fee_fen == snapshot.fee_fen
            and calculated.payment_total_fen == snapshot.total_fen)
