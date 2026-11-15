"""Store a verifiable payment-time service scope without rewriting old orders."""
from django.conf import settings
from stores.db import transaction
from .game_commission import service_scope


def enabled():
    from .principal_segments import enabled as principal_segments_enabled
    return bool(getattr(settings, 'PRESTART_SCOPE_SNAPSHOT_ENABLED', False)
                or getattr(settings, 'GAME_COMMISSION_PAYMENT_ENABLED', False)
                or principal_segments_enabled())


def capture(service):
    return service_scope(service) if enabled() else None


def verify(service_id, expected):
    if expected is None:
        return
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('订单服务快照必须在付款事务中核对')
    from .models import ServiceItem
    from users.models import EscortLevel
    current = ServiceItem.objects.select_for_update().get(pk=service_id)
    if current.required_level_id:
        current._state.fields_cache['required_level'] = EscortLevel.objects.select_for_update().get(pk=current.required_level_id)
    if service_scope(current) != expected:
        raise ValueError('服务范围或接单等级已变化，请刷新后重新确认')


def record(order, scope):
    if scope is not None:
        from .models import OrderPaidServiceScope
        OrderPaidServiceScope.objects.create(order=order, scope=scope)


def acceptance_error(profile, order, *, club_rules_enabled=None, allow_refund_recovery=False, refund_recovery_order_id=None,
                     deposit_required_override=None, lock_refund_recovery=False):
    """Use captured payment facts for new orders, preserving legacy behavior."""
    if not enabled():
        return profile.order_acceptance_error(order, provider_gender=order.provider_gender,
                                              club_rules_enabled=club_rules_enabled, allow_refund_recovery=allow_refund_recovery,
                                              refund_recovery_order_id=refund_recovery_order_id,
                                              deposit_required_override=deposit_required_override,
                                              lock_refund_recovery=lock_refund_recovery)
    from django.core.exceptions import ObjectDoesNotExist
    try:
        order.paid_service_scope
    except ObjectDoesNotExist:
        return profile.order_acceptance_error(order, provider_gender=order.provider_gender,
                                              club_rules_enabled=club_rules_enabled, allow_refund_recovery=allow_refund_recovery,
                                              refund_recovery_order_id=refund_recovery_order_id,
                                              deposit_required_override=deposit_required_override,
                                              lock_refund_recovery=lock_refund_recovery)
    from copy import copy
    from .prestart_handoff import _purchased_service
    from .state_machine import IllegalTransitionError
    try:
        purchased, _ = _purchased_service(order, order.service, verify_commission=False)
    except IllegalTransitionError:
        return '订单原服务范围待核对，暂不能接单'
    admission = copy(order)
    admission._state = copy(order._state)
    admission._state.fields_cache = {**order._state.fields_cache, 'service': purchased}
    reason = profile.order_acceptance_error(admission, provider_gender=order.provider_gender,
                                             club_rules_enabled=club_rules_enabled, allow_refund_recovery=allow_refund_recovery,
                                             refund_recovery_order_id=refund_recovery_order_id,
                                             deposit_required_override=deposit_required_override,
                                             lock_refund_recovery=lock_refund_recovery)
    if reason:
        return reason
    fields = ('game_category_id', 'provider_kind', 'platform', 'security_level',
              'provider_grade', 'gameplay_id', 'catalog_family')
    if purchased.game_category_id and any(getattr(purchased, key) != getattr(order.service, key) for key in fields):
        from users.qualifications import service_skill
        if not service_skill(profile, admission, exclude_service_ids={order.service_id}):
            return '陪玩未配置可核实的原服务技能'
    return ''
