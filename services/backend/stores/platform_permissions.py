"""Explicit platform roles; store employee IDs never confer platform authority."""

from .models import PlatformAccount, PlatformRoleGrant, Store


ROLE_ACTIONS = {
    PlatformRoleGrant.Role.SUPER_ADMIN: frozenset({
        'stores:view', 'stores:manage', 'dashboard:view',
        'platform_accounts:view', 'platform_accounts:manage',
        'store_accounts:view', 'store_accounts:manage',
        'salary:view', 'salary:configure', 'salary:calculate',
        'platform_salary:view', 'platform_salary:configure', 'platform_salary:confirm', 'platform_ledger:view',
        'pass_refund_shortfall:view',
        'withdraw:view',
    }),
    PlatformRoleGrant.Role.FINANCE: frozenset({
        'withdraw:view', 'withdraw:final_review', 'withdraw:record_payment',
        'salary:view', 'salary:configure', 'salary:calculate',
        'platform_salary:view', 'platform_salary:configure', 'platform_salary:confirm', 'platform_ledger:view',
        'pass_refund_shortfall:view', 'pass_refund_repayment:register',
    }),
}
GLOBAL_ACTIONS = frozenset({
    'stores:manage', 'dashboard:view', 'platform_accounts:view', 'platform_accounts:manage',
    'platform_salary:view', 'platform_salary:configure', 'platform_salary:confirm', 'platform_ledger:view',
})


def platform_may(account, action, *, store_slug=None):
    """Fail closed for unknown actors, actions, stores and unscoped requests."""
    if (not isinstance(account, PlatformAccount) or not account.is_active
            or not PlatformAccount.objects.filter(pk=account.pk, is_active=True).exists()):
        return False
    allowed_roles = [role for role, actions in ROLE_ACTIONS.items() if action in actions]
    if not allowed_roles:
        return False
    scopes = ['*']
    if store_slug is not None:
        if not isinstance(store_slug, str) or not Store.objects.filter(slug=store_slug).exists():
            return False
        if action not in GLOBAL_ACTIONS:
            scopes.append(store_slug)
    return PlatformRoleGrant.objects.filter(
        account=account, role__in=allowed_roles, scope_slug__in=scopes, is_active=True,
    ).exists()


def platform_stores_for(account, action):
    """Return directory rows in the caller's explicit scope, never copied business data."""
    if (not isinstance(account, PlatformAccount) or not account.is_active
            or not PlatformAccount.objects.filter(pk=account.pk, is_active=True).exists()):
        return Store.objects.none()
    roles = [role for role, actions in ROLE_ACTIONS.items() if action in actions]
    if not roles:
        return Store.objects.none()
    scopes = set(PlatformRoleGrant.objects.filter(
        account=account, role__in=roles, is_active=True,
    ).values_list('scope_slug', flat=True))
    if '*' in scopes:
        return Store.objects.all()
    if action in GLOBAL_ACTIONS:
        return Store.objects.none()
    return Store.objects.filter(slug__in=scopes)
