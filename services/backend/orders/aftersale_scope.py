"""Case scope is immutable for an open case and store-local to its order."""

from rest_framework import serializers


def normalize_scope(value):
    """Normalize the submitted intent without consulting mutable order state."""
    if value is None:
        return None
    if (not isinstance(value, list) or not value or
            any(type(item) is not int or item <= 0 for item in value) or len(set(value)) != len(value)):
        raise serializers.ValidationError('请选择本单的陪玩，或选择整单售后')
    return sorted(value)


def validate_scope(order, value):
    if value is None:
        return
    if order.status != order.Status.COMPLETED or order.is_gift_snapshot or order.service_clock_version is None:
        raise serializers.ValidationError('当前订单暂不支持按陪玩选择售后范围')
    known = set(order.providers.exclude(provider_account_id__isnull=True)
                .values_list('provider_account_id', flat=True))
    if order.provider_account_id:
        known.add(order.provider_account_id)
    if not set(value).issubset(known):
        raise serializers.ValidationError('所选陪玩不属于该订单，请刷新后重试')


def applies_to(case, account_id):
    """Unscoped, malformed or unidentifiable cases block conservatively."""
    ids = case.affected_provider_account_ids
    if (not isinstance(ids, list) or not ids
            or any(type(item) is not int or item <= 0 for item in ids)):
        return True
    return not account_id or account_id in ids


def visible_status(order, account_id):
    return next((row.status for row in order.aftersales.all() if applies_to(row, account_id)), '')
