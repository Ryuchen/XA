"""Exercise real transitions on synthetic accounts in the registered isolated databases."""
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
from django.utils import timezone  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402
from club_accounts.services import get_or_create_account_for_legacy_user  # noqa: E402
from club_accounts.tokens import issue_account_tokens  # noqa: E402
from console.models import AdminMembership, AdminRole  # noqa: E402
from stores.context import resolve_store, store_context  # noqa: E402
from stores.db import transaction  # noqa: E402
from stores.models import PlatformAccount, PlatformRoleGrant  # noqa: E402
from stores.platform_auth import issue_tokens  # noqa: E402
from site_messages.models import Message  # noqa: E402
import wallet.withdraw_rules as rules  # noqa: E402
import stores.platform_withdrawals as platform_withdrawals  # noqa: E402
from wallet.models import SystemConfig, Transaction, Wallet, WithdrawRequest, WithdrawalPlatformEvent  # noqa: E402
from wallet.services import get_wallet  # noqa: E402

stores = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104', 'sl.esports.club'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104', 'xa.esports.club')}
if len(sys.argv) != 2 or sys.argv[1] not in stores:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, database, host = stores[slug]
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
names = ['qa-two-stage-player-' + nonce, 'qa-two-stage-staff-' + nonce]
shop_name = 'qa-two-stage-shop-' + nonce
finance_name = 'qa-two-stage-finance-' + nonce
keys = {key: f'qa_two_stage_{name}_{nonce}' for key, name in [
    ('MIN_WITHDRAW_AMOUNT_KEY', 'min'), ('WITHDRAW_TAX_RATE_KEY', 'rate'),
    ('BALANCE_THRESHOLD_KEY', 'threshold'), ('LOCK_KEY', 'lock')]}
patches = [patch.object(rules, name, value) for name, value in keys.items()]
patches.append(patch.object(platform_withdrawals, 'PAYOUT_REFERENCE_LOCK_KEY', f'qa_payout_lock_{nonce}'))
for item in patches: item.start()
with override_settings(PLATFORM_SYSTEM_USERNAME=shop_name,
                       CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    try:
        finance = PlatformAccount(username=finance_name, phone='13900000001', is_phone_verified=True)
        finance.set_password(secrets.token_urlsafe(24)); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=slug)
        finance_token = issue_tokens(finance, 'SMS', finance.phone)[0]
        with store_context(store), transaction.atomic():
            User = get_user_model()
            provider = User.objects.create_user(username=names[0], password=secrets.token_urlsafe(24), role='PROVIDER')
            staff = User.objects.create_user(username=names[1], password=secrets.token_urlsafe(24), role='OPERATOR')
            player_account = get_or_create_account_for_legacy_user(provider)
            staff_account = get_or_create_account_for_legacy_user(staff)
            for account in (player_account, staff_account):
                account.is_phone_verified = True; account.save(update_fields=['is_phone_verified'])
            membership = AdminMembership.objects.create(user=staff, account=staff_account)
            role = AdminRole.objects.create(name='隔离提现测试-' + nonce, code='qa-two-' + nonce,
                                            permissions=['withdraw:view','withdraw:audit'])
            membership.roles.add(role)
            wallet = get_wallet(account=player_account, user=provider, for_update=True)
            wallet.balance = 100000; wallet.save(update_fields=['balance'])
            Transaction.objects.create(wallet=wallet, tx_type='ADJUST', amount=100000,
                balance_before=0, balance_after=100000, remark='隔离提现测试初始余额')
            for name, value in [('MIN_WITHDRAW_AMOUNT_KEY','100'),('WITHDRAW_TAX_RATE_KEY','5'),('BALANCE_THRESHOLD_KEY','0')]:
                SystemConfig.objects.create(key=keys[name], value=value)
            player_token = issue_account_tokens(player_account, auth_method='password_sms')[0]
            staff_token = issue_account_tokens(staff_account, auth_method='password_sms')[0]
        def client(site, token):
            c = APIClient(HTTP_HOST=site); c.credentials(HTTP_AUTHORIZATION='Bearer ' + token); return c
        p, s, f = client(host, player_token), client('admin.esports.club', staff_token), client('admin.esports.club', finance_token)
        def apply(tag):
            result = p.post('/api/wallet/withdraw/', {'amount':20000, 'payee_method':'WECHAT', 'payee_account':'qa-only',
                'payee_name':'隔离测试', 'client_request_id':tag + nonce}, format='json').data
            if result['code'] != 0: raise RuntimeError('Synthetic withdrawal could not be created')
            return result['data']['request']['id']
        pk = apply('qa-first-')
        claim = s.post(f'/api/admin/withdrawals/{pk}/claim/').data['data']
        initial = s.post(f'/api/admin/withdrawals/{pk}/approve/', {'expected_claimed_at':claim['claimed_at']}, format='json')
        if initial.data['code'] != 0: raise RuntimeError('Store initial review failed')
        endpoint = f'/api/admin/platform/withdrawals/{slug}/{pk}/'
        approved = f.post(endpoint + 'approve', {'client_request_id':'qa-approve-' + nonce}, format='json')
        if approved.data['code'] != 0: raise RuntimeError('Platform approval failed')
        payload = {'client_request_id':'qa-paid-' + nonce, 'payout_reference':'qa-receipt-' + nonce,
                   'paid_at':timezone.now().isoformat()}
        barrier, mutex, paid_results = threading.Barrier(2), threading.Lock(), []
        def paid():
            try:
                with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                c = client('admin.esports.club', finance_token); barrier.wait(timeout=10)
                result = c.post(endpoint + 'paid', payload, format='json').data.get('code')
            except Exception as error:
                result = type(error).__name__
            finally:
                connections[alias].close()
            with mutex: paid_results.append(result)
        workers = [threading.Thread(target=paid) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers): raise RuntimeError('Payout race did not terminate')
        with store_context(store):
            row = WithdrawRequest.objects.get(pk=pk); wallet.refresh_from_db()
            if paid_results != [0,0] or (row.status, wallet.balance, wallet.frozen_amount) != ('PAID',80000,0):
                raise RuntimeError(f'Unexpected payout results: {paid_results}')
            if WithdrawalPlatformEvent.objects.filter(withdrawal=row, action='paid').count() != 1:
                raise RuntimeError('Payout event was not unique')
            if Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=1000, wallet__user__username=shop_name).count() != 1:
                raise RuntimeError('Payout fee was not unique')
            notices = Message.objects.filter(recipient_account=player_account,
                action_url=f'/pages/wallet/index?withdraw_id={pk}')
            if list(notices.order_by('id').values_list('title', flat=True)) != [
                '提现门店初审通过', '提现平台终审通过', '提现已登记打款']:
                raise RuntimeError('Withdrawal notices were missing or duplicated')
        pk2 = apply('qa-second-')
        claim2 = s.post(f'/api/admin/withdrawals/{pk2}/claim/').data['data']
        barrier, race = threading.Barrier(2), {}
        def compete(kind):
            try:
                with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 10')
                barrier.wait(timeout=10)
                if kind == 'cancel':
                    result = client(host, player_token).post(f'/api/wallet/withdraw/{pk2}/cancel/',
                        {'client_request_id':'qa-cancel-' + nonce}, format='json').data.get('code')
                else:
                    result = client('admin.esports.club', staff_token).post(f'/api/admin/withdrawals/{pk2}/approve/',
                        {'expected_claimed_at':claim2['claimed_at']}, format='json').data.get('code')
            except Exception as error:
                result = type(error).__name__
            finally:
                connections[alias].close()
            with mutex: race[kind] = result
        workers = [threading.Thread(target=compete, args=(kind,)) for kind in ('cancel','approve')]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers): raise RuntimeError('Cancel/review race did not terminate')
        with store_context(store):
            row = WithdrawRequest.objects.get(pk=pk2); wallet.refresh_from_db()
            if sorted(race.values()) != [0,409] or row.status not in ('CANCELLED','STORE_APPROVED'):
                raise RuntimeError(f'Unexpected cancellation race: {race}')
            if wallet.frozen_amount != (0 if row.status == 'CANCELLED' else 20000):
                raise RuntimeError('Cancellation/review balance inconsistent')
        print(json.dumps({'store':slug, 'isolated_mysql':True, 'payment_idempotency':'passed',
                          'notices_unique':'passed', 'cancel_vs_review':'passed'}))
    finally:
        PlatformAccount.objects.filter(username=finance_name).update(is_active=False)
        with store_context(store), transaction.atomic():
            from club_accounts.models import LegacyAccountMap
            for user in get_user_model().objects.filter(username__in=names + [shop_name]):
                user.is_active = False; user.save(update_fields=['is_active'])
                mapping = LegacyAccountMap.objects.filter(legacy_user_id=user.pk).select_related('account').first()
                if mapping:
                    mapping.account.is_active = False; mapping.account.can_login = False
                    mapping.account.save(update_fields=['is_active','can_login','updated_at'])
                    Wallet.objects.filter(account=mapping.account).update(is_active=False)
        for item in reversed(patches): item.stop()
