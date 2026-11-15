"""Executed inside the dedicated packaged web container; credentials come from stdin."""
import json
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from club_accounts.models import ClubAccount, LegacyAccountMap
from console.models import AdminMembership, AdminRole
from console.permissions import ALL_PERMISSION_SET
from users.models import EscortProfile
from wallet.models import Wallet

if (os.environ.get('XA_LOCAL_VALIDATION') != 'true'
        or settings.DATABASES['default']['NAME'] != 'xa_round01'
        or settings.DATABASES['default']['HOST'] != 'db'
        or settings.DATABASES['default']['USER'] != 'xa_round01'
        or settings.DEBUG):
    raise SystemExit('Refusing non-isolated database or debug environment')
with connection.cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != 'xa_round01':
        raise SystemExit('Unexpected connected database')
executor = MigrationExecutor(connection)
if executor.migration_plan(executor.loader.graph.leaf_nodes()):
    raise SystemExit('Database migrations are not complete')

credentials = json.load(sys.stdin)
if (set(credentials) != {'project', 'accounts'}
        or not credentials['project'].startswith('xa-round01-')
        or set(credentials['accounts']) != {'sl_round01_admin', 'sl_round01_provider'}
        or any(not isinstance(value, str) or len(value) < 20
               for value in credentials['accounts'].values())):
    raise SystemExit('Unexpected credential file format')

User = get_user_model()
admin_name, provider_name = 'sl_round01_admin', 'sl_round01_provider'
role_code = 'round01_preview_all'
passwords = credentials['accounts']

with transaction.atomic():
    accounts = {a.username: a for a in ClubAccount.objects.select_for_update().filter(
        username__in=[admin_name, provider_name])}
    legacy = {u.username: u for u in User.objects.select_for_update().filter(
        username__in=[admin_name, provider_name])}
    role = AdminRole.objects.select_for_update().filter(code=role_code).first()
    super_role = AdminRole.objects.select_for_update().filter(code='super_admin', is_active=True).first()
    if not super_role:
        raise SystemExit('Built-in super-admin role is unavailable; no changes made')
    if accounts or legacy or role:
        if set(accounts) != {admin_name, provider_name} or set(legacy) != {admin_name, provider_name} or not role:
            raise SystemExit('Existing records conflict; no changes made')
        admin, provider = accounts[admin_name], accounts[provider_name]
        if (admin.account_type != 'STAFF' or provider.account_type != 'PROVIDER'
                or any(not a.is_active or not a.can_login or not a.check_password(passwords[a.username])
                       for a in (admin, provider))
                or not role.is_active or set(role.permissions) != ALL_PERMISSION_SET):
            raise SystemExit('Existing accounts or role differ; no changes made')
        for account in (admin, provider):
            user = legacy[account.username]
            if not LegacyAccountMap.objects.filter(account=account, legacy_user_id=user.pk).exists():
                raise SystemExit('Existing identity mapping differs; no changes made')
            if not Wallet.objects.filter(user=user, account=account).exists():
                raise SystemExit('Existing wallet mapping differs; no changes made')
        if (not AdminMembership.objects.filter(account=admin, user=legacy[admin_name],
                                                is_active=True, roles=role).exists()
                or not AdminMembership.objects.filter(account=admin, roles=super_role).exists()
                or not EscortProfile.objects.filter(account=provider, user=legacy[provider_name]).exists()):
            raise SystemExit('Existing membership or profile differs; no changes made')
        print('Existing isolated preview accounts verified; no records changed.')
    else:
        if AdminRole.objects.filter(name='首轮隔离预览管理员').exists():
            raise SystemExit('Role name already exists; no changes made')
        role = AdminRole.objects.create(name='首轮隔离预览管理员', code=role_code,
                                        permissions=sorted(ALL_PERMISSION_SET))
        for name, account_type in ((admin_name, 'STAFF'), (provider_name, 'PROVIDER')):
            account = ClubAccount.objects.create_account(username=name, password=passwords[name],
                account_type=account_type, nickname='隔离预览管理员' if name == admin_name else '隔离预览陪玩')
            user = User.objects.create_user(username=name, password=None,
                role=User.Role.ADMIN if name == admin_name else User.Role.PROVIDER,
                nickname=account.nickname, is_staff=False)
            LegacyAccountMap.objects.create(account=account, legacy_user_id=user.pk,
                legacy_role=User.Role.ADMIN if name == admin_name else User.Role.PROVIDER)
            updated = Wallet.objects.filter(user=user, account__isnull=True).update(account=account)
            if updated != 1:
                raise RuntimeError('Unexpected wallet state; transaction rolled back')
            if name == admin_name:
                membership = AdminMembership.objects.create(user=user, account=account, is_active=True)
                membership.roles.add(role, super_role)
            else:
                EscortProfile.objects.create(user=user, account=account, display_name='隔离预览陪玩',
                                             is_verified=True, status=EscortProfile.Status.AVAILABLE)
        print('Created two isolated preview accounts; credentials are not printed.')
