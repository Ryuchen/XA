"""Exercise the withdrawal dashboard drilldown using actual isolated applications."""
import os
from unittest import skipUnless

from django.db import connection
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures
from wallet.models import WithdrawRequest


@skipUnless(os.environ.get('XA_ADMIN_WITHDRAW_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DashboardWithdrawalBrowserTest(LiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor == 'sqlite' and connection.creation.is_in_memory_db(connection.settings_dict['NAME']):
            raise RuntimeError('提现页面真实浏览器验收需要独立文件 SQLite 或 MySQL；内存 SQLite 跨线程共享连接会导致结果不可靠。')
        super().setUpClass()

    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_pending_dashboard_opens_oldest_first(self):
        role = self.admin.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'dashboard:view', 'withdraw:view'})
        role.save(update_fields=['permissions'])
        payee = self.bind('oldest-browser-1234')
        ids = [self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, f'oldest-browser-{n}'), format='json'))['data']['request']['id'] for n in range(3)]
        listing = self.ok(self.client_for(self.admin.admin_membership.account).get('/api/admin/withdrawals/', {'status': 'PENDING'}))['data']
        self.assertEqual(listing['pending_order'], 'oldest_first')
        self.assertEqual([row['id'] for row in listing['list']], ids)

    def test_claim_last_row_returns_to_existing_page(self):
        from wallet.models import SystemConfig
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value':'1000'})
        payee = self.bind('last-page-browser-1234')
        ids = []
        for n in range(11):
            data = {**self.payload(payee, f'last-page-{n}'), 'amount':1000}
            ids.append(self.ok(self.provider_client.post('/api/wallet/withdraw/', data, format='json'))['data']['request']['id'])
        last_page = self.ok(self.client_for(self.admin.admin_membership.account).get('/api/admin/withdrawals/',
            {'status': 'PENDING', 'page_size': 10, 'page': 2}))['data']['list']
        self.assertEqual([row['id'] for row in last_page], [ids[-1]])
        self.ok(self.client_for(self.admin.admin_membership.account).post(f'/api/admin/withdrawals/{ids[-1]}/claim/'))
        self.assertEqual(WithdrawRequest.objects.get(pk=ids[-1]).claimed_by_account_id, self.admin.admin_membership.account_id)

    def test_other_staff_claim_drilldown_on_ipad_landscape(self):
        self.account.nickname = '陪玩测试昵称' * 7
        self.account.save(update_fields=['nickname'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = ['dashboard:view', 'withdraw:view', 'withdraw:release']
        role.save(update_fields=['permissions'])
        other = make_console_user(['withdraw:view', 'withdraw:audit'])
        other_account = get_or_create_account_for_legacy_user(other)
        other.admin_membership.account = other_account
        other.admin_membership.save(update_fields=['account'])
        reclaimer = make_console_user(['withdraw:view', 'withdraw:audit'])
        reclaimer_account = get_or_create_account_for_legacy_user(reclaimer)
        reclaimer.admin_membership.account = reclaimer_account
        reclaimer.admin_membership.save(update_fields=['account'])
        payee = self.bind('browser-test-1234')
        ids = [self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, f'mobile-withdraw-{n}'), format='json'))['data']['request']['id'] for n in range(2)]
        self.ok(self.client_for(other_account).post(f'/api/admin/withdrawals/{ids[0]}/claim/'))
        reclaimer_client = self.client_for(reclaimer_account)
        record = WithdrawRequest.objects.get(pk=ids[0])
        self.ok(self.client_for(self.admin.admin_membership.account).post(f'/api/admin/withdrawals/{ids[0]}/release-claim/',
            {'reason': '隔离复核改派', 'confirmed_unpaid': True,
             'expected_claimed_by_account': other_account.pk, 'expected_claimed_at': record.claimed_at.isoformat()}, format='json'))
        self.ok(reclaimer_client.post(f'/api/admin/withdrawals/{ids[0]}/claim/'))
        record.refresh_from_db()
        self.ok(reclaimer_client.post(f'/api/admin/withdrawals/{ids[0]}/approve/',
            {'expected_claimed_at': record.claimed_at.isoformat()}, format='json'))
        record.refresh_from_db()
        self.assertEqual((record.claimed_by_account_id, record.status), (reclaimer_account.pk, 'STORE_APPROVED'))
        self.assertFalse(record.payout_reference)
        history = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        receipt = next(row for row in history if row['id'] == ids[0])
        self.assertEqual((receipt['status'], receipt['actual_amount']), ('STORE_APPROVED', 19600))
        self.assertFalse(receipt['payout_reference'])
        messages = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        self.assertTrue(any(message['title'] == '提现门店初审通过' for message in messages), messages)
        self.ok(self.client_for(other_account).post(f'/api/admin/withdrawals/{ids[1]}/claim/'))
        self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, 'mobile-withdraw-unclaimed'), format='json'))
        pending = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        self.assertTrue(any(row['status'] == 'PENDING' for row in pending), pending)
