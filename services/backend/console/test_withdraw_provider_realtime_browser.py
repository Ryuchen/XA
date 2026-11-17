"""Provider wallet reflects real staff withdrawal actions over the isolated API."""
import os
import shutil

from django.db import connection
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class WithdrawProviderRealtimeBrowserTest(APITransactionTestCase):
    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_claim_release_and_payout_refresh_visible_wallet(self):
        node, database, build = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB'), os.environ.get('XA_WITHDRAW_H5_ROOT')
        if not node or not database or connection.settings_dict['NAME'] != database or not build:
            self.skipTest('requires isolated ASGI database and provider H5 build')
        payee = self.bind('realtime-wallet-1234')
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        withdrawal = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'provider-realtime-progress'), format='json'))['data']['request']
        manager = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager)
        manager.admin_membership.account = manager_account
        manager.admin_membership.save(update_fields=['account'])
        claimed = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdrawal["id"]}/claim/'))['data']
        manager_client = self.client_for(manager_account)
        self.ok(manager_client.post(f'/api/admin/withdrawals/{withdrawal["id"]}/release-claim/', {
            'confirmed_unpaid': True, 'reason': '隔离验收已核实未打款',
            'expected_claimed_by_account': claimed['claimed_by_account'], 'expected_claimed_at': claimed['claimed_at'],
        }, format='json'))
        reclaimed = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdrawal["id"]}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdrawal["id"]}/approve/', {
            'payout_reference': 'ISOLATED-REALTIME', 'expected_claimed_at': reclaimed['claimed_at'],
        }, format='json'))
        second = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'provider-realtime-rejected'), format='json'))['data']['request']
        second_claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{second["id"]}/claim/', {}, format='json'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{second["id"]}/reject/', {
            'audit_remark': '收款资料需重新核对', 'expected_claimed_at': second_claim['claimed_at'],
        }, format='json'))
        overview = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']
        self.assertEqual((overview['balance'], overview['frozen_amount']), (40000, 0))
        stats = self.ok(self.staff_client.get('/api/admin/dashboard/stats'))['data']
        self.assertEqual(stats['pending_withdraw_total'], 0)
        final = next(row for row in self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
                     if row['id'] == withdrawal['id'])
        self.assertEqual((final['status'], final['processing'], final['payout_reference']),
                         ('APPROVED', False, 'ISOLATED-REALTIME'))