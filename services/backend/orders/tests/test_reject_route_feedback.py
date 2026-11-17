"""Role-scoped feedback after actual staff and public order rejection."""
from uuid import uuid4
from datetime import timedelta
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortLevel, EscortProfile
from wallet.models import SystemConfig, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class RejectRouteFeedbackTest(APITransactionTestCase):
    def setUp(self):
        self.boss = make_user()
        self.staff = make_console_user(['order:view', 'order:dispatch'])
        self.provider = make_provider()
        self.observer = make_provider()
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user)
                         for user in (self.boss, self.staff, self.provider, self.observer)}
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        for user in (self.provider, self.observer):
            EscortProfile.objects.filter(user=user).update(account=self.accounts[user.pk])
        self.service = make_service(price=10000, billing_provider_count=1, commission_rate=20)
        make_wallet(self.boss, balance=50000)
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data

    def create(self, route, **fields):
        self.login(self.boss)
        return self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': route,
            'client_request_id': str(uuid4()), **fields,
        }, format='json'))['data']['id']

    def pool(self, user):
        self.login(user)
        return self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))['data']

    def boss_message(self, order_id):
        self.login(self.boss)
        rows = self.ok(self.client.get('/api/messages/', {'type': 'ORDER'}))['data']['messages']
        return next(row for row in rows if row['related_order_id'] == order_id and row['title'] == '陪玩已拒单')

    def test_staff_rejection_returns_to_staff_and_tells_each_role(self):
        order_id = self.create('STAFF')
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))
        self.login(self.provider)
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))
        self.assertEqual(result['msg'], '已拒单，订单已退回客服')
        self.assertEqual((result['data']['status'], result['data']['dispatch_route']), ('PENDING', 'STAFF'))
        self.assertNotIn(order_id, [row['id'] for row in self.pool(self.observer)])
        self.assertIn('已退回客服', self.boss_message(order_id)['preview'])
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']
        self.assertEqual((boss_order['status'], boss_order['dispatch_route']), ('PENDING', 'STAFF'))
        self.login(self.staff)
        queue = self.ok(self.client.get('/api/admin/orders/', {'actionable_staff': '1'}))['data']['list']
        self.assertIn(order_id, [row['id'] for row in queue])
        history = self.ok(self.client.get(f'/api/admin/orders/{order_id}/logs/'))['data']
        self.assertTrue(any(row['action'] == 'REJECT' and row['reason'] == '档期变化' for row in history))
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.observer.pk].pk,
        }, format='json'))
        self.login(self.observer)
        assigned = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']
        self.assertEqual(assigned['status'], 'GRABBED')
        self.assertIn('START', assigned['allowed_actions'])
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']['status'], 'GRABBED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_assigned_income_uses_provider_account_when_legacy_assignment_is_absent(self):
        from orders.models import OrderProvider
        order_id = self.create('STAFF')
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))
        assignment = OrderProvider.objects.get(order_id=order_id, provider_account=self.accounts[self.provider.pk])
        expected = assignment.provider_income
        self.assertGreater(expected, 0)
        # Account-based assignments can remain after the optional legacy link is removed.
        assignment.provider = None
        assignment.save(update_fields=['provider'])
        self.login(self.provider)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']
        self.assertEqual(detail['expected_income'], expected)
        rows = self.ok(self.client.get('/api/orders/orders/', {'status': 'all'}))['data']
        if isinstance(rows, dict):
            rows = rows['list']
        self.assertEqual(next(row for row in rows if row['id'] == order_id)['expected_income'], expected)

    def test_public_rejection_returns_to_public_pool(self):
        order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')
        self.assertIn(order_id, [row['id'] for row in self.pool(self.provider)])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))
        self.assertEqual(result['msg'], '已拒单，订单已回到待接单池')
        self.assertEqual((result['data']['status'], result['data']['dispatch_route']), ('PENDING', 'PUBLIC'))
        self.assertIn(order_id, [row['id'] for row in self.pool(self.observer)])
        self.assertIn('等待其他陪玩直接接单', self.boss_message(order_id)['preview'])
        self.login(self.staff)
        queue = self.ok(self.client.get('/api/admin/orders/', {'actionable_staff': '1'}))['data']['list']
        self.assertNotIn(order_id, [row['id'] for row in queue])
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_returned_pool_income_uses_each_provider_and_matches_regrab(self):
        low_cut = EscortLevel.objects.create(name='低抽成', commission_rate=10)
        high_cut = EscortLevel.objects.create(name='高抽成', commission_rate=30)
        EscortProfile.objects.filter(user=self.provider).update(level=low_cut)
        EscortProfile.objects.filter(user=self.observer).update(level=high_cut)
        self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['commission_policy'])
        order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')

        first = next(row for row in self.pool(self.provider) if row['id'] == order_id)
        self.assertEqual(first['expected_income'], 9000)
        accepted = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual(accepted['expected_income'], 9000)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))

        other = next(row for row in self.pool(self.observer) if row['id'] == order_id)
        self.assertEqual(other['expected_income'], 7000)
        own = next(row for row in self.pool(self.provider) if row['id'] == order_id)
        self.assertEqual((own['my_assignment_departure'], own['expected_income']), ('REJECTED', 9000))
        history = self.ok(self.client.get('/api/orders/orders/', {'status': 'all'}))['data']
        previous = next(row for row in history if row['id'] == order_id)
        self.assertEqual((previous['my_assignment_departure'], previous['expected_income']), ('REJECTED', 9000))
        self.assertIn('GRAB', previous['allowed_actions'])
        regrabbed = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual((regrabbed['status'], regrabbed['expected_income'], regrabbed['my_assignment_departure']),
                         ('GRABBED', 9000, None))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '改由其他陪玩'}, format='json'))
        next(row for row in self.pool(self.observer) if row['id'] == order_id)
        accepted_other = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual((accepted_other['status'], accepted_other['expected_income']), ('GRABBED', 7000))

    def test_provider_history_paginates_and_searches_real_rejected_orders(self):
        from orders.tests.test_release_flow import screenshot
        from tempfile import TemporaryDirectory
        media = TemporaryDirectory(prefix='xa-history-pages-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name, REQUIRE_RECHARGE_PROOF=True))
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'wallet:recharge']
        role.save(update_fields=['permissions'])
        for index in range(8):
            self.login(self.staff)
            self.ok(self.client.post('/api/admin/wallets/recharge/', {'account_id': self.accounts[self.boss.pk].pk,
                'amount': 20000, 'gift_amount': 0, 'trade_no': f'history-receipt-{index}',
                'proof_image': screenshot(), 'client_request_id': f'history-funding-{index}'}, format='multipart'))
        ids = []
        for _ in range(21):
            order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')
            self.login(self.provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '测试档期变化'}, format='json'))
            ids.append(order_id)
        first = self.ok(self.client.get('/api/orders/orders/', {'status': 'all', 'page': 1, 'page_size': 20}))['data']
        second = self.ok(self.client.get('/api/orders/orders/', {'status': 'all', 'page': 2, 'page_size': 20}))['data']
        self.assertEqual((first['total'], len(first['list']), second['total'], len(second['list'])), (21, 20, 21, 1))
        self.assertEqual([row['id'] for row in first['list']] + [row['id'] for row in second['list']], list(reversed(ids)))
        target = second['list'][0]
        found = self.ok(self.client.get('/api/orders/orders/', {'status': 'all', 'page': 1, 'q': target['order_no']}))['data']
        self.assertEqual((found['total'], found['list'][0]['id']), (1, target['id']))
        self.assertEqual(found['list'][0]['my_assignment_departure'], 'REJECTED')
        self.assertEqual(len(self.ok(self.client.get('/api/orders/orders/', {'status': 'all'}))['data']), 21)

    def test_provider_application_history_follows_boss_selection(self):
        order_id = self.create('PUBLIC', dispatch_mode='BOSS_SELECT')
        application_ids = []
        for user in (self.provider, self.observer):
            self.login(user)
            result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
            application_ids.append(result['my_applications'][0]['id'])
            history = self.ok(self.client.get('/api/orders/applications/mine/'))['data']
            self.assertEqual((history['total'], history['list'][0]['id'], history['list'][0]['status']),
                             (1, application_ids[-1], 'PENDING'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{application_ids[1]}/select/', {}, format='json'))
        for user, expected in ((self.provider, 'NOT_SELECTED'), (self.observer, 'SELECTED')):
            self.login(user)
            history = self.ok(self.client.get('/api/orders/applications/mine/'))['data']
            self.assertEqual((history['total'], history['list'][0]['status']), (1, expected))
            detail = self.ok(self.client.get(f"/api/orders/orders/{order_id}/applications/{history['list'][0]['id']}/mine/"))['data']
            self.assertEqual(detail['application']['status'], expected)
        self.login(self.boss)
        self.assertEqual(self.client.get('/api/orders/applications/mine/').status_code, 403)

    def test_invited_teammate_can_find_and_confirm_application_in_history(self):
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        order_id = self.create('PUBLIC', dispatch_mode='BOSS_SELECT')
        self.login(self.provider)
        application = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.observer.pk].pk}, format='json'))['data']['my_applications'][0]
        self.login(self.observer)
        row = self.ok(self.client.get('/api/orders/applications/mine/'))['data']['list'][0]
        self.assertEqual((row['id'], row['is_partner'], row['partner_confirmed'], len(row['members'])),
                         (application['id'], True, False, 2))
        self.ok(self.client.post(f"/api/orders/orders/{order_id}/applications/{row['id']}/action/",
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        self.assertTrue(self.ok(self.client.get('/api/orders/applications/mine/'))['data']['list'][0]['partner_confirmed'])
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/orders/{order_id}/applications/{row['id']}/select/", {}, format='json'))
        self.login(self.observer)
        self.assertEqual(self.ok(self.client.get('/api/orders/applications/mine/'))['data']['list'][0]['status'], 'SELECTED')

    @skipUnlessDBFeature('has_select_for_update')
    def test_boss_selection_and_partner_withdrawal_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from rest_framework.test import APIClient
        from orders.models import Order, OrderApplication

        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        order_id = self.create('PUBLIC', dispatch_mode='BOSS_SELECT')
        self.login(self.provider)
        app = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {
            'team_size': 2, 'partner_id': self.accounts[self.observer.pk].pk}, format='json'))['data']['my_applications'][0]
        self.login(self.observer)
        self.ok(self.client.post(f"/api/orders/orders/{order_id}/applications/{app['id']}/action/",
                                 {'action': 'CONFIRM_TEAM'}, format='json'))
        barrier = Barrier(2)
        def run(user, suffix, data):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[user.pk])[0])
                barrier.wait(timeout=10)
                response = client.post(f"/api/orders/orders/{order_id}/applications/{app['id']}/{suffix}/", data, format='json')
                return response.data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            selection = executor.submit(run, self.boss, 'select', {})
            withdrawal = executor.submit(run, self.observer, 'action', {'action':'WITHDRAW'})
            selection_code, withdrawal_code = selection.result(timeout=30), withdrawal.result(timeout=30)
        self.assertEqual([selection_code, withdrawal_code].count(0), 1)
        order = Order.objects.get(pk=order_id)
        row = OrderApplication.objects.get(pk=app['id'])
        if selection_code == 0:
            self.assertEqual((order.status, row.status), ('GRABBED', 'SELECTED'))
            self.assertEqual(order.providers.filter(released_at__isnull=True).count(), 2)
        else:
            self.assertEqual((order.status, row.status), ('PENDING', 'WITHDRAWN'))
            self.assertEqual(order.providers.filter(released_at__isnull=True).count(), 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 30000)


    def test_boss_selection_mode_survives_rejection_and_next_application(self):
        order_id = self.create('PUBLIC', dispatch_mode='BOSS_SELECT')
        self.login(self.provider)
        application = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        app_id = application['my_applications'][0]['id']
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{app_id}/select/', {}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))

        notice = self.boss_message(order_id)
        self.assertIn('等待陪玩报名后请在订单详情选择', notice['preview'])

        self.login(self.observer)
        application = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual(application['status'], 'PENDING')
        self.assertTrue(application['requires_boss_selection'])
        app_id = application['my_applications'][0]['id']
        self.login(self.boss)
        selected = self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{app_id}/select/', {}, format='json'))['data']
        self.assertEqual(selected['status'], 'GRABBED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_boss_direct_assignment_returns_to_boss_selection(self):
        order_id = self.create('PUBLIC', provider_ids=[self.accounts[self.provider.pk].pk])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))
        self.login(self.observer)
        applied = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual(applied['status'], 'PENDING')
        self.assertTrue(applied['requires_boss_selection'])
        app_id = applied['my_applications'][0]['id']
        self.login(self.boss)
        selected = self.ok(self.client.post(f'/api/orders/orders/{order_id}/applications/{app_id}/select/', {}, format='json'))['data']
        self.assertEqual(selected['status'], 'GRABBED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_staff_assignment_of_public_direct_order_preserves_direct_mode(self):
        order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))
        self.login(self.observer)
        accepted = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertFalse(accepted['requires_boss_selection'])
        self.assertEqual(accepted['status'], 'GRABBED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_missing_historical_assignment_log_keeps_previous_fallback(self):
        from orders.models import OrderStatusLog
        order_id = self.create('PUBLIC', provider_ids=[self.accounts[self.provider.pk].pk])
        OrderStatusLog.objects.filter(order_id=order_id, action=OrderStatusLog.Action.ASSIGN).delete()
        self.login(self.provider)
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))['data']
        self.assertTrue(result['requires_boss_selection'])

    def test_staff_created_direct_assignment_retains_boss_selection_fallback(self):
        self.login(self.staff)
        created = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'escort_mode': 'SINGLE', 'providers': [{'provider_id': self.accounts[self.provider.pk].pk}],
            'expected_amount': 10000, 'client_request_id': str(uuid4()),
        }, format='json'))['data']
        order_id = created['id']
        self.login(self.provider)
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))['data']
        self.assertTrue(result['requires_boss_selection'])
        self.login(self.observer)
        applied = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual(applied['status'], 'PENDING')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

    def test_missing_historical_creator_identity_keeps_previous_fallback(self):
        from orders.models import OrderStatusLog
        order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/assign/', {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))
        OrderStatusLog.objects.filter(order_id=order_id, action=OrderStatusLog.Action.CREATE).update(
            operator=None, operator_account=None,
        )
        self.login(self.provider)
        result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))['data']
        self.assertTrue(result['requires_boss_selection'])
