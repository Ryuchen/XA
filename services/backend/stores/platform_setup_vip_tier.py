"""First-opening VIP tier changes using the normal membership transition."""
from types import SimpleNamespace

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def save_tier(serializer, row, locked_accounts, actor, operation_id, request):
    from club_accounts.models import ClubAccount
    from engagement.vip_transition import resume_guard_available as transition_guard_available
    from engagement.vip_threshold_change import apply, apply_creation, preview, preview_creation

    values = serializer.validated_data
    audit_request = SimpleNamespace(platform_setup_actor=actor, platform_setup_operation_id=operation_id,
                                    method=request.method, path=request.path)
    if row is None:
        if values.get('active', True):
            if not transition_guard_available():
                raise ValidationError('会员重算和既有权益保护尚未开放')
            token = preview_creation(values)['token']
            saved = apply_creation(serializer, token, audit_request, locked_accounts)
        else:
            saved = serializer.save()
    else:
        target_type = values.get('boss_type', row.boss_type)
        if (target_type.pk != row.boss_type_id and ClubAccount.objects.filter(
                account_type='BOSS', boss_type_id=row.boss_type_id).exists()):
            raise ValidationError('该等级仍有关联老板，请先处理会员等级')
        changed = set()
        for key, value in values.items():
            old = getattr(row, key)
            if key in ('safe_box_service_ids', 'companion_service_ids'):
                old, value = old or [], value or []
            if old != value:
                changed.add(key)
        structural = changed & {'recharge_threshold', 'level', 'active'}
        if row.active and values.get('active') is False:
            if changed != {'active'}:
                raise ValidationError('停用等级时请先单独保存其他修改')
            if not transition_guard_available():
                raise ValidationError('会员权益保护尚未开放')
            from engagement.tier_retirement import preview_retirement, retire_tier
            token = preview_retirement(row)['token']
            retire_tier(row, token, audit_request, locked_account_ids=locked_accounts)
            saved = row
            structural = set()
            changed = set()
        else:
            saved = None
        if structural:
            if len(structural) != 1 or changed != structural:
                raise ValidationError('请单独保存等级门槛、顺序或启用状态，再修改其他配置')
            if not transition_guard_available():
                raise ValidationError('会员重算和既有权益保护尚未开放')
            reactivate = 'active' in structural
            renumber = 'level' in structural
            threshold = values.get('recharge_threshold', row.recharge_threshold)
            token = preview(row, threshold, reactivate=reactivate,
                            new_level=values['level'] if renumber else None)['token']
            saved = apply(serializer, token, audit_request, locked_accounts,
                          reactivate=reactivate, renumber=renumber)
        elif saved is None:
            if 'boss_type' in changed:
                raise ValidationError('不能直接更换等级关联的老板分级，请使用等级重算流程')
            saved = serializer.save()
            if changed & {'monthly_discount', 'season_discount', 'safe_box_service_ids', 'benefits'}:
                from .platform_setup_vip_coupon_terms import sync_tier_terms
                sync_tier_terms(saved)
    from engagement.notifier import notify_vip_config_changed
    from coupons.notifier import notify_coupon_catalog_changed
    transaction.on_commit(notify_vip_config_changed)
    transaction.on_commit(notify_coupon_catalog_changed)
    return saved
