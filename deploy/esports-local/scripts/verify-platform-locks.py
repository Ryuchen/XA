"""Exercise platform locks on the registered dev MySQL; never alter existing accounts."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.db import connection, connections, transaction  # noqa: E402
from django.utils import timezone  # noqa: E402
from club_accounts.otp import encrypt_secret, hotp  # noqa: E402
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformOtpCredential, PlatformRoleGrant  # noqa: E402
from stores.platform_accounts import AccountChangeError, update_account  # noqa: E402
from stores.platform_auth import PlatformAuthError, issue_tokens, start_challenge, verify_challenge  # noqa: E402

if (settings.DATABASES['default']['NAME'] != 'xa_test_fronted_acceptance_20261104'
        or settings.DATABASES['default']['HOST'] != '127.0.0.1'
        or str(settings.DATABASES['default']['PORT']) != '13306'):
    raise SystemExit('Isolated acceptance boundary mismatch')
with connection.cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != 'xa_test_fronted_acceptance_20261104':
        raise SystemExit('Unexpected database')
active_supers = lambda: PlatformRoleGrant.objects.filter(role='SUPER_ADMIN', scope_slug='*', is_active=True, account__is_active=True)
if active_supers().exists():
    print(json.dumps({'status': 'skipped', 'reason': 'another active platform super administrator exists'}))
    raise SystemExit(0)

nonce = uuid.uuid4().hex
accounts = []
try:
    with transaction.atomic(using='default'):
        for suffix in ('a', 'b'):
            account = PlatformAccount(username=f'qa-locks-{nonce}-{suffix}', phone='19900000000', is_phone_verified=True)
            account.set_password(secrets.token_urlsafe(24))
            account.save()
            PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN', scope_slug='*')
            PlatformAuditEvent.objects.create(account=account, action='test.fixture_created', target_type='platform_account', target_id=str(account.pk))
            accounts.append(account)
    if active_supers().exclude(account_id__in=[a.pk for a in accounts]).exists():
        raise RuntimeError('An external administrator appeared; test was not run')
    barrier = threading.Barrier(2)
    results = []
    mutex = threading.Lock()
    def disable_self(account):
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
            token = issue_tokens(account, 'SMS', account.phone)[0]
            from rest_framework_simplejwt.tokens import AccessToken
            barrier.wait(timeout=10)
            update_account(AccessToken(token), account.pk, {'expected_updated_at': account.updated_at.isoformat(), 'is_active': False})
            value = 'disabled'
        except AccountChangeError as error:
            value = f'rejected:{error.status}'
        except Exception as error:
            value = f'error:{type(error).__name__}'
        finally:
            connections['default'].close()
        with mutex:
            results.append(value)
    workers = [threading.Thread(target=disable_self, args=(account,)) for account in accounts]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=25)
    if any(worker.is_alive() for worker in workers):
        raise RuntimeError('Concurrent administrator test did not terminate')
    if active_supers().exclude(account_id__in=[a.pk for a in accounts]).exists():
        raise RuntimeError('External administrator appeared during test')
    if sorted(results) != ['disabled', 'rejected:409']:
        raise RuntimeError('Last-super-admin concurrency check failed: ' + ','.join(sorted(results)))
    survivor = PlatformAccount.objects.get(pk__in=[a.pk for a in accounts], is_active=True)
    secret = 'JBSWY3DPEHPK3PXP'
    PlatformOtpCredential.objects.create(account=survivor, secret_ciphertext=encrypt_secret(secret))
    challenge = start_challenge(survivor)['otpChallenge']
    code = hotp(secret, int(timezone.now().timestamp()) // 30)
    barrier = threading.Barrier(2)
    results = []
    def use_same_code():
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
            barrier.wait(timeout=10)
            verify_challenge(survivor, challenge, code, 'OTP')
            value = 'verified'
        except PlatformAuthError:
            value = 'rejected'
        except Exception as error:
            value = f'error:{type(error).__name__}'
        finally:
            connections['default'].close()
        with mutex:
            results.append(value)
    workers = [threading.Thread(target=use_same_code) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(timeout=25)
    if any(worker.is_alive() for worker in workers) or sorted(results) != ['rejected', 'verified']:
        raise RuntimeError('OTP replay concurrency check failed: ' + ','.join(sorted(results)))
    print(json.dumps({'isolated_mysql': True, 'last_super_concurrency': 'passed', 'otp_replay_concurrency': 'passed'}))
finally:
    with transaction.atomic(using='default'):
        for account in PlatformAccount.objects.select_for_update().filter(pk__in=[a.pk for a in accounts], username__startswith=f'qa-locks-{nonce}-'):
            account.is_active = False
            account.security_version = uuid.uuid4()
            account.save(update_fields=['is_active', 'security_version', 'updated_at'])
            PlatformAuditEvent.objects.create(account=account, action='test.fixture_deactivated', target_type='platform_account', target_id=str(account.pk))
