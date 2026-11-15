import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message
from stores.context import resolve_store
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from wallet.models import SystemConfig, WithdrawRequest
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_WITHDRAW_THREE_ROLE_BROWSER') == '1', 'Requires isolated ASGI and three-end Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True)
class WithdrawalThreeRoleBrowserTest(LiveServerTestCase):
    def test_store_review_finance_poll_payout_and_provider_message(self):
        store = resolve_store('shenglang')
        store.initialized_at = timezone.now(); store.save(update_fields=['initialized_at'])
        finance = PlatformAccount(username='three-role-finance', phone='13900001112', is_phone_verified=True)
        finance.set_password('isolated-test-password'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug=store.slug)
        token, refresh = issue_tokens(finance, 'SMS', finance.phone)
        session = {'token':token, 'refresh':refresh, 'sessionId':'three-role-finance',
                   'profile':{'id':str(finance.pk), 'username':finance.username, 'identity_type':'platform',
                              'grants':[{'role':'FINANCE', 'scope_slug':store.slug}]}}
        provider = make_provider(); account = get_or_create_account_for_legacy_user(provider)
        wallet = get_wallet(account=account, user=provider); wallet.balance = 100000; wallet.save(update_fields=['balance'])
        staff = make_console_user(['withdraw:view', 'withdraw:audit'])
        admin = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        for key, value in [('min_withdraw_amount','0'), ('withdraw_balance_threshold','0'), ('withdraw_tax_rate','5')]:
            SystemConfig.objects.update_or_create(key=key, defaults={'value':value})
        client = APIClient(HTTP_HOST='sl.esports.club')
        provider_token = issue_account_tokens(account)[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        result = client.post('/api/wallet/withdraw/', {'amount':20000, 'payee_method':'WECHAT',
            'payee_name':'隔离三端提现', 'payee_account':'qa-only', 'client_request_id':'three-role-apply'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        pk = result.data['data']['request']['id']
        WithdrawRequest.objects.filter(pk=pk).update(created_at=timezone.now() - timedelta(minutes=1))
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/withdraw-three-role-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'id':pk, 'session':session, 'staffToken':issue_account_tokens(admin)[0],
                                  'provider':{'id':account.pk, 'token':provider_token}}), text=True, encoding='utf-8',
                capture_output=True, timeout=65)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        row = WithdrawRequest.objects.get(pk=pk); wallet.refresh_from_db()
        self.assertEqual((row.status, row.transaction.status, wallet.balance, wallet.frozen_amount), ('PAID','SUCCESS',80000,0))
        self.assertEqual(list(Message.objects.filter(recipient_account=account).order_by('id').values_list('title', flat=True)),
                         ['提现门店初审通过', '提现平台终审通过', '提现已登记打款'])
