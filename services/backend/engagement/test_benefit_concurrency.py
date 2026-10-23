from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from django.db import close_old_connections, connections
from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APITransactionTestCase, APIClient
from rest_framework.exceptions import ValidationError
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from orders.models import ExperienceUse, Order
from coupons.models import UserCoupon
from coupons.claims import claim
from coupons.benefits import grant_periodic
from . import test_benefit_automation as fixtures
from .models import ConsumptionConfirmation, MemberBenefitGrant
from .confirmation import process_order
from .services import config


@skipUnlessDBFeature('has_select_for_update')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class BenefitConcurrencyTest(APITransactionTestCase):
    setUp = fixtures.BenefitAutomationTest.setUp
    service = fixtures.BenefitAutomationTest.service
    coupon = fixtures.BenefitAutomationTest.coupon
    tiers = fixtures.BenefitAutomationTest.tiers
    completed = fixtures.BenefitAutomationTest.completed

    def race(self, callback):
        config()
        barrier = Barrier(2)
        def work(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(work, range(2), timeout=30))

    def paid_race(self, services):
        token,_ = issue_account_tokens(self.account)
        def buy(index):
            client=APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+token)
            return client.post('/api/orders/create/', {'service_id':services[index].pk,
                'client_request_id':f'concurrent-paid-{index}'}, format='json').data['code']
        with patch('orders.views._schedule_auto_cancel'), patch('orders.views.notify_order_update'), patch('orders.views.enqueue_kook_dispatch'), patch('orders.views.enqueue_wecom_dispatch'):
            result = self.race(buy)
        self.assertEqual(sorted(result),[0,400])
        self.assertEqual(ExperienceUse.objects.count(),1)
        self.assertEqual(Order.objects.count(),1)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance,5000000-18800)

    def test_same_platform_daily_different_service_cannot_double_pay(self):
        self.paid_race([self.service('DAILY'),self.service('DAILY')])

    def test_new_first_cross_platforms_shares_one_qualification(self):
        self.paid_race([self.service('NEW_FIRST','PC'),self.service('NEW_FIRST','MOBILE')])

    def test_cloned_daily_templates_claim_once(self):
        service=self.service('DAILY_VALUE'); coupons=[self.coupon(service,'DAILY_VALUE') for _ in range(2)]
        def take(index):
            try:
                claim(ClubAccount.objects.get(pk=self.account.pk), self.boss, coupons[index].pk)
                return True
            except ValidationError:
                return False
        self.assertEqual(sorted(self.race(take)),[False,True])
        self.assertEqual(UserCoupon.objects.count(),1)

    def test_two_mature_orders_cross_threshold_grant_once(self):
        self.tiers(); orders=[self.completed(amount=150000) for _ in range(2)]
        with patch('engagement.tasks.enqueue_broadcast'):
            self.assertEqual(self.race(lambda i:process_order(orders[i].pk)),[True,True])
        self.account.refresh_from_db(); self.assertEqual(self.account.boss_type.vip_config.level,1)
        self.assertEqual(ConsumptionConfirmation.objects.count(),2)
        self.assertEqual(MemberBenefitGrant.objects.count(),1)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='COMPANION').count(),2)

    def test_same_mature_order_retry_is_idempotent(self):
        self.tiers(); order=self.completed()
        with patch('engagement.tasks.enqueue_broadcast'):
            self.assertEqual(sorted(self.race(lambda _:process_order(order.pk))),[False,True])
        self.assertEqual(ConsumptionConfirmation.objects.count(),1)
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX').count(),1)

    def test_vip6_parallel_grants_keep_one_safety_allowance(self):
        tiers=self.tiers(); self.account.boss_type=tiers[5].boss_type; self.account.save()
        self.race(lambda _:grant_periodic(ClubAccount.objects.get(pk=self.account.pk)))
        self.assertEqual(UserCoupon.objects.filter(coupon__scope='SAFE_BOX',status='UNUSED').count(),1)
        self.assertEqual(UserCoupon.objects.filter(coupon__grant_key__startswith='vip-month:').count(),10)
