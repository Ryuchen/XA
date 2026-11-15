"""Create or deactivate only uniquely named platform QA accounts in the registered dev DB."""
import json
import os
from pathlib import Path
import secrets
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.db import connection, transaction  # noqa: E402
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant  # noqa: E402

if (settings.DATABASES['default']['NAME'] != 'xa_test_fronted_acceptance_20261104'
        or settings.DATABASES['default']['HOST'] != '127.0.0.1'
        or str(settings.DATABASES['default']['PORT']) != '13306'
        or any((settings.SMS_SECRET_ID, settings.SMS_SECRET_KEY, settings.SMS_APP_ID,
                settings.SMS_TEMPLATE_ID, settings.SMS_SIGN_NAME))):
    raise SystemExit('Isolated acceptance boundary mismatch')
with connection.cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != 'xa_test_fronted_acceptance_20261104':
        raise SystemExit('Unexpected database')

action = sys.argv[1] if len(sys.argv) > 1 else ''
if action == 'create':
    nonce = uuid.uuid4().hex
    phone = '199' + str(secrets.randbelow(100000000)).zfill(8)
    finance_phone = '198' + str(secrets.randbelow(100000000)).zfill(8)
    password = secrets.token_urlsafe(24)
    with transaction.atomic(using='default'):
        account = PlatformAccount(username='qa-platform-' + nonce, nickname='隔离验收平台管理员', phone=phone)
        account.set_password(password)
        account.save()
        PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN', scope_slug='*')
        PlatformAuditEvent.objects.create(account=account, action='test.fixture_created', target_type='platform_account', target_id=str(account.pk))
    # Only the parent acceptance process receives these temporary credentials.
    print(json.dumps({'nonce': nonce, 'username': account.username, 'password': password,
                      'phone': phone, 'finance_phone': finance_phone}))
elif action == 'finish' and len(sys.argv) == 3:
    try:
        nonce = uuid.UUID(hex=sys.argv[2]).hex
    except ValueError:
        raise SystemExit('Invalid test fixture identifier') from None
    names = ['qa-platform-' + nonce, 'qa-finance-' + nonce]
    with transaction.atomic(using='default'):
        for account in PlatformAccount.objects.select_for_update().filter(username__in=names):
            account.is_active = False
            account.security_version = uuid.uuid4()
            account.save(update_fields=['is_active', 'security_version', 'updated_at'])
            PlatformAuditEvent.objects.create(account=account, action='test.fixture_deactivated', target_type='platform_account', target_id=str(account.pk))
    print(json.dumps({'status': 'test_accounts_deactivated'}))
else:
    raise SystemExit('Usage: create | finish TEST_NONCE')
