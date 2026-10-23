from types import SimpleNamespace
from unittest.mock import patch
from django.db import transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from club_accounts.models import ClubAccount
from orders.models import Order
from orders.state_machine import transition
from orders.tests.factories import make_user, make_wallet, make_order
from users.models import BossType
from users.boss_types import default_miniapp_boss_type
from wallet.models import RechargeRecord
from .models import VipTier, MemberBenefitGrant, Broadcast, ConsumptionConfirmation
from .services import update_vip, completed_consumption, config, award_order_points
from .views import member_data
from .serializers import TierSerializer
from .tests import mapped


class ConsumptionVipTests(TestCase):
    def setUp(self):
        self.user=make_user()
        self.account=mapped(self.user)
        make_wallet(self.user,balance=1000000)
        self.guest=default_miniapp_boss_type()
        self.account.boss_type=self.guest;self.account.save(update_fields=['boss_type'])
        self.user.boss_type=self.guest;self.user.save(update_fields=['boss_type'])
        self.tiers=[]
        for level,threshold,discount in [(1,300000,98),(2,600000,97),(3,1000000,96)]:
            bt=BossType.objects.create(name=f'VIP{level}',discount_rate=discount)
            self.tiers.append(VipTier.objects.create(boss_type=bt,level=level,recharge_threshold=threshold))

    def order(self,amount,**kwargs):
        order = make_order(self.user,amount=amount,customer_account=self.account,
                          **{'status':'COMPLETED','payment_status':'PAID',**kwargs})
        if order.status == 'COMPLETED' and order.payment_status == 'PAID' and not order.is_gift_snapshot:
            ConsumptionConfirmation.objects.create(order=order, account=self.account, business_cycle=self.account.business_cycle, amount=order.amount)
        return order

    def test_exact_boundaries_upgrade_once_and_preserve_legacy_type(self):
        self.order(299999,original_amount=600000)
        update_vip(self.account);self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.guest.pk)
        self.order(1)
        update_vip(self.account);update_vip(self.account)
        self.account.refresh_from_db();self.user.refresh_from_db()
        self.assertEqual((self.account.boss_type_id,self.user.boss_type_id),(self.tiers[0].boss_type_id,)*2)
        self.assertEqual(MemberBenefitGrant.objects.count(),1)
        self.order(300000)
        update_vip(self.account);self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.tiers[1].boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.count(),2)
        self.assertEqual(Broadcast.objects.filter(kind='UPGRADE').count(),2)

    def test_recharge_pending_cancelled_refunded_and_free_orders_do_not_qualify(self):
        RechargeRecord.objects.create(user=self.user,account=self.account,amount=1000000,gift_amount=9000000)
        self.order(500000,status='PENDING')
        self.order(500000,status='CANCELLED')
        self.order(500000,payment_status='REFUNDED')
        self.order(500000,payment_status='UNPAID')
        self.order(0,original_amount=500000)
        update_vip(self.account);self.account.refresh_from_db()
        self.assertEqual(completed_consumption(self.account),0)
        self.assertEqual(self.account.boss_type_id,self.guest.pk)
        self.assertFalse(MemberBenefitGrant.objects.exists())

    def test_completion_does_not_upgrade_without_report_confirmation(self):
        order=self.order(300000,status='IN_SERVICE',settlement_mode='REPORT_APPROVAL')
        transition(order.pk,'COMPLETED')
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.guest.pk)

    def test_completion_failure_rolls_back_status_and_upgrade(self):
        order=self.order(300000,status='IN_SERVICE')
        with patch('orders.state_machine.OrderStatusLog.objects.create',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):transition(order.pk,'COMPLETED')
        order.refresh_from_db();self.account.refresh_from_db()
        self.assertEqual(order.status,'IN_SERVICE')
        self.assertEqual(self.account.boss_type_id,self.guest.pk)

    def test_completed_gifts_do_not_upgrade_when_points_disabled(self):
        from orders.tests.factories import make_service, make_service_category
        conf=config();conf.points_enabled=False;conf.save()
        order=self.order(300000, service=make_service(service_category=make_service_category(is_gift=True)))
        with transaction.atomic():award_order_points(order)
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.guest.pk)
        self.assertEqual(completed_consumption(self.account),0)

    def test_refund_reduces_progress_without_automatic_downgrade_or_duplicate_grants(self):
        order=self.order(300000)
        update_vip(self.account)
        order.payment_status='REFUNDED';order.save(update_fields=['payment_status'])
        update_vip(self.account);self.account.refresh_from_db()
        data=member_data(self.account)
        self.assertEqual(data['consumption_amount'],0)
        self.assertEqual(data['vip']['level'],1)
        self.assertEqual(data['next_vip']['level'],2)
        self.assertEqual(MemberBenefitGrant.objects.count(),1)

    def test_disabled_type_or_tier_does_not_upgrade(self):
        self.order(1000000)
        for tier in self.tiers:
            tier.active=False;tier.save(update_fields=['active'])
        update_vip(self.account);self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.guest.pk)
        self.tiers[0].active=True;self.tiers[0].save(update_fields=['active'])
        bt=self.tiers[0].boss_type;bt.is_active=False;bt.save(update_fields=['is_active'])
        update_vip(self.account);self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id,self.guest.pk)

    def test_consumption_threshold_alias_and_legacy_parameter(self):
        tier=self.tiers[0]
        self.assertEqual(TierSerializer(tier).data['consumption_threshold'],300000)
        serializer=TierSerializer(tier,data={'consumption_threshold':310000},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors);serializer.save()
        tier.refresh_from_db();self.assertEqual(tier.recharge_threshold,310000)
        serializer=TierSerializer(tier,data={'recharge_threshold':320000},partial=True)
        self.assertTrue(serializer.is_valid(),serializer.errors);serializer.save()
        serializer=TierSerializer(tier,data={'recharge_threshold':100,'consumption_threshold':200},partial=True)
        self.assertFalse(serializer.is_valid())


@skipUnlessDBFeature('has_select_for_update')
class ConsumptionVipConcurrencyTests(TransactionTestCase):
    setUp = ConsumptionVipTests.setUp
    order = ConsumptionVipTests.order

    def race(self, callback):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        barrier = Barrier(2)

        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return callback(index)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2), timeout=30))

    def test_two_completions_cross_threshold_and_grant_once(self):
        config()
        orders = [self.order(150000) for _ in range(2)]
        with patch('engagement.tasks.enqueue_broadcast'):
            self.race(lambda i: update_vip(ClubAccount.objects.get(pk=self.account.pk)))
        self.account.refresh_from_db(); self.user.refresh_from_db()
        self.assertEqual(completed_consumption(self.account), 300000)
        self.assertEqual((self.account.boss_type_id, self.user.boss_type_id), (self.tiers[0].boss_type_id,) * 2)
        self.assertEqual(MemberBenefitGrant.objects.count(), 1)
        self.assertEqual(Broadcast.objects.filter(kind='UPGRADE').count(), 1)

    def test_concurrent_retries_with_stale_account_do_not_downgrade_or_duplicate(self):
        config()
        self.order(600000)
        stale_accounts = [ClubAccount.objects.get(pk=self.account.pk) for _ in range(2)]
        with patch('engagement.tasks.enqueue_broadcast'):
            self.race(lambda i: update_vip(stale_accounts[i]))
        self.account.refresh_from_db()
        self.assertEqual(self.account.boss_type_id, self.tiers[1].boss_type_id)
        self.assertEqual(MemberBenefitGrant.objects.count(), 2)
        self.assertEqual(Broadcast.objects.filter(kind='UPGRADE').count(), 1)
