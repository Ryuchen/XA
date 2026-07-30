from django.conf import settings
from django.db import transaction

from .models import ClubAccount, LegacyAccountMap


LEGACY_ROLE_TO_ACCOUNT_TYPE = {
    'ADMIN': ClubAccount.AccountType.STAFF,
    'OPERATOR': ClubAccount.AccountType.STAFF,
    'CUSTOMER': ClubAccount.AccountType.BOSS,
    'PROVIDER': ClubAccount.AccountType.PROVIDER,
}


def _sync_legacy_fixture_fields(legacy_user, account):
    if not getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
        return
    updates = []
    for field in ('boss_type_id', 'inviter_commission_rate'):
        value = getattr(legacy_user, field)
        if getattr(account, field) != value:
            setattr(account, field, value)
            updates.append(field)
    if legacy_user.inviter_id:
        inviter_account = get_or_create_account_for_legacy_user(
            legacy_user.inviter,
        )
        if account.inviter_id != inviter_account.id:
            account.inviter = inviter_account
            updates.append('inviter')
    if updates:
        account.save(update_fields=[*updates, 'updated_at'])


def authenticate_account(username, password, account_type=None):
    username = (username or '').strip()
    if not username or not password:
        return None

    queryset = ClubAccount.objects.filter(
        username=username,
        is_active=True,
        can_login=True,
    )
    if account_type:
        queryset = queryset.filter(account_type=account_type)

    account = queryset.first()
    if account is None or not account.check_password(password):
        return None
    return account


@transaction.atomic
def get_or_create_account_for_legacy_user(legacy_user):
    """Return the business account paired with a historical Django user.

    The migration command remains the preferred bulk cut-over path.  This
    helper is the safe, idempotent compatibility path for an account that
    reaches the application before the bulk migration has run (and for the
    legacy API fixtures used by the regression suite).
    """
    mapping = (
        LegacyAccountMap.objects.select_related('account')
        .filter(legacy_user_id=legacy_user.pk)
        .first()
    )
    if mapping:
        _sync_legacy_fixture_fields(legacy_user, mapping.account)
        return mapping.account

    account_type = LEGACY_ROLE_TO_ACCOUNT_TYPE.get(
        getattr(legacy_user, 'role', ''),
    )
    if account_type is None:
        return None

    # A partially completed migration may already have created the account.
    account = ClubAccount.objects.filter(username=legacy_user.username).first()
    if account is None:
        account = ClubAccount(
            username=legacy_user.username,
            password=legacy_user.password,
            last_login=legacy_user.last_login,
            account_type=account_type,
            openid=legacy_user.openid or None,
            phone=legacy_user.phone or None,
            avatar=legacy_user.avatar,
            avatar_url=legacy_user.avatar_url,
            nickname=legacy_user.nickname,
            real_name=legacy_user.real_name,
            is_phone_verified=legacy_user.is_phone_verified,
            is_openid_bound=legacy_user.is_openid_bound,
            game_region=legacy_user.game_region,
            game_nickname=legacy_user.game_nickname,
            game_uid=legacy_user.game_uid,
            inviter_commission_rate=legacy_user.inviter_commission_rate,
            boss_type_id=legacy_user.boss_type_id,
            account_no=legacy_user.boss_no,
            can_login=legacy_user.can_login,
            can_view=legacy_user.can_view,
            is_active=legacy_user.is_active,
            last_active_at=legacy_user.last_active_at,
        )
        account.save()
    elif account.account_type != account_type:
        return None

    LegacyAccountMap.objects.create(
        legacy_user_id=legacy_user.pk,
        account=account,
        legacy_role=legacy_user.role,
    )
    _sync_legacy_fixture_fields(legacy_user, account)
    return account


def link_legacy_relations(legacy_user, account):
    """Backfill dual-write relation columns for one historical account.

    This is deliberately enabled only in the test compatibility mode.  It
    lets the pre-migration regression fixtures exercise the post-migration
    views without weakening production token authentication.
    """
    if not getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
        return

    from django.apps import apps

    aliases = {
        'account': 'user',
        'customer_account': 'customer',
        'provider_account': 'provider',
        'inviter_account': 'inviter',
        'operator_account': 'operator',
        'recipient_account': 'recipient',
        'sender_account': 'sender',
        'applicant_account': 'applicant',
        'boss_account': 'boss',
    }
    for model in apps.get_models():
        field_names = {field.name for field in model._meta.fields}
        for account_field, legacy_field in aliases.items():
            if account_field not in field_names or legacy_field not in field_names:
                continue
            model.objects.filter(**{
                f'{account_field}__isnull': True,
                legacy_field: legacy_user,
            }).update(**{account_field: account})


def migrate_legacy_test_fixtures():
    """Mirror all historical users for legacy regression tests only."""
    if not getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False):
        return
    from django.contrib.auth import get_user_model

    mapped_ids = LegacyAccountMap.objects.values_list('legacy_user_id', flat=True)
    for legacy_user in get_user_model().objects.exclude(id__in=mapped_ids):
        account = get_or_create_account_for_legacy_user(legacy_user)
        if account is not None:
            link_legacy_relations(legacy_user, account)
