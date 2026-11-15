"""Bind payment-time membership rules to the actual points receipt."""
import hashlib
import json

from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderPointsBenefitSnapshot
from .points_checkout_test import require_isolated_checkout


KEYS = ('vip_enabled', 'points_enabled', 'points_rate', 'birthday_multiplier')


def _terms(order):
    if not isinstance(order.funding_terms, dict) or not set(KEYS).issubset(order.funding_terms):
        raise ValidationError('订单会员及积分倍率快照缺失')
    values = {key: order.funding_terms[key] for key in KEYS}
    if (type(values['vip_enabled']) is not bool or type(values['points_enabled']) is not bool
            or type(values['points_rate']) is not int or not 0 <= values['points_rate'] <= 1000
            or type(values['birthday_multiplier']) is not int or not 1 <= values['birthday_multiplier'] <= 10):
        raise ValidationError('订单会员及积分倍率快照无效')
    return values


def _digest(redemption, terms):
    order = redemption.order
    payload = [redemption.pk, order.pk, order.customer_account_id, order.business_cycle,
               redemption.payment_id, redemption.ledger_id, terms]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def capture_locked(redemption):
    from .points_service_payment import _enabled
    if not _enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('付款会员快照须在门店事务内保存')
    terms = _terms(redemption.order)
    return OrderPointsBenefitSnapshot.objects.create(redemption=redemption, terms=terms,
                                                      evidence_hash=_digest(redemption, terms))


def verify_locked(redemption):
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('付款会员快照须在门店事务内核对')
    from .points_business_access import permits
    from .points_transfer_refund_access import permits as transfer_permits
    if not (any(permits(redemption.order, phase=phase, kind=kind) for kind in ('service', 'addon') for phase in ('report', 'settlement'))
            or any(transfer_permits(redemption.order, phase=phase) for phase in ('refund', 'settlement'))):
        try:
            require_isolated_checkout()
        except ValidationError:
            from .points_negotiation_production import require_order_locked
            require_order_locked(redemption.order_id, phase='after')
    snapshot = OrderPointsBenefitSnapshot.objects.select_for_update().filter(redemption=redemption).first()
    terms = _terms(redemption.order)
    if (not snapshot or snapshot.terms != terms or set(snapshot.terms) != set(KEYS)
            or snapshot.evidence_hash != _digest(redemption, terms)):
        raise ValidationError('付款会员及积分倍率凭证缺失或不一致')
    return snapshot
