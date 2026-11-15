"""Race only unique synthetic config keys and accounts in an isolated store."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connections  # noqa: E402
from django.test import override_settings  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user  # noqa: E402
from club_accounts.tokens import issue_account_tokens  # noqa: E402
from console.models import AdminMembership, AdminRole  # noqa: E402
import console.views as console_views  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
import wallet.withdraw_rules as rules  # noqa: E402
from wallet.models import Transaction, Wallet, WithdrawRequest  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

stores = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104', 'sl.esports.club'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104', 'xa.esports.club')}
if len(sys.argv) != 2 or sys.argv[1] not in stores:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, database, provider_host = stores[slug]
config = settings.DATABASES[alias]
if config['NAME'] != database or config['HOST'] != '127.0.0.1' or str(config['PORT']) != '13306':
    raise SystemExit('Isolated database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != database:
        raise SystemExit('Unexpected actual database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')
nonce = uuid.uuid4().hex
names = ['qa-withdraw-rule-provider-' + nonce, 'qa-withdraw-rule-staff-' + nonce,
         'qa-withdraw-rule-reviewer-' + nonce]
keys = {key: f'qa_withdraw_{name}_{nonce}' for key, name in [
    ('MIN_WITHDRAW_AMOUNT_KEY', 'min'), ('WITHDRAW_TAX_RATE_KEY', 'rate'),
    ('BALANCE_THRESHOLD_KEY', 'threshold'), ('LOCK_KEY', 'lock')]}
changes = [patch.object(rules, name, value) for name, value in keys.items()]
changes += [patch.object(console_views, name, keys[name]) for name in ('MIN_WITHDRAW_AMOUNT_KEY', 'WITHDRAW_TAX_RATE_KEY')]
for change in changes:
    change.start()
with override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    try:
        with store_context(store), transaction.atomic():
            User = get_user_model()
            provider = User.objects.create_user(username=names[0], password=secrets.token_urlsafe(24), role='PROVIDER')
            staff = User.objects.create_user(username=names[1], password=secrets.token_urlsafe(24), role='OPERATOR')
            reviewer = User.objects.create_user(username=names[2], password=secrets.token_urlsafe(24), role='OPERATOR')
            provider_account = get_or_create_account_for_legacy_user(provider)
            staff_account = get_or_create_account_for_legacy_user(staff)
            reviewer_account = get_or_create_account_for_legacy_user(reviewer)
            for account in (provider_account, staff_account, reviewer_account):
                account.is_phone_verified = True
                account.save(update_fields=['is_phone_verified'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            role = AdminRole.objects.create(name='隔离提现规则测试-' + nonce, code='qa-wr-' + nonce,
                                            permissions=['withdraw:view', 'withdraw:config'])
            membership.roles.add(role)
            reviewer_membership = AdminMembership.objects.create(user=reviewer, account=reviewer_account)
            reviewer_role = AdminRole.objects.create(name='隔离提现初审测试-' + nonce, code='qa-review-' + nonce,
                                                     permissions=['withdraw:view', 'withdraw:audit'])
            reviewer_membership.roles.add(reviewer_role)
            wallet = get_wallet(account=provider_account, user=provider, for_update=True)
            wallet.balance = 10000
            wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, tx_type='ADJUST', amount=10000,
                                       balance_before=0, balance_after=10000, remark='隔离测试初始余额')
            staff_token = issue_account_tokens(staff_account, auth_method='password_sms')[0]
            reviewer_token = issue_account_tokens(reviewer_account, auth_method='password_sms')[0]
            provider_token = issue_account_tokens(provider_account, auth_method='password_sms')[0]
        admin = APIClient(HTTP_HOST='admin.esports.club')
        admin.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        provider_client = APIClient(HTTP_HOST=provider_host)
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        initial = admin.put('/api/admin/config/withdraw', {'min_amount':100, 'balance_threshold':0, 'tax_rate':0}, format='json')
        if initial.data.get('code') != 0:
            raise RuntimeError('Initial isolated config failed')
        reviewer_client = APIClient(HTTP_HOST='admin.esports.club')
        reviewer_client.credentials(HTTP_AUTHORIZATION='Bearer ' + reviewer_token)
        before = reviewer_client.get('/api/admin/config/withdraw').data
        denied = reviewer_client.put('/api/admin/config/withdraw',
            {'min_amount':500, 'balance_threshold':100, 'tax_rate':25}, format='json')
        if denied.status_code != 403 or reviewer_client.get('/api/admin/config/withdraw').data != before:
            raise RuntimeError('Reviewer could alter isolated withdrawal configuration')
        quote = provider_client.get('/api/wallet/withdraw/', {'amount':1000}).data['data']
        held, release = threading.Event(), threading.Event()
        original = rules.lock_withdraw_rules
        def observe_lock():
            value = original()
            if threading.current_thread().name == 'setter':
                held.set()
                if not release.wait(10):
                    raise RuntimeError('Timed out waiting to release config lock')
            return value
        results = {}
        def edit():
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                c = APIClient(HTTP_HOST='admin.esports.club')
                c.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                response = c.put('/api/admin/config/withdraw', {'min_amount':100, 'balance_threshold':500,
                    'tax_rate':0, 'expected_version':quote['config_version']}, format='json')
                results['edit'] = response.data.get('code')
            except Exception as error:
                results['edit'] = type(error).__name__
            finally:
                connections[alias].close()
        def submit():
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                c = APIClient(HTTP_HOST=provider_host)
                c.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
                response = c.post('/api/wallet/withdraw/', {'amount':1000, 'payee_method':'WECHAT',
                    'payee_account':'qa-only', 'payee_name':'隔离测试', 'client_request_id':'qa-rule-' + nonce,
                    'expected_config_version':quote['config_version']}, format='json')
                results['submit'] = response.data.get('errorCode')
            except Exception as error:
                results['submit'] = type(error).__name__
            finally:
                connections[alias].close()
        with patch.object(rules, 'lock_withdraw_rules', side_effect=observe_lock):
            editor = threading.Thread(target=edit, name='setter')
            applicant = threading.Thread(target=submit, name='applicant')
            editor.start()
            if not held.wait(10):
                raise RuntimeError('Config lock not acquired')
            applicant.start()
            release.set()
            editor.join(timeout=25); applicant.join(timeout=25)
            if editor.is_alive() or applicant.is_alive():
                raise RuntimeError('Concurrent config/withdraw test did not terminate')
        with store_context(store):
            wallet.refresh_from_db()
            if results != {'edit':0, 'submit':'WITHDRAW_QUOTE_CHANGED'}:
                raise RuntimeError(f'Unexpected result: {results}')
            if (wallet.balance, wallet.frozen_amount) != (10000, 0) or WithdrawRequest.objects.filter(account=provider_account).exists():
                raise RuntimeError('Stale quote froze money')
        print(json.dumps({'store':slug, 'isolated_mysql':True, 'config_vs_submit':'passed',
                          'reviewer_config_denied':'passed', 'wallet':'unchanged'}))
    finally:
        with store_context(store), transaction.atomic():
            from club_accounts.models import LegacyAccountMap
            for user in get_user_model().objects.filter(username__in=names):
                user.is_active = False
                user.save(update_fields=['is_active'])
                mapping = LegacyAccountMap.objects.filter(legacy_user_id=user.pk).select_related('account').first()
                if mapping:
                    mapping.account.is_active = False
                    mapping.account.can_login = False
                    mapping.account.save(update_fields=['is_active', 'can_login', 'updated_at'])
                    Wallet.objects.filter(account=mapping.account).update(is_active=False)
        for change in reversed(changes):
            change.stop()
