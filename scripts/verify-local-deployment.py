"""Run inside the isolated backend container; exercise the deployed HTTP gateway.

Creates fresh labelled fixtures, then uses real HTTP for business mutations.
Does not call WeChat or payment platforms. Refuses other database environments.
Usage: docker compose ... exec -T web python - < scripts/verify-local-deployment.py
"""
import io
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Sum
from django.utils import timezone
from PIL import Image
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from orders.models import Order, ServiceCategory, ServiceItem
from support.models import SupportContactCard
from users.models import EscortProfile
from wallet.models import ProviderReport, RechargeRecord, Transaction, WithdrawRequest
from wallet.services import get_wallet


def check(condition, message):
    if not condition:
        raise AssertionError(message)


check(os.environ.get('XA_LOCAL_VALIDATION') == 'true', 'Local validation opt-in missing')
check(connection.vendor == 'mysql' and connection.settings_dict['NAME'] == 'xa_verify'
      and connection.settings_dict['HOST'] == 'db', 'Not the isolated Compose database')
check(not settings.WECHAT_MOCK_LOGIN and not settings.KOOK_ENABLED
      and not settings.WECOM_DISPATCH_ENABLED, 'Unexpected external integration settings')
check(settings.REQUIRE_ORDER_EVIDENCE_IMAGES and settings.REQUIRE_RECHARGE_PROOF
      and settings.ORDER_SETTLEMENT_MODE == 'REPORT_APPROVAL', 'Strict business settings required')
executor = MigrationExecutor(connection)
pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
check(not pending, 'Local validation database has unapplied migrations; inspect and migrate it before running the business smoke')

base = 'http://gateway'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
run_id = secrets.token_hex(6)
password = secrets.token_urlsafe(24)
png = io.BytesIO()
Image.new('RGB', (4, 4), 'white').save(png, format='PNG')
evidence = png.getvalue()


def request(path, token=None, payload=None, method=None, image_field=None, expected=0):
    headers = {'Host': '127.0.0.1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if image_field:
        boundary = 'xa-' + secrets.token_hex(12)
        parts = []
        for name, value in (payload or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{image_field}"; filename="local-evidence.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
        parts.extend([evidence, f'\r\n--{boundary}--\r\n'.encode()])
        data = b''.join(parts)
        headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
    elif payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if expected == 0:
            raise AssertionError(f'{path}: HTTP {error.code}') from None
        result = json.load(error)
    check(result.get('code') == expected, f'{path}: code={result.get("code")}, message={result.get("msg")}')
    return result.get('data') if expected == 0 else result


def balance(account, amount, frozen=0):
    actual = get_wallet(account=account)
    check((actual.balance, actual.frozen_amount) == (amount, frozen),
          f'Wallet mismatch: expected {(amount, frozen)}, got {(actual.balance, actual.frozen_amount)}')


def fixture(name, role):
    user = get_user_model().objects.create_user(
        username=f'local_verify_{run_id}_{name}', password=password,
        nickname=f'本地部署验证{name}', role=role,
    )
    account = get_or_create_account_for_legacy_user(user)
    get_wallet(account=account)
    return user, account


def password_login(path, username):
    credentials = {'username': username, 'password': password}
    response = request(path, payload=credentials)
    if response.get('requiresOTP'):
        # These are newly created isolated accounts, so enrollment is exercised
        # through the real login API rather than bypassing authentication.
        check(response.get('requiresOTPSetup') and response.get('otpSecret'), 'Expected fresh local OTP enrollment')
        from club_accounts.otp import hotp
        code = hotp(response['otpSecret'], int(timezone.now().timestamp()) // 30)
        response = request(path, payload={**credentials, 'otpChallenge': response['otpChallenge'], 'otpCode': code})
    check(bool(response.get('token')), f'{path}: login did not return a token')
    return response['token']


for port, name in ((81, 'admin'), (82, 'provider')):
    origin = f'http://gateway:{port}'
    with opener.open(origin, timeout=30) as page:
        check(page.status == 200 and 'text/html' in page.headers.get('Content-Type', ''), f'{name}: HTML unavailable')
        html = page.read().decode('utf-8')
    scripts = re.findall(r'<script\b[^>]*\bsrc=[\"\x27]([^\"\x27]+)', html, re.I)
    check(scripts, f'{name}: compiled scripts missing')
    for source in scripts:
        asset_url = urllib.parse.urljoin(origin + '/', source)
        check(urllib.parse.urlsplit(asset_url).netloc == f'gateway:{port}', 'Unexpected external script')
        with opener.open(asset_url, timeout=30) as asset:
            check(asset.status == 200 and 'javascript' in asset.headers.get('Content-Type', '')
                  and len(asset.read()) > 0, f'{name}: compiled script unavailable')
print('PASS both deployed frontends and compiled JavaScript assets', flush=True)

request('/api/orders/gameplays/')
boss_user, boss = fixture('boss', 'CUSTOMER')
provider_user, provider = fixture('provider', 'PROVIDER')
staff_user, staff = fixture('staff', 'OPERATOR')
profile = EscortProfile.objects.create(user=provider_user, account=provider,
                                      display_name='本地部署验证陪玩', is_verified=True,
                                      pass_tier='GOLD', pass_expires_at=timezone.now() + timedelta(days=1))
role = AdminRole.objects.create(name=f'本地验证{run_id}', code=f'local_{run_id}', permissions=[
    'wallet:recharge', 'recharge:view', 'report:view', 'report:audit', 'order:view', 'order:dispatch',
    'order:refund', 'withdraw:view', 'withdraw:audit',
])
membership = AdminMembership.objects.create(user=staff_user, account=staff)
membership.roles.add(role)
service = ServiceItem.objects.create(name=f'本地部署验证服务{run_id}', price=20000, commission_rate=20)
card = SupportContactCard.objects.create(name=f'本地验证客服{run_id}')
boss_token, _ = issue_account_tokens(boss)  # WeChat credentials are deliberately excluded locally.
request('/api/orders/services/', boss_token)
provider_token = password_login('/api/users/account-login/', provider.username)
staff_token = password_login('/api/admin/auth/login', staff.username)
print(f'PASS real provider/admin password login; fixture run {run_id}', flush=True)

recharge = {'account_id': boss.pk, 'amount': 160000, 'gift_amount': 0,
            'trade_no': f'LOCAL_ONLY_{run_id}', 'client_request_id': f'recharge-{run_id}'}
request('/api/admin/wallets/recharge/', staff_token, recharge, image_field='proof_image')
balance(boss, 160000)
request('/api/admin/wallets/recharge/', staff_token, recharge, image_field='proof_image', expected=409)
balance(boss, 160000)
check(RechargeRecord.objects.filter(account=boss).count() == 1, 'Duplicate recharge')
record = RechargeRecord.objects.get(account=boss)
try:
    opener.open(base + urllib.parse.urlsplit(record.proof_image.url).path, timeout=30)
except urllib.error.HTTPError as error:
    check(error.code == 404, 'Private receipt returned an unexpected status')
else:
    raise AssertionError('Private receipt was exposed as public media')
record_data = request(f'/api/admin/recharge-records/{record.pk}/', staff_token)
proof_path = urllib.parse.urlsplit(record_data['proof_image_url']).path
check(proof_path.startswith('/api/media/private/'), 'Receipt did not receive a private URL')
proof_request = urllib.request.Request(base + proof_path, headers={'Host': '127.0.0.1'})
with opener.open(proof_request, timeout=30) as proof:
    check(proof.status == 200 and proof.read() == evidence, 'Authorized receipt not served by gateway')
print('PASS proof recharge and idempotent retry', flush=True)

orders = []
for mode in ('selected', 'grab', 'dispatch'):
    payload = {'service_id': service.pk, 'support_contact_id': card.pk,
               'client_request_id': f'order-{run_id}-{mode}'}
    if mode == 'dispatch':
        payload.update(customer_id=boss.pk, escort_mode='SINGLE',
                       providers=[{'provider_id': provider.pk, 'commission_rate': 20}])
        order_data = request('/api/admin/orders/dispatch/', staff_token, payload)
    else:
        if mode == 'selected':
            payload['provider_id'] = provider.pk
        elif mode == 'grab':
            payload['dispatch_mode'] = 'AUTO_GRAB'
        order_data = request('/api/orders/create/', boss_token, payload)
    order_id = order_data['id']
    if mode == 'grab':
        # Wait the actual configured priority delay; never rewrite order timestamps.
        delay = profile.order_visibility_delay_seconds
        check(delay <= 45, 'Unexpected priority delay; inspect local configuration')
        if delay:
            request(f'/api/orders/orders/{order_id}/grab/', provider_token, {}, expected=400)
            print(f'PASS priority window denies early grab; waiting {delay} seconds', flush=True)
        pool_order = Order.objects.get(pk=order_id)
        visible_at = (pool_order.pool_entered_at or pool_order.created_at) + timedelta(seconds=delay + 1)
        while timezone.now() < visible_at:
            time.sleep(min(2, (visible_at - timezone.now()).total_seconds()))
        request(f'/api/orders/orders/{order_id}/grab/', provider_token, {})
    request(f'/api/orders/orders/{order_id}/start/', provider_token, {}, image_field='entry_image')
    request(f'/api/orders/orders/{order_id}/complete/', provider_token, {}, image_field='completion_image')
    balance(provider, 0)
    report = ProviderReport.objects.get(order_id=order_id, provider_account=provider)
    request(f'/api/wallet/reports/{report.pk}/images/', provider_token,
            {'kind': 'RESULT'}, image_field='image')
    request(f'/api/wallet/reports/{report.pk}/submit/', provider_token, {})
    request(f'/api/admin/reports/{report.pk}/approve/', staff_token, {})
    orders.append(order_id)
    balance(boss, 160000 - 20000 * len(orders))
    balance(provider, 0)
    check(Order.objects.get(pk=order_id).settled_at is None, 'Order settled before aftersale window')
    check(Transaction.objects.filter(order_id=order_id).aggregate(value=Sum('amount'))['value'] == -20000,
          'Pending order payment ledger mismatch')
    print(f'PASS {mode}: order {order_id}, evidence, approved report and pending settlement', flush=True)

# Gift settlement is immediate and supplies genuine income for the withdrawal
# flow; regular service orders above remain pending their 72-hour window.
gift_category = ServiceCategory.objects.create(name=f'本地验证礼物{run_id}', is_gift=True)
gift = ServiceItem.objects.create(name=f'本地验证礼物{run_id}', price=60000, commission_rate=20,
                                  service_category=gift_category)
gift_data = request('/api/orders/gifts/send/', boss_token, {
    'gift_service_id': gift.pk, 'provider_id': provider.pk, 'quantity': 1,
    'expected_amount': 60000, 'client_request_id': f'gift-{run_id}',
})
balance(boss, 40000)
balance(provider, 48000)
check(Transaction.objects.filter(order_id=gift_data['id']).aggregate(value=Sum('amount'))['value'] == 0,
      'Gift ledger does not balance')
print('PASS immediate gift settlement and balanced ledger', flush=True)

for action in ('cancel', 'refund'):
    order_data = request('/api/orders/create/', boss_token, {
        'service_id': service.pk, 'support_contact_id': card.pk,
        'client_request_id': f'order-{run_id}-{action}',
    })
    if action == 'cancel':
        request(f'/api/orders/orders/{order_data["id"]}/cancel/', boss_token, {})
    else:
        request(f'/api/admin/orders/{order_data["id"]}/refund/', staff_token, {'reason': '本地部署验证退款'})
    balance(boss, 40000)
print('PASS cancellation and admin refund restore balance', flush=True)

payee = request('/api/wallet/withdraw-accounts/WECHAT/', provider_token, {
    'payee_account': f'LOCAL_TEST_ONLY_{run_id}', 'payee_name': '本地测试收款人',
    'expected_id': 0, 'expected_version': 0,
}, method='PUT')
for method in ('ALIPAY', 'BANK'):
    request(f'/api/wallet/withdraw-accounts/{method}/', provider_token, {
        'payee_account': f'LOCAL_TEST_ONLY_{method}_{run_id}', 'payee_name': '本地测试收款人',
        'expected_id': 0, 'expected_version': 0,
    }, method='PUT')
check(len(request('/api/wallet/withdraw-accounts/', provider_token)) == 3, 'Expected three independent payees')
withdrawal_payload = {'amount': 10000, 'withdrawal_account_id': payee['id'],
                      'expected_payee_version': payee['version'], 'client_request_id': f'withdraw-{run_id}'}
request('/api/wallet/withdraw/', provider_token, withdrawal_payload)
balance(provider, 38000, 10000)
withdrawal = WithdrawRequest.objects.get(account=provider)
request('/api/wallet/withdraw-accounts/WECHAT/', provider_token, {
    'expected_id': payee['id'], 'expected_version': payee['version'],
}, method='DELETE')
request('/api/wallet/withdraw/', provider_token, withdrawal_payload, expected=409)
balance(provider, 38000, 10000)
check(WithdrawRequest.objects.filter(account=provider).count() == 1, 'Duplicate withdrawal')
withdrawal.refresh_from_db()
check(withdrawal.payee_account == f'LOCAL_TEST_ONLY_{run_id}', 'Submitted payee snapshot changed')
withdraw_claim = request(f'/api/admin/withdrawals/{withdrawal.pk}/claim/', staff_token, {})
request(f'/api/admin/withdrawals/{withdrawal.pk}/approve/', staff_token,
        {'payout_reference': f'LOCAL_SIMULATED_PAYOUT_{run_id}', 'expected_claimed_at': withdraw_claim['claimed_at']})
balance(provider, 38000, 0)
print('PASS bound payee, freeze, unbind/retry snapshot and simulated payout approval', flush=True)

bank = next(row for row in request('/api/wallet/withdraw-accounts/', provider_token)
            if row['payee_method'] == 'BANK')
request('/api/wallet/withdraw/', provider_token, {
    'amount': 10000, 'withdrawal_account_id': bank['id'], 'expected_payee_version': bank['version'],
    'client_request_id': f'withdraw-reject-{run_id}',
})
balance(provider, 28000, 10000)
rejected = WithdrawRequest.objects.get(account=provider, status='PENDING')
reject_claim = request(f'/api/admin/withdrawals/{rejected.pk}/claim/', staff_token, {})
request(f'/api/admin/withdrawals/{rejected.pk}/reject/', staff_token,
        {'audit_remark': '本地部署验证驳回', 'expected_claimed_at': reject_claim['claimed_at']})
balance(provider, 38000, 0)
print('PASS withdrawal rejection restores balance and releases frozen funds', flush=True)

# Keep the audit trail while removing this run's fixtures from customer pickers.
ServiceItem.objects.filter(pk=service.pk).update(is_active=False)
ServiceItem.objects.filter(pk=gift.pk).update(is_active=False)
EscortProfile.objects.filter(pk=profile.pk).update(is_verified=False)
SupportContactCard.objects.filter(pk=card.pk).update(is_active=False)

# Keep fixture IDs, never tokens/passwords, so persistence can be checked after restart.
print(json.dumps({'run_id': run_id, 'boss_account_id': boss.pk,
                  'provider_account_id': provider.pk, 'order_ids': orders,
                  'withdrawal_id': withdrawal.pk, 'boss_balance': 40000,
                  'provider_balance': 38000, 'provider_frozen': 0}, ensure_ascii=False), flush=True)
print('HTTP business smoke passed. Real WeChat, external notifications and actual payouts remain excluded.', flush=True)
