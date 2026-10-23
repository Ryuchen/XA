"""The purchased participant count, independent of later catalog edits."""
from .models import Order


def required_team_size(order):
    # Existing paid DOUBLE orders retain their agreed mode and original shares,
    # including historical packages which stored a total price with multiplier 1.
    if order.escort_mode == Order.EscortMode.DOUBLE:
        return 2
    if order.required_provider_count in (1, 2):
        return order.required_provider_count
    # Older SINGLE rows only permit a team when their original snapshot says 2.
    # Never infer this from the current service price, title or participant rows.
    snapshot = order.gameplay_snapshot or {}
    return 2 if snapshot.get('billing_provider_count') == 2 else 1


def team_size_error(order, size):
    required = required_team_size(order)
    if size != required:
        return '该订单为单陪套餐，仅支持一人报名' if required == 1 else '该订单为双陪套餐，须由两名陪玩组队报名'
    return ''
