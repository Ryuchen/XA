from .settings import *  # noqa: F403


# The historical migration chain cannot build a fresh SQLite test database
# because an old wallet data migration references a later CustomUser field.
# Unit tests sync current local-app models while retaining Django core migrations.
MIGRATION_MODULES = {
    app: None
    for app in (
        'announcements',
        'audition',
        'banners',
        'chat',
        'club_accounts',
        'console',
        'coupons',
        'orders',
        'promotions',
        'site_messages',
        'support',
        'users',
        'wallet',
    )
}

# Most historical API tests use DRF's force_authenticate(CustomUser).  Keep
# that fixture style working while production accepts business-account JWTs
# only; the permission layer lazily mirrors and links the fixture account.
LEGACY_FORCE_AUTH_COMPAT = True
REQUIRE_ORDER_EVIDENCE_IMAGES = False
