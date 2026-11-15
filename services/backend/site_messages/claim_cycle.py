"""Resolve entitlement recipient cycles from persisted entitlement origins."""


def recipient_cycle(claim):
    if claim.source == 'GIFT' and claim.source_order_id:
        order = claim.source_order
        return order.business_cycle if order.customer_account_id == claim.account_id else None
    if claim.source == 'LOTTERY':
        from engagement.models import LotteryDraw
        return LotteryDraw.objects.filter(claim=claim, account_id=claim.account_id,
            event_key=claim.event_key).values_list('business_cycle', flat=True).first()
    prefixes = {'REDEMPTION': ('redeem',), 'VIP': ('vip-quota', 'vip-birthday-gift')}
    parts = claim.event_key.split(':')
    if (len(parts) < 4 or parts[0] not in prefixes.get(claim.source, ())
            or not parts[1].isascii() or not parts[1].isdigit()
            or not parts[2].isascii() or not parts[2].isdigit()
            or int(parts[1]) != claim.account_id or int(parts[2]) <= 0):
        return None
    return int(parts[2])
