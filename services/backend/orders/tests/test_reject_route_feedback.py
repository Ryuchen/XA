"""Role-scoped feedback after actual staff and public order rejection."""
from uuid import uuid4
from datetime import timedelta
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
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

    def test_direct_grab_mode_survives_rejection_and_next_provider_can_start(self):
        from orders.models import Order
        from orders.timeout import expired_order_candidates, order_timeout_minutes

        order_id = self.create('PUBLIC', dispatch_mode='AUTO_GRAB')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        # The first waiting period elapsed while the provider had the order;
        # a rejection starts a fresh waiting period without charging again.
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        rejection_started = timezone.now()
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '档期变化'}, format='json'))
        returned = Order.objects.get(pk=order_id)
        duration = timedelta(minutes=order_timeout_minutes(returned))
        self.assertGreaterEqual(returned.auto_cancel_at, rejection_started + duration - timedelta(seconds=1))
        self.assertLessEqual(returned.auto_cancel_at, timezone.now() + duration)
        self.assertFalse(expired_order_candidates().filter(pk=order_id).exists())

        self.login(self.boss)
        returned_to_boss = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']
        self.assertFalse(returned_to_boss['requires_boss_selection'])
        node = shutil.which('node')
        if node:
            repo = Path(settings.BASE_DIR).parent.parent
            script = ("let input='';process.stdin.on('data',x=>input+=x);"
                      "process.stdin.on('end',()=>{const p=require('./apps/boss-miniapp/native/core/orders').present(JSON.parse(input));"
                      "console.log(JSON.stringify({providerDisplay:p.providerDisplay,canSelectProvider:p.canSelectProvider}));});")
            rendered = subprocess.run([node, '-e', script], input=json.dumps(returned_to_boss, default=str),
                text=True, capture_output=True, encoding='utf-8', cwd=repo, timeout=10)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertEqual(json.loads(rendered.stdout), {'providerDisplay': '等待陪玩直接接单', 'canSelectProvider': False})

        self.login(self.staff)
        admin_order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))['data']
        self.assertEqual((admin_order['status'], admin_order['dispatch_route'], admin_order['requires_boss_selection']),
                         ('PENDING', 'PUBLIC', False))

        self.login(self.observer)
        pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))['data']
        returned = next(row for row in pool if row['id'] == order_id)
        self.assertFalse(returned['requires_boss_selection'])
        accepted = self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))['data']
        self.assertEqual(accepted['status'], 'GRABBED')
        self.assertIn('START', accepted['allowed_actions'])

        self.login(self.boss)
        order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['data']
        self.assertEqual(order['status'], 'GRABBED')
        self.assertEqual(order['provider']['id'], self.observer.pk)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 40000)

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
