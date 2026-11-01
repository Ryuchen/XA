from datetime import timedelta
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class PromotionWorkflowTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['promotion:view', 'promotion:edit', 'promotion:delete'])
        admin = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=admin)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        self.account = account
        make_wallet(boss, balance=100000)
        self.buyer = APIClient()
        self.buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.service = make_service(price=10000)

    def create_promotion(self, title, start, end, **extra):
        payload = {'title': title, 'scope': 'ALL', 'discount_rate': 80,
                   'start_at': start.isoformat(), 'end_at': end.isoformat(), **extra}
        response = self.client.post('/api/admin/promotions/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data['data']['id']

    def test_window_status_and_real_order_survives_promotion_deletion(self):
        now = timezone.now()
        lower = self.create_promotion('低优先级', now-timedelta(days=1), now+timedelta(days=1), priority=1)
        chosen = self.create_promotion('实际活动', now-timedelta(hours=1), now+timedelta(days=1), priority=5, discount_rate=70)
        future = self.create_promotion('未开始', now+timedelta(days=1), now+timedelta(days=2))
        ended = self.create_promotion('已结束', now-timedelta(days=2), now-timedelta(days=1))
        disabled = self.create_promotion('停用', now-timedelta(days=1), now+timedelta(days=1), is_active=False)
        response = self.client.get('/api/admin/promotions/', {'page_size': 100})
        self.assertEqual(response.status_code, 200)
        statuses = {row['id']: row['window_status'] for row in response.data['data']['list']}
        self.assertEqual({k: statuses[k] for k in (lower, chosen, future, ended, disabled)},
                         {lower: 'IN_WINDOW', chosen: 'IN_WINDOW', future: 'SCHEDULED', ended: 'ENDED', disabled: 'DISABLED'})
        coupon = Coupon.objects.create(name='互斥验证券', discount_type='DIRECT', amount=1000, threshold=0)
        claimed = self.buyer.post(f'/api/coupons/{coupon.pk}/claim/')
        self.assertEqual(claimed.data['code'], 0, claimed.data)
        claim = UserCoupon.objects.get(account=self.account, coupon=coupon)
        conflict = self.buyer.post('/api/orders/create/', {'service_id': self.service.pk, 'user_coupon_id': claim.pk,
                                    'client_request_id': str(uuid4())}, format='json')
        self.assertEqual(conflict.data['code'], 400, conflict.data)
        self.assertIn('活动', conflict.data['msg'])
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'UNUSED')
        result = self.buyer.post('/api/orders/create/', {'service_id': self.service.pk, 'dispatch_route': 'STAFF',
                                  'client_request_id': str(uuid4())}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual((order.promotion_id, order.original_amount, order.promo_discount, order.amount), (chosen, 10000, 3000, 7000))
        original_commission = (order.commission_type, order.commission_rate, order.commission_fixed)
        self.assertEqual(self.client.delete(f'/api/admin/promotions/{chosen}/').status_code, 200)
        order.refresh_from_db()
        self.assertIsNone(order.promotion_id)
        self.assertEqual((order.original_amount, order.promo_discount, order.amount), (10000, 3000, 7000))
        self.assertEqual((order.commission_type, order.commission_rate, order.commission_fixed), original_commission)

    def test_promotion_list_requires_permission(self):
        staff = make_console_user(['order:view'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.get('/api/admin/promotions/').status_code, 403)
