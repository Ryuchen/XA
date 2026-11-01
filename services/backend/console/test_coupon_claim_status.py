from datetime import timedelta
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class CouponClaimStatusTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        link_legacy_relations(self.boss, self.account)
        make_wallet(self.boss, balance=100000)
        self.buyer = APIClient()
        self.buyer.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        staff = make_console_user(['coupon:view'])
        admin = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=admin)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        self.service = make_service(price=10000)

    def claim(self, name):
        coupon = Coupon.objects.create(name=name, discount_type='DIRECT', amount=1000, threshold=0, valid_to=None)
        response = self.buyer.post(f'/api/coupons/{coupon.pk}/claim/')
        self.assertEqual(response.data['code'], 0, response.data)
        return coupon, UserCoupon.objects.get(account=self.account, coupon=coupon)

    def listing(self, status=''):
        response = self.client.get('/api/admin/user-coupons/', {'status': status, 'page_size': 100})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['claim_status_version'], 1)
        return response.data['data']['list']

    def test_real_claims_and_order_use_match_each_status_filter(self):
        permanent, permanent_row = self.claim('永久券')
        individual, individual_row = self.claim('逐张到期券')
        override, override_row = self.claim('逐张时间优先券')
        disabled, disabled_row = self.claim('停用券')
        ordered, ordered_row = self.claim('已用券')
        now = timezone.now()
        # Move only test timestamps to cover boundaries without waiting for expiry.
        UserCoupon.objects.filter(pk=individual_row.pk).update(expires_at=now-timedelta(seconds=1))
        UserCoupon.objects.filter(pk=override_row.pk).update(expires_at=now+timedelta(days=1))
        Coupon.objects.filter(pk=override.pk).update(valid_to=now-timedelta(days=1))
        Coupon.objects.filter(pk=disabled.pk).update(is_active=False)
        result = self.buyer.post('/api/orders/create/', {'service_id': self.service.pk, 'user_coupon_id': ordered_row.pk,
            'dispatch_route': 'STAFF', 'client_request_id': str(uuid4())}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['id']
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='PAY').count(), 1)
        all_rows = {row['id']: row for row in self.listing()}
        expected = {permanent_row.pk:'UNUSED', individual_row.pk:'EXPIRED', override_row.pk:'UNUSED', disabled_row.pk:'DISABLED', ordered_row.pk:'USED'}
        self.assertEqual({key: all_rows[key]['status'] for key in expected}, expected)
        for status in set(expected.values()):
            rows = self.listing(status)
            self.assertEqual({row['id'] for row in rows}, {key for key,value in expected.items() if value == status})
            self.assertTrue(all(row['status'] == status for row in rows))
        self.assertIsNone(all_rows[permanent_row.pk]['effective_valid_to'])
        self.assertIsNotNone(all_rows[individual_row.pk]['effective_valid_to'])
        self.assertEqual(all_rows[ordered_row.pk]['order_no'], result.data['data']['order_no'])
        usable = self.buyer.get('/api/coupons/mine/', {'usable': 1})
        self.assertEqual(usable.data['code'], 0, usable.data)
        self.assertEqual({r['id'] for r in usable.data['data']}, {permanent_row.pk, override_row.pk})
        denied = self.buyer.post('/api/orders/create/', {'service_id': self.service.pk, 'user_coupon_id': individual_row.pk,
            'client_request_id': str(uuid4())}, format='json')
        self.assertNotEqual(denied.data['code'], 0, denied.data)
        cancelled = self.buyer.post(f'/api/orders/orders/{order_id}/cancel/', {}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        self.assertIn(ordered_row.pk, {row['id'] for row in self.listing('UNUSED')})

    def test_old_cycle_and_legacy_rows_are_not_mislabelled_expired(self):
        _, old = self.claim('旧周期券')
        self.account.business_cycle += 1
        self.account.save(update_fields=['business_cycle'])
        legacy_coupon = Coupon.objects.create(name='旧用户券', discount_type='DIRECT', amount=1000, valid_to=None)
        legacy = UserCoupon.objects.create(user=self.boss, coupon=legacy_coupon)
        previous = self.listing('PREVIOUS_CYCLE')
        self.assertEqual([row['id'] for row in previous], [old.pk])
        self.assertEqual(previous[0]['status_display'], '非当前周期')
        self.assertIn(legacy.pk, {row['id'] for row in self.listing('UNUSED')})
        self.assertEqual(self.listing('EXPIRED'), [])
        invalid = self.client.get('/api/admin/user-coupons/', {'status': 'NOPE'})
        self.assertEqual(invalid.status_code, 400)

    def test_claim_records_require_coupon_view_permission(self):
        self.claim('权限检查券')
        staff = make_console_user(['order:view'])
        account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.get('/api/admin/user-coupons/').status_code, 403)
