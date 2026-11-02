"""An open provider profile follows actual earnings-to-deposit settlement."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from orders.tests.test_provider_vue_team_progress_http import isolated_realtime
from wallet.models import SystemConfig


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderVueMineDepositRealtimeTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_staff_approval_settles_and_updates_open_deposit_card(self):
        root = os.environ.get('XA_PROVIDER_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated provider Vue build')
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value':'1'})
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        order = self.new_order()
        self.assertEqual(order.income_hold_hours, 72)
        report = self.finish(order)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        # Set only the elapsed completion time; approval and ledger remain real.
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now()-timedelta(hours=73))
        cash = make_provider(display_name='隔离现金押金陪玩', deposit_mode='CASH', deposit_paid=0)
        cash_account = get_or_create_account_for_legacy_user(cash)
        link_legacy_relations(cash, cash_account)
        cash.escort_profile.account = cash_account
        cash.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge(trade_no='mine-cash-recharge', request_id='mine-cash-recharge', account=cash_account))
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_realtime() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/provider-vue-mine-deposit-375.cjs')],
                input=json.dumps({'root':root, 'base':base, 'wsBase':ws_base, 'orderId':order.pk, 'reportId':report.pk,
                    'id':self.accounts[self.provider.pk].pk, 'token':issue_account_tokens(self.accounts[self.provider.pk])[0],
                    'bossToken':issue_account_tokens(self.accounts[self.boss.pk])[0],
                    'staffToken':issue_account_tokens(self.accounts[self.admin.pk])[0],
                    'cashId':cash_account.pk, 'cashToken':issue_account_tokens(cash_account)[0],
                    'fixed':os.environ.get('XA_MINE_DEPOSIT_FIXED') == '1'}),
                text=True, encoding='utf-8', capture_output=True, cwd=repo, timeout=70)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
