"""Small, explicitly allowlisted surface for an unopen store's first admin."""
import re

from .models import Store, StoreProvisioningClaim


def is_setup_admin(store, account):
    if store.status != Store.Status.CONFIGURING or store.initialized_at is None or not account:
        return False
    membership = getattr(account, 'admin_membership', None)
    return bool(account._state.db == store.database_alias and account.username
                and account.account_type == 'STAFF' and account.is_active and account.can_login
                and membership and membership.is_active
                and membership.roles.filter(code='super_admin', is_active=True).exists()
                and StoreProvisioningClaim.objects.filter(store=store, admin_username=account.username).exists())


_EXACT = {
    '/api/stores/current': {'GET'},
    '/api/admin/auth/profile': {'GET'},
    '/api/admin/auth/refresh': {'POST'},
    '/api/admin/auth/otp-setup': {'GET', 'POST', 'DELETE'},
    '/api/admin/license/activate': {'GET', 'POST'},
    '/api/messages': {'GET'},
    '/api/messages/unread-count': {'GET'},
    '/api/messages/read-all': {'POST'},
    '/api/admin/dictionaries': {'GET'},
    '/api/admin/dictionary-items': {'GET', 'POST'},
    '/api/admin/dictionary-options': {'GET'},
    '/api/admin/catalog-image': {'POST'},
    '/api/admin/club-content-image': {'POST'},
    '/api/admin/club-content': {'GET', 'POST'},
    '/api/admin/qualification-options': {'GET'},
    '/api/admin/options/game-categories': {'GET'},
    '/api/admin/options/service-categories': {'GET'},
    '/api/admin/options/gameplays': {'GET'},
    '/api/admin/options/escort-levels': {'GET'},
    '/api/admin/options/boss-types': {'GET'},
    '/api/admin/options/service-items': {'GET'},
    '/api/admin/config/provider-capacity': {'GET', 'PUT'},
    '/api/admin/config/grab-window': {'GET', 'PUT'},
    '/api/admin/config/provider-pass': {'GET', 'PUT'},
    '/api/admin/config/provider-pass/availability': {'GET', 'PUT'},
    '/api/admin/config/provider-pass/no-pass-delay': {'GET', 'PUT'},
    '/api/admin/config/points-redemption': {'GET', 'PUT'},
    '/api/admin/config/points-usage': {'GET', 'PUT'},
    '/api/admin/config/checkin': {'GET', 'PUT'},
    '/api/admin/config/kook-dispatch': {'GET', 'PUT'},
    '/api/admin/config/kook-points': {'GET', 'PUT'},
    '/api/admin/kook-points-status': {'GET'},
    '/api/admin/config/wecom-dispatch': {'GET', 'PUT'},
    '/api/admin/config/kook-dispatch/preview': {'POST'},
    '/api/admin/config/order-account-fields/targets': {'GET'},
    '/api/admin/config/commission': {'GET', 'PUT'},
    '/api/admin/config/referral-draft': {'GET', 'PUT'},
    '/api/admin/config/settlement-clocks': {'GET', 'PUT'},
    '/api/admin/config/deposit-rules': {'GET', 'PUT'},
    '/api/admin/config/deposit-targets': {'PUT'},
    '/api/admin/config/withdraw': {'GET', 'PUT'},
    '/api/admin/config/game-commissions': {'GET'},
    '/api/admin/config/game-commissions/coverage': {'GET'},
    '/api/admin/order-traffic': {'GET', 'PUT'},
    '/api/admin/config/payment-fee': {'GET', 'PUT'},
    '/api/admin/config/payment-fee/history': {'GET'},
    '/api/admin/config/payment-fee/preview': {'POST'},
    '/api/admin/config/order-timeout': {'GET', 'PUT'},
    '/api/admin/config/order-timeout/history': {'GET'},
    '/api/admin/config/reservation-deadlines': {'GET', 'PUT'},
    '/api/admin/config/room-confirmation': {'GET', 'PUT'},
    '/api/admin/game-categories': {'GET', 'POST'},
    '/api/admin/service-categories': {'GET', 'POST'},
    '/api/admin/service-items': {'GET', 'POST'},
    '/api/admin/escort-levels': {'GET', 'POST'},
    '/api/admin/achievements': {'GET', 'POST'},
    '/api/admin/checkin-gifts': {'GET', 'POST'},
    '/api/admin/coupons': {'GET', 'POST'},
    '/api/admin/coupons/service-options': {'GET'},
    '/api/admin/promotions': {'GET', 'POST'},
    '/api/admin/gameplays': {'GET', 'POST'},
    '/api/admin/boss-types': {'GET', 'POST'},
    '/api/admin/vip-tiers': {'GET', 'POST'},
    '/api/admin/vip-tiers/service-options': {'GET'},
    '/api/admin/club-rewards': {'GET', 'POST'},
    '/api/admin/lotteries': {'GET', 'POST'},
    '/api/admin/gift-benefits': {'GET', 'POST'},
    '/api/admin/recharge-packages': {'GET', 'POST'},
    '/api/admin/wallets/recharge-packages': {'GET'},
    '/api/admin/wallets/recharge-quote': {'GET'},
    '/api/admin/salary-plans': {'GET', 'POST'},
    '/api/admin/payroll/config': {'GET', 'PUT'},
    '/api/admin/support-cards': {'GET', 'POST'},
    '/api/admin/vip-tiers/creation-preview': {'POST'},
    '/api/admin/vip-tiers/reorder-preview': {'POST'},
    '/api/admin/vip-tiers/reorder': {'POST'},
    '/api/admin/club-config': {'GET', 'PUT'},
    '/api/admin/club-config/vip-resume-preview': {'GET'},
}
_DETAILS = (
    (re.compile(r'^/api/messages/[1-9][0-9]*$'), {'GET'}),
    (re.compile(r'^/api/admin/dictionaries/setup-confirmation/(capacity|grab_window|reservation_deadlines|room_confirmation|settlement_clocks|deposit_targets|deposit_percent|withdraw|provider_pass|no_pass_delay|recharge_packages|points_award_rates|gift_pricing)$'), {'GET', 'POST'}),
    (re.compile(r'^/api/admin/dictionary-items/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/config/game-commissions/[1-9][0-9]*$'), {'GET', 'PUT'}),
    (re.compile(r'^/api/admin/config/game-commissions/[1-9][0-9]*/preview/[1-9][0-9]*$'), {'GET'}),
    (re.compile(r'^/api/admin/config/service-addons/[1-9][0-9]*$'), {'GET'}),
    (re.compile(r'^/api/admin/config/service-addons/[1-9][0-9]*/[1-9][0-9]*$'), {'GET', 'PUT'}),
    (re.compile(r'^/api/admin/config/service-addons/[1-9][0-9]*/[1-9][0-9]*/price-options$'), {'GET', 'PUT'}),
    (re.compile(r'^/api/admin/config/order-account-fields/(game|service)/[1-9][0-9]*$'), {'GET', 'PUT'}),
    (re.compile(r'^/api/admin/(game-categories|service-categories|service-items|gameplays)/[1-9][0-9]*$'),
     {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/escort-levels/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/escort-levels/[1-9][0-9]*/deletion-impact$'), {'GET'}),
    (re.compile(r'^/api/admin/achievements/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/checkin-gifts/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/(coupons|promotions)/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/(boss-types|vip-tiers)/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH'}),
    (re.compile(r'^/api/admin/club-rewards/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/lotteries/[1-9][0-9]*$'), {'GET', 'DELETE'}),
    (re.compile(r'^/api/admin/lotteries/[1-9][0-9]*/configure$'), {'POST'}),
    (re.compile(r'^/api/admin/gift-benefits/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/recharge-packages/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/(salary-plans|support-cards)/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/club-content/[1-9][0-9]*$'), {'GET', 'PUT', 'PATCH', 'DELETE'}),
    (re.compile(r'^/api/admin/vip-tiers/[1-9][0-9]*/(recalculation-preview|retirement-preview)$'), {'GET', 'POST'}),
    (re.compile(r'^/api/admin/gameplays/[1-9][0-9]*/restore$'), {'POST'}),
)


def setup_request_allowed(path, method):
    """No wildcard prefixes: future APIs do not become accessible by accident."""
    path = path.rstrip('/')
    return method in _EXACT.get(path, ()) or any(pattern.fullmatch(path) and method in methods
                                                   for pattern, methods in _DETAILS)
