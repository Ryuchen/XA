"""The support queue follows the time of each current customer request."""
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='COMPLETION',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AfterSaleQueueRealTest(APITestCase):
    def setUp(self):
        self.boss, self.provider = make_user(), make_provider()
        self.staff = make_console_user(['dashboard:view', 'order:view', 'order:settle', 'order:refund'])
        self.accounts = {u.pk: get_or_create_account_for_legacy_user(u) for u in (self.boss, self.provider, self.staff)}
        self.staff.admin_membership.account = self.accounts[self.staff.pk]
        self.staff.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        make_wallet(self.boss, balance=100000)

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def complete(self):
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'client_request_id': str(uuid4()),
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {}, format='json'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {}, format='json'))
        return order_id

    def open_case(self, order_id, at):
        self.login(self.boss)
        with patch('django.utils.timezone.now', return_value=at):
            return self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
                'reason': '订单售后测试', 'client_request_id': str(uuid4()),
            }, format='json'))

    def resolve(self, case_id):
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/aftersales/{case_id}/resolve/', {
            'resolution': '已与老板沟通',
        }, format='json'))

    def test_reopened_case_sort_uses_current_request_time_and_paginates(self):
        old_order, new_order, tied_order, untouched = [self.complete() for _ in range(4)]
        now = timezone.now()
        original = self.open_case(old_order, now - timedelta(minutes=10))
        self.resolve(original['id'])
        first = self.open_case(new_order, now - timedelta(minutes=5))
        tied = self.open_case(tied_order, now - timedelta(minutes=5))
        reopened = self.open_case(old_order, now - timedelta(minutes=1))
        self.assertEqual(reopened['id'], original['id'])

        self.login(self.staff)
        stats = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual(stats['open_aftersale_order_total'], 3)
        pages = [self.ok(self.client.get('/api/admin/orders/', {
            'aftersale_open': '1', 'page': page, 'page_size': 1,
        })) for page in (1, 2, 3)]
        self.assertEqual([page['total'] for page in pages], [3, 3, 3])
        self.assertTrue(all(page['aftersale_queue_order'] == 'opened_asc' for page in pages))
        self.assertEqual([page['list'][0]['id'] for page in pages], [new_order, tied_order, old_order])
        self.assertEqual(pages[0]['list'][0]['aftersale_open_at'], first['created_at'])
        self.assertEqual(pages[1]['list'][0]['aftersale_open_at'], tied['created_at'])
        self.assertEqual(pages[2]['list'][0]['aftersale_open_at'], reopened['created_at'])
        all_orders = self.ok(self.client.get('/api/admin/orders/', {'page_size': 10}))
        self.assertEqual([row['id'] for row in all_orders['list'][:4]], [untouched, tied_order, new_order, old_order])
        self.assertNotIn('aftersale_queue_order', all_orders)
        combined = self.ok(self.client.get('/api/admin/orders/', {'aftersale_open': '1', 'actionable_staff': '1'}))
        self.assertEqual(combined['total'], 0)
        self.assertEqual(combined['staff_queue_order'], 'deadline_asc')
        self.assertNotIn('aftersale_queue_order', combined)

        self.resolve(first['id'])
        self.login(self.staff)
        remaining = self.ok(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}))
        self.assertEqual(remaining['total'], 2)
        self.assertEqual([row['id'] for row in remaining['list']], [tied_order, old_order])
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['open_aftersale_order_total'], 2)

    def test_order_refund_keeps_open_case_visible_until_staff_resolves_it(self):
        order_id = self.complete()
        case = self.open_case(order_id, timezone.now())
        self.login(self.staff)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order_id}/refund-preview/'))
        self.assertTrue(preview['eligible'])
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/refund/', {
            'reason': '隔离测试先退款', 'client_request_id': str(uuid4()),
            'plan_token': preview['plan_token'],
        }, format='json'))
        order = self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))
        self.assertEqual((order['payment_status'], order['refund_summary']['remaining_amount']), ('REFUNDED', 0))
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['open_aftersale_order_total'], 1)
        queue = self.ok(self.client.get('/api/admin/orders/', {'aftersale_open': '1'}))
        self.assertEqual([row['id'] for row in queue['list']], [order_id])
        self.assertEqual(self.ok(self.client.get(f'/api/admin/aftersales/{case["id"]}/'))['status'], 'OPEN')
        self.resolve(case['id'])
        self.login(self.staff)
        self.assertEqual(self.ok(self.client.get('/api/admin/dashboard/stats'))['open_aftersale_order_total'], 0)
