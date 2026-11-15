"""Capture current store commission for a proven purchased service scope."""
from django.conf import settings
from django.utils import timezone
from stores.db import transaction

from .state_machine import IllegalTransitionError


def lock_standard_transfer_plans(order, catalog_service, profiles):
    """Resolve one entry rule per incoming profile without changing the order.

    The caller holds the order, catalog item, provider-dispatch and provider
    profile locks. Existing config and promotion rows are locked before
    resolving, but absent/new configuration row races still require MySQL
    concurrency verification before this opt-in path can be deployed.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('转入抽成快照必须在订单事务内生成')
    from .models import GameCategory, GameCommissionProfile, OrderPaidServiceScope
    from .prestart_handoff import _purchased_service
    from .paid_service_scope import enabled as scope_enabled
    from .principal_segments import enabled_for_order
    from .ordinary_transfer_capability import permits as refunded_transfer_permits
    if ((not scope_enabled() and not enabled_for_order(order) and not refunded_transfer_permits(order))
            or not OrderPaidServiceScope.objects.filter(order=order).exists()):
        raise IllegalTransitionError('原订单服务范围未保存，请先核对')
    purchased, _ = _purchased_service(order, catalog_service)
    game_profile = None
    if purchased.game_category_id:
        if not GameCategory.objects.select_for_update().filter(pk=purchased.game_category_id).exists():
            raise IllegalTransitionError('原订单游戏分类不存在，请先核对')
        game_profile = GameCommissionProfile.objects.select_for_update().filter(
            game_category_id=purchased.game_category_id).first()
    from users.models import EscortLevel
    levels = {level.pk: level for level in EscortLevel.objects.select_for_update().filter(
        pk__in={profile.level_id for profile in profiles if profile.level_id}).order_by('pk')}
    for profile in profiles:
        if profile.level_id:
            if profile.level_id not in levels:
                raise IllegalTransitionError('接替陪玩等级不存在，请先核对')
            profile._state.fields_cache['level'] = levels[profile.level_id]
    from .commission import resolve, global_config, TYPE_KEY, FIXED_KEY
    from wallet.models import SystemConfig, COMMISSION_RATE_KEY
    # Match the order used by CommissionConfigView when it updates these keys.
    locked_values = {}
    for key in (TYPE_KEY, FIXED_KEY, COMMISSION_RATE_KEY):
        row = SystemConfig.objects.select_for_update().filter(key=key).first()
        if row:
            locked_values[key] = row.value
    rules = global_config(values=locked_values)
    from promotions.models import Promotion
    now = timezone.now()
    promotions = Promotion.objects.select_for_update().filter(is_active=True, start_at__lte=now, end_at__gte=now)
    promotion = next((item for item in promotions if item.applies_to(purchased)), None)
    from .pricing import PricingError
    amount = order.amount
    from .models import OrderPlatformSubsidy
    subsidy = OrderPlatformSubsidy.objects.filter(order=order).first()
    if subsidy:
        from .subsidy_segment_capture import transfer_testing_enabled
        if not transfer_testing_enabled() or order.amount != 0:
            raise IllegalTransitionError('安全箱转入抽成基数待核对')
        amount = subsidy.basis
    from .points_service_payment import needs_integrated_processing
    if needs_integrated_processing(order):
        from .points_transfer_capture import locked_deal
        from .points_transfer_revision_scope import active as funding_revision_active
        from rest_framework.exceptions import ValidationError
        try:
            amount = locked_deal(order, allow_existing_allocations=funding_revision_active(order))
        except ValidationError as exc:
            raise IllegalTransitionError('积分订单转入抽成基数待核对') from exc
    try:
        return {profile.account_id: resolve(purchased, amount, profile=profile, promotion=promotion,
                                             game_profile=game_profile, global_rules=rules,
                                             global_rate=rules['commission_rate'])
                for profile in profiles}
    except (PricingError, ValueError) as exc:
        raise IllegalTransitionError(str(exc)) from exc
