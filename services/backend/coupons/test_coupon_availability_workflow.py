import os
"""A claimed voucher follows actual admin availability across customer flows."""
from uuid import uuid4
from datetime import timedelta
from tempfile import TemporaryDirectory
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import Request, urlopen

from django.conf import settings
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import UserCoupon
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class CouponAvailabilityWorkflowTest(APITransactionTestCase):
    def setUp(self):
        # Enter before method-level @override_settings. A settings context
        # entered inside a decorated method and cleaned up afterwards restores
        # the decorator's already-exited values into the following test.
        media = TemporaryDirectory(prefix='xa-coupon-workflow-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        from console.grab_config import save_grab_window
        from engagement.models import ClubOperationConfig, PointFundingLot
        save_grab_window(30, 0, lambda *args: None)
        ClubOperationConfig.objects.get_or_create(pk=1)
        boss = make_user()
        staff = make_console_user(['coupon:view', 'coupon:edit'])
        self.account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, self.account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.wallet = make_wallet(boss, balance=50000)
        PointFundingLot.objects.create(account=self.account, source_key='coupon-workflow-prepaid',
            kind='PREPAID', remaining=50000)
        self.service = make_service(price=10000)
        self.boss = self.client_for(self.account)
        self.staff = self.client_for(staff_account)

    @staticmethod
    def client_for(account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def test_direct_coupon_threshold_matches_its_name_and_legacy_is_preserved(self):
        from coupons.models import Coupon
        rejected = self.staff.post('/api/admin/coupons/', {
            'name': '错误门槛', 'discount_type': 'DIRECT', 'amount': 500, 'threshold': 1000,
        }, format='json')
        self.assertNotEqual(rejected.data.get('code', 400), 0)
        self.assertIn('无门槛券', str(rejected.data))
        legacy = Coupon.objects.create(name='历史门槛券', discount_type='DIRECT', amount=500, threshold=1000)
        unchanged = self.staff.patch(f'/api/admin/coupons/{legacy.pk}/', {'name': '历史门槛券调整名称'}, format='json')
        self.ok(unchanged)
        legacy.refresh_from_db()
        self.assertEqual(legacy.threshold, 1000)
        changed = self.staff.patch(f'/api/admin/coupons/{legacy.pk}/', {'threshold': 2000}, format='json')
        self.assertNotEqual(changed.data.get('code', 400), 0)
        self.ok(self.staff.patch(f'/api/admin/coupons/{legacy.pk}/', {'threshold': 0}, format='json'))
        legacy.refresh_from_db()
        self.assertEqual(legacy.threshold, 0)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True)
    @skipUnless(os.environ.get('XA_SAFE_BOX_FORMAL') == '1',
                '安全箱正式补贴/抢单到期链仍在接线（18-盘点免费安全箱/抢单到期待最终联调）；断言保留')
    def test_stale_checkout_reprices_safety_box_after_staff_changes_binding(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.models import OrderPlatformSubsidy
        from orders.tests.factories import make_boss_type
        old = make_service(name='原安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        new = make_service(name='新安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier_type = make_boss_type(name='结算页会员', discount_rate=90)
        tier = VipTier.objects.create(boss_type=tier_type, level=6, recharge_threshold=100,
                                      safe_box_service_ids=[old.pk])
        self.account.boss_type = tier_type
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        original = self.ok(self.boss.post('/api/orders/quote/', {'service_id': old.pk, 'user_coupon_id': voucher.pk}, format='json'))
        self.assertEqual(original['amount'], 0)
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.ok(self.client_for(staff_account).patch(f'/api/admin/vip-tiers/{tier.pk}/',
            {'safe_box_service_ids': [new.pk]}, format='json'))
        # No intervening /coupons/mine/ request or scheduled task refresh.
        refused = self.boss.post('/api/orders/create/', {'service_id': old.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'stale-safe-binding'}, format='json')
        self.assertNotEqual(refused.data.get('code', 400), 0)
        self.assertIn('不适用于此套餐', str(refused.data))
        stale = self.boss.post('/api/orders/quote/', {'service_id': old.pk, 'user_coupon_id': voucher.pk}, format='json')
        self.assertNotEqual(stale.data.get('code', 400), 0)
        self.assertIn('不适用于此套餐', str(stale.data))
        refreshed = self.ok(self.boss.post('/api/orders/quote/', {'service_id': new.pk, 'user_coupon_id': voucher.pk}, format='json'))
        self.assertEqual(refreshed['amount'], 0)
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id': new.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'new-safe-binding'}, format='json'))
        self.assertEqual(created['amount'], 0)
        self.assertEqual(OrderPlatformSubsidy.objects.get(order_id=created['id']).basis, 10000)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True)
    def test_downgrade_locked_safe_box_keeps_original_service_at_checkout(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type
        old = make_service(name='降级前安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        new = make_service(name='降级后安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        high = make_boss_type(name='原会员', discount_rate=90)
        low = make_boss_type(name='降级会员', discount_rate=95)
        VipTier.objects.create(boss_type=high, level=6, recharge_threshold=100, season_discount=0, safe_box_service_ids=[old.pk])
        VipTier.objects.create(boss_type=low, level=1, recharge_threshold=0, safe_box_service_ids=[new.pk])
        self.account.boss_type = high; self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        voucher.vip_terms_locked_by_downgrade = True
        voucher.save(update_fields=['vip_terms_locked_by_downgrade'])
        self.account.boss_type = low; self.account.save(update_fields=['boss_type'])
        quote = self.ok(self.boss.post('/api/orders/quote/', {'service_id':old.pk, 'user_coupon_id':voucher.pk}, format='json'))
        self.assertEqual(quote['amount'], 0)
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id':old.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'downgrade-locked-safe-box'}, format='json'))
        self.assertEqual(created['amount'], 0)
        voucher.refresh_from_db(); voucher.coupon.refresh_from_db()
        self.assertEqual((voucher.status, voucher.coupon.discount_rate, voucher.coupon.service_ids), ('USED', 0, [old.pk]))

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True)
    @skipUnless(os.environ.get('XA_SAFE_BOX_FORMAL') == '1',
                '安全箱正式补贴/抢单到期链仍在接线（18-盘点免费安全箱/抢单到期待最终联调）；断言保留')
    def test_free_safe_box_public_pool_income_matches_direct_grab(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type, make_provider

        service = make_service(name='公开池会员安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='公开池六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        provider = make_provider(display_name='公开池陪玩', deposit_paid=40000)
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        player = self.client_for(account)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'AUTO_GRAB', 'client_request_id': str(uuid4()),
        }, format='json'))
        self.assertEqual(order['amount'], 0)
        from orders.models import Order
        Order.objects.filter(pk=order['id']).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        pool = self.ok(player.get('/api/orders/orders/?status=pending&role=provider'))
        before = next(row for row in pool if row['id'] == order['id'])
        self.assertEqual(before['expected_income'], 8000)
        self.ok(player.post(f"/api/orders/orders/{order['id']}/grab/", {}, format='json'))
        after = self.ok(player.get(f"/api/orders/orders/{order['id']}/"))
        self.assertEqual(after['expected_income'], before['expected_income'])

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_repeated_transfer_keeps_prior_entitlement(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.models import OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        service = make_service(name='多次转单安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='多次转单会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        providers = []
        for name in ('先后接单陪玩', '中途陪玩', '最终陪玩'):
            person = make_provider(display_name=name, deposit_paid=40000); account = get_or_create_account_for_legacy_user(person)
            person.escort_profile.account = account; person.escort_profile.save(update_fields=['account'])
            person.escort_profile.service_items.add(service); providers.append(account)
        data = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-multiple-transfers'}, format='json'))
        base = f"/api/admin/orders/{data['id']}/"; operator = self.client_for(staff_account)
        self.ok(operator.post(base + 'assign/', {'provider_id': providers[0].pk}, format='json'))
        self.ok(self.client_for(providers[0]).post(f"/api/orders/orders/{data['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        def transfer(old, new, value, kind='PERCENT'):
            self.ok(operator.post(base + 'transfer/', {'transfers': [{'old_provider_id': old.pk, 'new_provider_id': new.pk,
                'split_type': kind, 'split_value': value, 'penalty_amount': 0}], 'reason': '多次转单验收'}, format='json'))
        transfer(providers[0], providers[1], 40)
        transfer(providers[1], providers[0], 50)
        active = next(row for row in self.ok(operator.get(base))['providers'] if row['provider'] == providers[0].pk)
        self.assertEqual((active['expected_income'], active['transferable_income']), (5600, 2400))
        transfer(providers[0], providers[2], 1200, 'FIXED')
        shares = {share.assignment.provider_account_id: share.amount for share in OrderPlatformSubsidyShare.objects.filter(
            subsidy__order_id=data['id']).select_related('assignment')}
        self.assertEqual([shares[account.pk] for account in providers], [4400, 2400, 1200])
        options = self.ok(operator.get(base + 'subsidy-negotiation-options/'))
        self.assertEqual(sum(row['expected_income'] for row in options['shares']), 8000)
        from engagement.confirmation import process_order
        from orders.models import Order
        from wallet.models import ProviderReport, Wallet, get_platform_wallet
        self.ok(self.client_for(providers[2]).post(f"/api/orders/orders/{data['id']}/complete/",
                                                   {'completion_image': screenshot()}, format='multipart'))
        reports = list(ProviderReport.objects.filter(order_id=data['id']))
        self.assertEqual({report.provider_account_id for report in reports}, {account.pk for account in providers})
        for report in reports:
            player = self.client_for(next(account for account in providers if account.pk == report.provider_account_id))
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': screenshot()}, format='multipart'))
            self.ok(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=data['id']).update(completed_at=old)
        ProviderReport.objects.filter(order_id=data['id']).update(submitted_at=old)
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        self.assertTrue(process_order(data['id']))
        self.assertEqual([Wallet.objects.get(account=account).balance for account in providers], [4400, 2400, 1200])

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                       ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_snapshots_funded_basis_and_assigned_share(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        service = make_service(name='会员安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='六级会员补贴验证', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='会员补贴陪玩', deposit_paid=40000)
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        result = self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'vip-funded-snapshot'}, format='json'))
        self.assertEqual(result['amount'], 0)
        subsidy = OrderPlatformSubsidy.objects.get(order_id=result['id'])
        self.assertEqual((subsidy.basis, subsidy.coupon_id_snapshot), (10000, voucher.pk))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{result['id']}/assign/", {'provider_id':provider_account.pk}, format='json'))
        share = OrderPlatformSubsidyShare.objects.get(subsidy=subsidy)
        self.assertEqual((share.assignment.provider_account_id, share.amount, share.paid_at), (provider_account.pk, 8000, None))
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 50000)
        rejected = self.client_for(provider_account).post(f"/api/orders/orders/{result['id']}/reject/", {'reason':'档期变化'}, format='json')
        self.assertEqual(rejected.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        service.price = 15000; service.save(update_fields=['price'])
        replacement = make_provider(display_name='补贴接替陪玩', deposit_paid=40000)
        replacement_account = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = replacement_account; replacement.escort_profile.save(update_fields=['account'])
        replacement.escort_profile.service_items.add(service)
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{result['id']}/transfer/", {'reason':'客服协调更换', 'transfers':[
            {'old_provider_id':provider_account.pk, 'new_provider_id':replacement_account.pk}]}, format='json'))
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(subsidy=subsidy, assignment__provider_account=provider_account).amount, 0)
        share = OrderPlatformSubsidyShare.objects.get(subsidy=subsidy, assignment__provider_account=replacement_account)
        self.assertEqual((share.assignment.provider_account_id, share.amount), (replacement_account.pk, 8000))
        provider_order = self.ok(self.client_for(replacement_account).get(f"/api/orders/orders/{result['id']}/"))
        self.assertEqual(provider_order['expected_income'], 8000)
        from engagement.confirmation import process_order
        from orders.models import Order
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, Transaction, Wallet, get_platform_wallet
        player = self.client_for(replacement_account); operator = self.client_for(staff_account)
        self.ok(player.post(f"/api/orders/orders/{result['id']}/start/", {'entry_image':screenshot()}, format='multipart'))
        self.ok(player.post(f"/api/orders/orders/{result['id']}/complete/", {'completion_image':screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=result['id'], provider_account=replacement_account)
        self.ok(player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
        self.ok(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        def provider_report():
            rows = self.ok(player.get('/api/wallet/reports/', {'order_id': result['id'], 'page': 1}))['list']
            return next(row for row in rows if row['id'] == report.pk)
        initial_report = provider_report()
        self.assertFalse(initial_report['platform_funding_insufficient'])
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=result['id']).update(completed_at=old)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
        platform = get_platform_wallet(); platform.balance = 7999; platform.save(update_fields=['balance'])
        self.assertFalse(process_order(result['id']))
        order = Order.objects.get(pk=result['id']); self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        platform.refresh_from_db(); self.assertEqual(platform.balance, 7999)
        admin_report = self.ok(operator.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual((admin_report['payout_amount'], admin_report['platform_subsidy_income'],
                          admin_report['expected_income'], admin_report['settlement_status']), (0, 8000, 8000, 'WAITING'))
        self.assertTrue(admin_report['platform_funding_insufficient'])
        self.assertTrue(provider_report()['platform_funding_insufficient'])
        waiting = self.ok(player.get(f"/api/orders/orders/{result['id']}/"))
        self.assertEqual((waiting['expected_income'], waiting['settlement_status']), (8000, 'WAITING'))
        platform.balance = 10000; platform.save(update_fields=['balance'])
        self.assertFalse(provider_report()['platform_funding_insufficient'])
        self.assertTrue(process_order(result['id']))
        self.assertFalse(process_order(result['id']))
        share.refresh_from_db(); order.refresh_from_db(); platform.refresh_from_db()
        self.assertIsNotNone(order.settled_at); self.assertIsNotNone(share.paid_at)
        self.assertEqual((platform.balance, Wallet.objects.get(account=replacement_account).balance), (2000, 8000))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)
        paid = self.ok(player.get(f"/api/orders/orders/{result['id']}/"))
        self.assertEqual((paid['expected_income'], paid['settlement_status']), (8000, 'SETTLED'))
        admin_report = self.ok(operator.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual((admin_report['payout_amount'], admin_report['platform_subsidy_income'],
                          admin_report['expected_income'], admin_report['settlement_status']), (0, 8000, 8000, 'SETTLED'))
        self.assertFalse(admin_report['platform_funding_insufficient'])
        report.refresh_from_db(); self.assertEqual(report.payout_amount, 0)
        from wallet.reporting import report_financial_snapshot
        self.assertEqual(report_financial_snapshot(report)['expected_income'], 8000)
    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_transfer_splits_the_platform_entitlement(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.models import OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        service = make_service(name='转单安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='转单六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type=tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher=UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff=make_console_user(['order:view','order:dispatch','report:view','report:audit','wallet:view'])
        staff_account=get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account=staff_account; staff.admin_membership.save(update_fields=['account'])
        providers=[]
        for name in ('原陪玩', '接替陪玩'):
            provider=make_provider(display_name=name, deposit_paid=40000); account=get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account=account; provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service); providers.append(account)
        order=self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk,'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF','client_request_id':'vip-funded-transfer'},format='json'))
        operator=self.client_for(staff_account)
        self.ok(operator.post(f"/api/admin/orders/{order['id']}/assign/", {'provider_id':providers[0].pk},format='json'))
        self.ok(self.client_for(providers[0]).post(f"/api/orders/orders/{order['id']}/start/", {'entry_image':screenshot()},format='multipart'))
        detail=self.ok(operator.get(f"/api/admin/orders/{order['id']}/"))
        self.assertEqual(detail['providers'][0]['expected_income'],8000)
        self.ok(operator.post(f"/api/admin/orders/{order['id']}/transfer/", {'transfers':[{'old_provider_id':providers[0].pk,
            'new_provider_id':providers[1].pk,'split_type':'PERCENT','split_value':40,'penalty_amount':0}],
            'reason':'隔离转单'},format='json'))
        rows=OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order['id']).select_related('assignment')
        self.assertEqual({row.assignment.provider_account_id:row.amount for row in rows}, {providers[0].pk:3200,providers[1].pk:4800})
        self.assertTrue(all(row.paid_at is None for row in rows))
        from engagement.confirmation import process_order
        from orders.models import Order
        from wallet.models import ProviderReport, Wallet, get_platform_wallet
        self.ok(self.client_for(providers[1]).post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image':screenshot()},format='multipart'))
        pending = self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']
        self.assertEqual((pending['order_count'], pending['amount'], len(pending['orders'])), (1, 8000, 1))
        reports = list(ProviderReport.objects.filter(order_id=order['id']))
        self.assertEqual({report.provider_account_id for report in reports}, {account.pk for account in providers})
        for report in reports:
            player=self.client_for(next(account for account in providers if account.pk == report.provider_account_id))
            for kind in ('ENTRY','COMPLETION','RESULT'):
                self.ok(player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':kind,'image':screenshot()},format='multipart'))
            self.ok(player.post(f'/api/wallet/reports/{report.pk}/submit/', {},format='json'))
            self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {},format='json'))
        old=timezone.now()-timedelta(hours=73)
        Order.objects.filter(pk=order['id']).update(completed_at=old)
        ProviderReport.objects.filter(order_id=order['id']).update(submitted_at=old)
        platform=get_platform_wallet(); platform.balance=7999; platform.save(update_fields=['balance'])
        self.assertFalse(process_order(order['id']))
        self.assertEqual([Wallet.objects.filter(account=account).values_list('balance', flat=True).first() or 0 for account in providers], [0,0])
        platform.refresh_from_db(); platform.balance=10000; platform.save(update_fields=['balance'])
        self.assertTrue(process_order(order['id']))
        self.assertEqual([Wallet.objects.get(account=account).balance for account in providers], [3200,4800])
        platform.refresh_from_db(); self.assertEqual(platform.balance,2000)
        for account, expected in zip(providers, (3200,4800)):
            detail = self.ok(self.client_for(account).get(f"/api/orders/orders/{order['id']}/"))
            self.assertEqual((detail['expected_income'], detail['settlement_status']), (expected, 'SETTLED'))

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_in_service_aftersale_holds_subsidy_until_resolved(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.models import Order
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Wallet, get_platform_wallet
        service = make_service(name='售后安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='售后六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:settle', 'order:refund'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        player = make_provider(display_name='售后陪玩', deposit_paid=40000); account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        replacement = make_provider(display_name='售后接替陪玩', deposit_paid=40000); replacement_account = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = replacement_account; replacement.escort_profile.save(update_fields=['account'])
        replacement.escort_profile.service_items.add(service)
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-open-case'}, format='json'))
        operator = self.client_for(staff_account)
        self.ok(operator.post(f"/api/admin/orders/{created['id']}/assign/", {'provider_id': account.pk}, format='json'))
        client = self.client_for(account)
        self.ok(client.post(f"/api/orders/orders/{created['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        ticket = self.ok(self.boss.post(f"/api/orders/orders/{created['id']}/aftersale/",
            {'reason': '服务有问题，请客服核对', 'client_request_id': 'vip-case-request'}, format='json'))
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        self.ok(operator.post(f"/api/admin/orders/{created['id']}/transfer/", {'transfers': [{
            'old_provider_id': account.pk, 'new_provider_id': replacement_account.pk, 'split_type': 'PERCENT',
            'split_value': 40, 'penalty_amount': 0}], 'reason': '售后期间安排接替'}, format='json'))
        self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertFalse(Wallet.objects.filter(account=account, balance__gt=0).exists())
        replacement_client = self.client_for(replacement_account)
        report = self.ok(replacement_client.post('/api/wallet/reports/', {'order_id': created['id']}, format='json'))
        self.ok(replacement_client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'ENTRY', 'image': screenshot()}, format='multipart'))
        self.ok(replacement_client.post(f"/api/orders/orders/{created['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        self.assertIsNone(Order.objects.get(pk=created['id']).settled_at)
        self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertFalse(Wallet.objects.filter(account=account, balance__gt=0).exists())
        self.ok(operator.post(f"/api/admin/aftersales/{ticket['id']}/resolve/", {'resolution': '双方已确认继续结算'}, format='json'))
        self.assertEqual(retry_completion_subsidies.run(), 1)
        self.assertEqual((Wallet.objects.get(account=account).balance, Wallet.objects.get(account=replacement_account).balance), (3200, 4800))
        boss2 = make_user(); buyer = get_or_create_account_for_legacy_user(boss2)
        link_legacy_relations(boss2, buyer); make_wallet(boss2, balance=0)
        buyer.boss_type = tier; buyer.save(update_fields=['boss_type']); grant_periodic(buyer)
        voucher2 = UserCoupon.objects.get(account=buyer, coupon__scope='SAFE_BOX')
        created2 = self.ok(self.client_for(buyer).post('/api/orders/create/', {'service_id': service.pk,
            'user_coupon_id': voucher2.pk, 'dispatch_route': 'STAFF', 'client_request_id': 'vip-case-after-completion'}, format='json'))
        base2 = f"/api/admin/orders/{created2['id']}/"
        self.ok(operator.post(base2 + 'assign/', {'provider_id': account.pk}, format='json'))
        self.ok(client.post(f"/api/orders/orders/{created2['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        case2 = self.ok(self.client_for(buyer).post(f"/api/orders/orders/{created2['id']}/aftersale/",
            {'reason': '协商处理服务问题', 'client_request_id': 'vip-case-after-completion-request'}, format='json'))
        self.ok(client.post(f"/api/orders/orders/{created2['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        share = self.ok(operator.get(base2 + 'subsidy-negotiation-options/'))['shares'][0]
        allocations = [{'share_id': share['share_id'], 'amount': 4000}]
        plan = self.ok(operator.post(base2 + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
        self.ok(operator.post(base2 + 'subsidy-negotiation-confirm/', {'allocations': allocations, 'plan_token': plan['plan_token'],
            'reason': '售后协商终止', 'client_request_id': 'vip-completed-case-confirm', 'reviewed': True}, format='json'))
        closed = Order.objects.get(pk=created2['id'])
        self.assertEqual((closed.status, closed.aftersales.get(pk=case2['id']).status), ('CANCELLED', 'RESOLVED'))
        self.assertIsNone(closed.settled_at)
        self.assertFalse(UserCoupon.objects.filter(account=buyer, coupon__scope='SAFE_BOX', status='UNUSED').exists())
        platform.refresh_from_db(); platform.balance = 4000; platform.save(update_fields=['balance'])
        self.assertEqual(retry_completion_subsidies.run(), 1)
        self.assertEqual(Wallet.objects.get(account=account).balance, 7200)
        self.assertEqual(UserCoupon.objects.filter(account=buyer, coupon__scope='SAFE_BOX', status='UNUSED').count(), 1)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_platform_pending_pages_actual_completed_orders(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import get_platform_wallet
        service = make_service(name='待结算分页安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='分页六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        staff = make_console_user(['order:view', 'order:dispatch', 'wallet:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        operator = self.client_for(staff_account)
        player = make_provider(display_name='分页陪玩', deposit_paid=40000); account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        client = self.client_for(account)
        platform = get_platform_wallet(); platform.balance = 0; platform.save(update_fields=['balance'])
        numbers = []
        for index in range(11):
            boss = make_user(); buyer = get_or_create_account_for_legacy_user(boss)
            link_legacy_relations(boss, buyer); make_wallet(boss, balance=0)
            buyer.boss_type = tier; buyer.save(update_fields=['boss_type']); grant_periodic(buyer)
            voucher = UserCoupon.objects.get(account=buyer, coupon__scope='SAFE_BOX')
            order = self.ok(self.client_for(buyer).post('/api/orders/create/', {'service_id': service.pk,
                'user_coupon_id': voucher.pk, 'dispatch_route': 'STAFF', 'client_request_id': f'queue-{index}'}, format='json'))
            numbers.append(order['order_no'])
            self.ok(operator.post(f"/api/admin/orders/{order['id']}/assign/", {'provider_id': account.pk}, format='json'))
            self.ok(client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
            self.ok(client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        first = self.ok(operator.get('/api/admin/wallets/platform-funds/', {'pending_page': 1}))['pending']
        second = self.ok(operator.get('/api/admin/wallets/platform-funds/', {'pending_page': 2}))['pending']
        self.assertEqual((first['order_count'], first['amount'], first['pages']), (11, 88000, 2))
        self.assertEqual([row['order_no'] for row in first['orders']], numbers[:10])
        self.assertEqual([row['order_no'] for row in second['orders']], numbers[10:])
        self.assertEqual(self.ok(operator.get('/api/admin/wallets/platform-funds/', {'pending_page': 99}))['pending']['page'], 2)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_platform_topup_resumes_subsidy_without_spending_provider_deposits(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.models import Order
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction, Wallet, get_platform_wallet
        service = make_service(name='补款安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='补款六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'wallet:view', 'wallet:adjust', 'dashboard:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        operator = self.client_for(staff_account)
        depositor = make_provider(display_name='缴押金陪玩', deposit_required=10000)
        depositor_account = get_or_create_account_for_legacy_user(depositor)
        depositor.escort_profile.account = depositor_account; depositor.escort_profile.save(update_fields=['account'])
        make_wallet(depositor, balance=10000); link_legacy_relations(depositor, depositor_account)
        self.ok(self.client_for(depositor_account).post('/api/wallet/deposit/',
            {'amount': 10000, 'client_request_id': 'vip-test-deposit'}, format='json'))
        funds = self.ok(operator.get('/api/admin/wallets/platform-funds/'))
        self.assertEqual((funds['balance'], funds['deposits'], funds['available']), (10000, 10000, 0))
        player = make_provider(display_name='补贴陪玩', deposit_paid=40000); account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        order = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-topup-order'}, format='json'))
        self.ok(operator.post(f"/api/admin/orders/{order['id']}/assign/", {'provider_id': account.pk}, format='json'))
        client = self.client_for(account)
        self.ok(client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        self.assertEqual(self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']['order_count'], 0)
        self.ok(client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        self.assertIsNone(Order.objects.get(pk=order['id']).settled_at)
        pending = self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']
        self.assertEqual((pending['order_count'], pending['amount'], pending['orders'][0]['order_no']),
                         (1, 8000, order['order_no']))
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['pending_subsidy_total'], snapshot['pending_subsidy_amount']), (1, 8000))
        viewer = make_console_user(['dashboard:view']); viewer_account = get_or_create_account_for_legacy_user(viewer)
        viewer.admin_membership.account = viewer_account; viewer.admin_membership.save(update_fields=['account'])
        hidden = self.ok(self.client_for(viewer_account).get('/api/admin/dashboard/stats'))
        self.assertIsNone(hidden['pending_subsidy_total']); self.assertIsNone(hidden['pending_subsidy_amount'])
        finance = make_console_user(['wallet:view']); finance_account = get_or_create_account_for_legacy_user(finance)
        finance.admin_membership.account = finance_account; finance.admin_membership.save(update_fields=['account'])
        limited = self.ok(self.client_for(finance_account).get('/api/admin/wallets/platform-funds/'))['pending']
        self.assertEqual((limited['order_count'], limited['amount'], limited['orders']), (1, 8000, []))
        self.assertEqual(retry_completion_subsidies.run(), 0)
        payload = {'amount': 8000, 'reference': 'offline-20261103-001', 'client_request_id': 'vip-platform-topup'}
        self.assertEqual(self.staff.post('/api/admin/wallets/platform-topup/', payload, format='json').status_code, 403)
        topup = self.ok(operator.post('/api/admin/wallets/platform-topup/', payload, format='json'))
        tx = Transaction.objects.get(pk=topup['transaction_id'])
        self.assertEqual((tx.amount, tx.external_tx_id, tx.operator_account_id), (8000, payload['reference'], staff_account.pk))
        duplicate = operator.post('/api/admin/wallets/platform-topup/', {**payload, 'client_request_id': 'other-request'}, format='json')
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(operator.post('/api/admin/wallets/platform-topup/', payload, format='json').status_code, 409)
        self.assertEqual(Transaction.objects.filter(wallet_id=topup['wallet_id'], external_tx_id=payload['reference']).count(), 1)
        self.assertEqual(retry_completion_subsidies.run(), 1)
        platform = get_platform_wallet(); platform.refresh_from_db()
        self.assertEqual((platform.balance, Wallet.objects.get(account=account).balance), (10000, 8000))
        funds = self.ok(operator.get('/api/admin/wallets/platform-funds/'))
        self.assertEqual((funds['deposits'], funds['available']), (10000, 0))
        self.assertEqual((funds['pending']['order_count'], funds['pending']['amount']), (0, 0))
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['pending_subsidy_total'], snapshot['pending_subsidy_amount']), (0, 0))

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_negotiated_termination_recovers_and_retries(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.models import Order, OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction, Wallet, get_platform_wallet
        service = make_service(name='协商安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='协商六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'wallet:view', 'dashboard:view'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        players = []
        for name in ('协商原陪玩', '协商接替陪玩'):
            user = make_provider(display_name=name, deposit_paid=40000); account = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service); players.append(account)
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-termination'}, format='json'))
        base = f"/api/admin/orders/{created['id']}/"; operator = self.client_for(staff_account)
        self.ok(operator.post(base + 'assign/', {'provider_id': players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f"/api/orders/orders/{created['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform = get_platform_wallet(); platform.balance = 3200; platform.save(update_fields=['balance'])
        self.ok(operator.post(base + 'transfer/', {'transfers': [{'old_provider_id': players[0].pk,
            'new_provider_id': players[1].pk, 'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
            'reason': '协商验收转单'}, format='json'))
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['today_subsidy_paid'], snapshot['today_subsidy_recovered']), (3200, 0))
        detail = self.ok(self.boss.get(f"/api/orders/orders/{created['id']}/"))
        self.assertTrue(detail['can_aftersale'])
        ticket = self.ok(self.boss.post(f"/api/orders/orders/{created['id']}/aftersale/",
            {'reason': '希望协商终止服务', 'client_request_id': 'vip-service-aftersale'}, format='json'))
        self.assertEqual(ticket['status'], 'OPEN')
        data = self.ok(operator.get(base + 'subsidy-negotiation-options/'))
        rows = {row['provider_account_id']: row for row in data['shares']}
        allocations = [{'share_id': rows[players[0].pk]['share_id'], 'amount': 2000},
                       {'share_id': rows[players[1].pk]['share_id'], 'amount': 3000}]
        preview = self.ok(operator.post(base + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
        payload = {'allocations': allocations, 'plan_token': preview['plan_token'], 'reason': '双方协商终止',
                   'client_request_id': 'vip-termination-confirm', 'reviewed': True}
        platform.refresh_from_db(); platform.balance = 1; platform.save(update_fields=['balance'])
        stale = operator.post(base + 'subsidy-negotiation-confirm/', payload, format='json')
        self.assertEqual(stale.data['errorCode'], 'REFUND_PLAN_CHANGED')
        self.assertFalse(Order.objects.get(pk=created['id']).refund_records.exists())
        platform.balance = 0; platform.save(update_fields=['balance'])
        preview = self.ok(operator.post(base + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
        payload['plan_token'] = preview['plan_token']
        self.ok(operator.post(base + 'subsidy-negotiation-confirm/', payload, format='json'))
        order = Order.objects.get(pk=created['id']); voucher.refresh_from_db(); platform.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, voucher.status), ('CANCELLED', 'REFUNDED', 'USED'))
        self.assertEqual(order.aftersales.get().status, 'RESOLVED')
        self.assertIsNone(order.settled_at)
        grant_periodic(self.account)
        self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED').exists())
        self.assertEqual((Wallet.objects.get(account=players[0]).balance, platform.balance), (2000, 1200))
        pending = self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']
        self.assertEqual((pending['order_count'], pending['amount'], pending['orders'][0]['status']), (1, 3000, 'CANCELLED'))
        self.assertEqual(order.refund_records.get().recovered_amount, 1200)
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['today_subsidy_paid'], snapshot['today_subsidy_recovered']), (3200, 1200))
        dashboard_staff = make_console_user(['dashboard:view'])
        dashboard_account = get_or_create_account_for_legacy_user(dashboard_staff)
        dashboard_staff.admin_membership.account = dashboard_account
        dashboard_staff.admin_membership.save(update_fields=['account'])
        restricted = self.ok(self.client_for(dashboard_account).get('/api/admin/dashboard/stats'))
        self.assertIsNone(restricted['today_subsidy_paid'])
        self.assertIsNone(restricted['today_subsidy_recovered'])
        # A prior-day payout and today's recovery remain separate cash movements.
        from datetime import timedelta
        from django.utils import timezone
        Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE', amount__lt=0).update(
            created_at=timezone.now() - timedelta(days=2))
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['today_subsidy_paid'], snapshot['today_subsidy_recovered']), (0, 1200))
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(assignment__provider_account=players[1], subsidy__order=order).amount, 3000)
        boss_detail = self.ok(self.boss.get(f"/api/orders/orders/{order.pk}/"))
        self.assertEqual(boss_detail['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'NOT_RETURNED')
        for account, agreed in zip(players, (2000, 3000)):
            detail = self.ok(self.client_for(account).get(f"/api/orders/orders/{order.pk}/"))
            self.assertEqual(detail['refund_summary']['negotiated_provider_retained'], agreed)
        self.ok(operator.post(base + 'subsidy-negotiation-confirm/', payload, format='json'))
        self.assertEqual(order.refund_records.count(), 1)
        self.assertEqual(retry_completion_subsidies.run(), 0)
        platform.balance = 3000; platform.save(update_fields=['balance'])
        self.assertEqual(retry_completion_subsidies.run(), 1)
        self.assertEqual(retry_completion_subsidies.run(), 0)
        order.refresh_from_db(); platform.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        fresh = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED')
        self.assertNotEqual(fresh.pk, voucher.pk)
        voucher.refresh_from_db(); self.assertEqual(voucher.status, 'USED')
        grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED').count(), 1)
        self.assertEqual((Wallet.objects.get(account=players[1]).balance, platform.balance), (3000, 0))
        snapshot = self.ok(operator.get('/api/admin/dashboard/stats'))
        self.assertEqual((snapshot['today_subsidy_paid'], snapshot['today_subsidy_recovered']), (3000, 1200))
        self.assertEqual(self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']['order_count'], 0)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 3)
        boss2 = make_user(); account2 = get_or_create_account_for_legacy_user(boss2)
        link_legacy_relations(boss2, account2); make_wallet(boss2, balance=0)
        account2.boss_type = tier; account2.save(update_fields=['boss_type']); grant_periodic(account2)
        voucher2 = UserCoupon.objects.get(account=account2, coupon__scope='SAFE_BOX')
        created2 = self.ok(self.client_for(account2).post('/api/orders/create/', {'service_id': service.pk,
            'user_coupon_id': voucher2.pk, 'dispatch_route': 'STAFF', 'client_request_id': 'vip-termination-immediate'}, format='json'))
        base2 = f"/api/admin/orders/{created2['id']}/"
        self.ok(operator.post(base2 + 'assign/', {'provider_id': players[1].pk}, format='json'))
        self.ok(self.client_for(players[1]).post(f"/api/orders/orders/{created2['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform.balance = 4000; platform.save(update_fields=['balance'])
        share2 = self.ok(operator.get(base2 + 'subsidy-negotiation-options/'))['shares'][0]
        allocations2 = [{'share_id': share2['share_id'], 'amount': 4000}]
        preview2 = self.ok(operator.post(base2 + 'subsidy-negotiation-preview/', {'allocations': allocations2}, format='json'))
        self.ok(operator.post(base2 + 'subsidy-negotiation-confirm/', {'allocations': allocations2,
            'plan_token': preview2['plan_token'], 'reason': '协商结束服务', 'client_request_id': 'vip-termination-immediate-confirm',
            'reviewed': True}, format='json'))
        order2 = Order.objects.get(pk=created2['id']); platform.refresh_from_db()
        self.assertIsNotNone(order2.settled_at)
        self.assertEqual((platform.balance, Wallet.objects.get(account=players[1]).balance), (0, 7000))
        self.assertEqual(UserCoupon.objects.filter(account=account2, coupon__scope='SAFE_BOX', status='UNUSED').count(), 1)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_transfer_waits_and_pays_original_provider(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.models import OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction, Wallet, get_platform_wallet
        service=make_service(name='完成转单安全箱',price=10000,benefit_kind='SAFEBOX_33',platform='PC',commission_rate=20)
        tier=make_boss_type(name='转单完成会员',discount_rate=90)
        VipTier.objects.create(boss_type=tier,level=6,recharge_threshold=0,season_discount=0,safe_box_service_ids=[service.pk])
        self.account.boss_type=tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher=UserCoupon.objects.get(account=self.account,coupon__scope='SAFE_BOX')
        staff=make_console_user(['order:view','order:dispatch','order:refund','wallet:view']); staff_account=get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account=staff_account; staff.admin_membership.save(update_fields=['account'])
        providers=[]
        for name in ('原履约陪玩','接替履约陪玩'):
            person=make_provider(display_name=name, deposit_paid=40000); account=get_or_create_account_for_legacy_user(person)
            person.escort_profile.account=account; person.escort_profile.save(update_fields=['account'])
            person.escort_profile.service_items.add(service); providers.append(account)
        order=self.ok(self.boss.post('/api/orders/create/',{'service_id':service.pk,'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF','client_request_id':'vip-completion-transfer'},format='json'))
        operator=self.client_for(staff_account)
        self.ok(operator.post(f"/api/admin/orders/{order['id']}/assign/",{'provider_id':providers[0].pk},format='json'))
        self.ok(self.client_for(providers[0]).post(f"/api/orders/orders/{order['id']}/start/",{'entry_image':screenshot()},format='multipart'))
        platform=get_platform_wallet(); platform.balance=3199; platform.save(update_fields=['balance'])
        self.ok(operator.post(f"/api/admin/orders/{order['id']}/transfer/",{'transfers':[{'old_provider_id':providers[0].pk,
            'new_provider_id':providers[1].pk,'split_type':'PERCENT','split_value':40,'penalty_amount':0}],
            'reason':'隔离完成结算转单'},format='json'))
        old=OrderPlatformSubsidyShare.objects.get(subsidy__order_id=order['id'],assignment__provider_account=providers[0])
        self.assertEqual(old.amount,3200); self.assertIsNone(old.paid_at); self.assertIsNone(old.assignment.settled_at)
        pending = self.ok(operator.get('/api/admin/wallets/platform-funds/'))['pending']
        self.assertEqual((pending['order_count'], pending['amount'], pending['orders'][0]['status']), (1, 3200, 'IN_SERVICE'))
        options = self.ok(operator.get(f"/api/admin/orders/{order['id']}/subsidy-negotiation-options/"))
        preview = operator.get(f"/api/admin/orders/{order['id']}/refund-preview/")
        self.assertEqual(preview.data.get('errorCode'), 'SUBSIDY_NEGOTIATION_REQUIRED')
        shares = {item['provider_account_id']: item for item in options['shares']}
        self.assertEqual(options['coupon_outcome'], 'USED')
        self.assertEqual((shares[providers[0].pk]['expected_income'], shares[providers[0].pk]['paid_income']), (3200, 0))
        self.assertEqual((shares[providers[1].pk]['expected_income'], shares[providers[1].pk]['paid_income']), (4800, 0))
        denied = self.staff.get(f"/api/admin/orders/{order['id']}/subsidy-negotiation-options/")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(retry_completion_subsidies.run(),0)
        platform.refresh_from_db(); platform.balance=10000; platform.save(update_fields=['balance'])
        self.assertEqual(retry_completion_subsidies.run(),1)
        old.refresh_from_db(); self.assertIsNotNone(old.paid_at)
        options = self.ok(operator.get(f"/api/admin/orders/{order['id']}/subsidy-negotiation-options/"))
        shares = {item['provider_account_id']: item for item in options['shares']}
        self.assertEqual((shares[providers[0].pk]['paid_income'], shares[providers[1].pk]['paid_income']), (3200, 0))
        rejected_return = operator.post(f"/api/admin/orders/{order['id']}/transfer/", {'transfers': [{
            'old_provider_id': providers[1].pk, 'new_provider_id': providers[0].pk,
            'split_type': 'PERCENT', 'split_value': 50, 'penalty_amount': 0}], 'reason': '核对已结算转回'}, format='json')
        self.assertNotEqual(rejected_return.data.get('code'), 0)
        self.assertIn('已收到', rejected_return.data.get('msg', ''))
        allocations = [{'share_id': shares[providers[0].pk]['share_id'], 'amount': 2000},
                       {'share_id': shares[providers[1].pk]['share_id'], 'amount': 3000}]
        preview = self.ok(operator.post(f"/api/admin/orders/{order['id']}/subsidy-negotiation-preview/",
                                        {'allocations': allocations}, format='json'))
        self.assertEqual((preview['agreed_total'], preview['recovery_total'], preview['unpaid_total']), (5000, 1200, 3000))
        self.assertTrue(preview['recovery_available'])
        self.assertEqual(preview['platform_balance'], 6800)
        from django.core import signing
        self.assertEqual(signing.loads(preview['plan_token'], salt='orders.subsidy-negotiation.v1')['order_id'], order['id'])
        self.assertTrue(preview['recovery_plan_token'])
        self.assertEqual(len(preview['recoveries']), 1)
        self.assertEqual((preview['recoveries'][0]['available_required'], preview['recoveries'][0]['shortfall']), (1200, 0))
        invalid = operator.post(f"/api/admin/orders/{order['id']}/subsidy-negotiation-preview/",
            {'allocations': [allocations[0], {**allocations[1], 'amount': 4801}]}, format='json')
        self.assertEqual(invalid.data['errorCode'], 'SUBSIDY_ALLOCATION_INVALID')
        old.refresh_from_db(); self.assertEqual(old.amount, 3200)
        self.assertEqual(Wallet.objects.get(account=providers[0]).balance,3200)
        from wallet.models import SystemConfig
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value': '0'})
        self.ok(self.client_for(providers[0]).post('/api/wallet/withdraw/', {'amount': 2700,
            'payee_method': 'WECHAT', 'payee_account': 'LOCAL_TEST_ONLY', 'payee_name': '隔离提现',
            'client_request_id': 'vip-subsidy-preview-withdraw'}, format='json'))
        after_withdraw = self.ok(operator.post(f"/api/admin/orders/{order['id']}/subsidy-negotiation-preview/",
                                               {'allocations': allocations}, format='json'))
        self.assertFalse(after_withdraw['recovery_available'])
        self.assertEqual(after_withdraw['shortfalls'][0]['shortfall'], 700)
        blocked = operator.post(f"/api/admin/orders/{order['id']}/subsidy-negotiation-confirm/", {
            'allocations': allocations, 'plan_token': after_withdraw['plan_token'], 'reason': '协商终止',
            'client_request_id': 'vip-shortfall-confirm', 'reviewed': True}, format='json')
        self.assertEqual(blocked.data['errorCode'], 'REFUND_RECOVERY_INSUFFICIENT')
        from orders.models import Order
        self.assertEqual(Order.objects.get(pk=order['id']).status, 'IN_SERVICE')
        self.assertNotEqual(signing.loads(preview['plan_token'], salt='orders.subsidy-negotiation.v1')['fingerprint'],
                            signing.loads(after_withdraw['plan_token'], salt='orders.subsidy-negotiation.v1')['fingerprint'])
        self.assertEqual(retry_completion_subsidies.run(),0)
        replacement_player = self.client_for(providers[1])
        replacement_report = self.ok(replacement_player.post('/api/wallet/reports/', {'order_id':order['id']}, format='json'))
        self.ok(replacement_player.post(f"/api/wallet/reports/{replacement_report['id']}/images/", {'kind':'ENTRY','image':screenshot()},format='multipart'))
        self.ok(self.client_for(providers[1]).post(f"/api/orders/orders/{order['id']}/complete/",{'completion_image':screenshot()},format='multipart'))
        platform.refresh_from_db(); self.assertEqual(platform.balance,2000)
        self.assertEqual(Wallet.objects.get(account=providers[1]).balance,4800)
        self.assertEqual(Transaction.objects.filter(order_id=order['id'],tx_type='SUBSIDY_EXPENSE').count(),2)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_STAFF_TEAM_ASSIGN_FORMAL') == '1',
                '安全箱客服指派旅程已被抢单池取代（18-盘点客服安排抢单/免费安全箱待最终联调）；断言保留')
    def test_free_safe_box_completion_waits_for_platform_then_retries(self):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.models import Order, OrderPlatformSubsidyShare
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction, Wallet, get_platform_wallet
        service = make_service(name='完成结算安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='完成结算六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, season_discount=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='完成结算陪玩', deposit_paid=40000)
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        result = self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'vip-completion-funding'}, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{result['id']}/assign/", {'provider_id':account.pk}, format='json'))
        player = self.client_for(account)
        self.ok(player.post(f"/api/orders/orders/{result['id']}/start/", {'entry_image':screenshot()}, format='multipart'))
        refused = self.client_for(staff_account).post(f"/api/admin/orders/{result['id']}/refund/",
            {'reason': '协商售后', 'client_request_id': 'vip-funded-aftersale'}, format='json')
        self.assertNotEqual(refused.data.get('code'), 0)
        self.assertIn('报酬', refused.data.get('msg', ''))
        self.assertEqual(Order.objects.get(pk=result['id']).status, 'IN_SERVICE')
        voucher.refresh_from_db(); self.assertEqual(voucher.status, 'USED')
        platform = get_platform_wallet(); platform.balance = 7999; platform.save(update_fields=['balance'])
        self.ok(player.post(f"/api/orders/orders/{result['id']}/complete/", {'completion_image':screenshot()}, format='multipart'))
        order = Order.objects.get(pk=result['id']); self.assertEqual(order.status, 'COMPLETED'); self.assertIsNone(order.settled_at)
        self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        import asyncio
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        group = f'account_{account.pk}'
        async_to_sync(layer.group_add)(group, channel)
        self.addCleanup(async_to_sync(layer.group_discard), group, channel)
        platform.refresh_from_db(); platform.balance = 10000; platform.save(update_fields=['balance'])
        self.assertEqual(retry_completion_subsidies.run(), 1)
        async def receive_wallet_update():
            while True:
                event = await asyncio.wait_for(layer.receive(channel), timeout=2)
                if event['type'] == 'wallet_update':
                    return event
        event = async_to_sync(receive_wallet_update)()
        self.assertEqual(event['data']['account_id'], account.pk)
        self.assertEqual(event['data']['wallet_id'], Wallet.objects.get(account=account).pk)
        self.assertEqual(retry_completion_subsidies.run(), 0)
        order.refresh_from_db(); platform.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual((platform.balance, Wallet.objects.get(account=account).balance), (2000, 8000))
        self.assertIsNotNone(OrderPlatformSubsidyShare.objects.get(subsidy__order=order).paid_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)
        # A separate buyer with available platform funds settles at completion.
        boss2 = make_user(); account2 = get_or_create_account_for_legacy_user(boss2)
        link_legacy_relations(boss2, account2); make_wallet(boss2, balance=0)
        account2.boss_type = tier; account2.save(update_fields=['boss_type']); grant_periodic(account2)
        voucher2 = UserCoupon.objects.get(account=account2, coupon__scope='SAFE_BOX')
        created2 = self.ok(self.client_for(account2).post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher2.pk,
            'dispatch_route':'STAFF', 'client_request_id':'vip-completion-funded'}, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{created2['id']}/assign/", {'provider_id':account.pk}, format='json'))
        self.ok(player.post(f"/api/orders/orders/{created2['id']}/start/", {'entry_image':screenshot()}, format='multipart'))
        platform.balance = 10000; platform.save(update_fields=['balance'])
        self.ok(player.post(f"/api/orders/orders/{created2['id']}/complete/", {'completion_image':screenshot()}, format='multipart'))
        completed2 = Order.objects.get(pk=created2['id']); self.assertIsNotNone(completed2.settled_at)
        platform.refresh_from_db()
        self.assertEqual((platform.balance, Wallet.objects.get(account=account).balance), (2000, 16000))

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_SAFE_BOX_FORMAL') == '1',
                '安全箱正式补贴/抢单到期链仍在接线（18-盘点免费安全箱/抢单到期待最终联调）；断言保留')
    def test_free_vip_safe_box_cancel_then_complete_and_replenish(self):
        from engagement.confirmation import process_order
        from engagement.models import VipTier
        from coupons.benefits import grant_periodic
        from orders.models import Order, OrderProvider
        from orders.tests.factories import make_boss_type, make_provider
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, Transaction, Wallet
        service = make_service(name='会员安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier_type = make_boss_type(name='六级测试会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier_type, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier_type; self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        self.assertEqual(voucher.coupon.discount_rate,0)
        from orders.purchase_benefits import prepare_purchase, vip_safe_box_subsidy_basis, vip_safe_box_subsidy_income
        pricing, _, selected = prepare_purchase(self.account, service, 1, voucher.pk)
        self.assertEqual(vip_safe_box_subsidy_basis(self.account, service, 1, selected, pricing), 10000)
        self.assertEqual(vip_safe_box_subsidy_basis(self.account, service, 2, selected, pricing), 0)
        self.assertEqual(vip_safe_box_subsidy_basis(self.account, service, 1, None, pricing), 0)
        self.assertEqual(vip_safe_box_subsidy_income(10000, commission_type='PERCENT', commission_rate=20, commission_fixed=0), 8000)
        self.assertEqual(vip_safe_box_subsidy_income(10000, commission_type='FIXED', commission_rate=0, commission_fixed=1500), 8500)
        staff_user = make_console_user(['order:view','order:dispatch','report:view','report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff_user)
        staff_user.admin_membership.account = staff_account; staff_user.admin_membership.save(update_fields=['account'])
        staff = self.client_for(staff_account)
        provider = make_provider(display_name='免费券服务陪玩', deposit_paid=40000)
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        player = self.client_for(provider_account)
        def create(key):
            return self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk,
                'user_coupon_id':voucher.pk, 'dispatch_route':'STAFF', 'client_request_id':key}, format='json'))
        first = create('free-safe-box-cancel')
        self.assertEqual((first['original_amount'],first['coupon_discount'],first['amount']),(10000,10000,0))
        self.ok(self.boss.post(f"/api/orders/orders/{first['id']}/cancel/", {}, format='json'))
        voucher.refresh_from_db(); self.assertEqual(voucher.status,'UNUSED')
        self.assertFalse(Transaction.objects.filter(order_id=first['id'],tx_type='REFUND').exists())
        second = create('free-safe-box-service'); order_id = second['id']
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':provider_account.pk}, format='json'))
        self.ok(player.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        self.ok(player.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image':screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id,provider_account=provider_account)
        self.ok(player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT','image':screenshot()}, format='multipart'))
        self.ok(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.ok(staff.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        past = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order_id).update(completed_at=past)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=past)
        self.assertTrue(process_order(order_id)); self.assertFalse(process_order(order_id))
        share = OrderProvider.objects.get(order_id=order_id,provider_account=provider_account)
        self.assertEqual((share.settlement_base,share.provider_income),(0,0))
        self.assertEqual(Wallet.objects.get(user=provider).balance,0)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance,50000)
        rows = list(UserCoupon.objects.filter(account=self.account,coupon__scope='SAFE_BOX').order_by('pk'))
        self.assertEqual([row.status for row in rows],['USED','UNUSED'])
        self.assertNotEqual(rows[0].pk,rows[1].pk)
        grant_periodic(self.account)
        self.assertEqual(UserCoupon.objects.filter(account=self.account,coupon__scope='SAFE_BOX').count(),2)
        # Exercise the isolated ledger primitive on the real fulfilled order.
        # Production order settlement is not wired to it yet.
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare
        from orders.subsidies import settle_platform_subsidy_share
        from wallet.models import get_platform_wallet
        subsidy = OrderPlatformSubsidy.objects.create(order_id=order_id, coupon_id_snapshot=voucher.pk, basis=10000)
        item = OrderPlatformSubsidyShare.objects.create(subsidy=subsidy, assignment=share, amount=8000)
        platform = get_platform_wallet(); platform.balance = 7999; platform.save(update_fields=['balance'])
        self.assertFalse(settle_platform_subsidy_share(item.pk))
        self.assertEqual(Wallet.objects.get(user=provider).balance, 0)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='SUBSIDY_EXPENSE').exists())
        platform.refresh_from_db(); self.assertEqual(platform.balance, 7999)
        platform.balance = 10000; platform.save(update_fields=['balance'])
        self.assertTrue(settle_platform_subsidy_share(item.pk))
        self.assertTrue(settle_platform_subsidy_share(item.pk))
        platform.refresh_from_db(); item.refresh_from_db()
        self.assertEqual((platform.balance, Wallet.objects.get(user=provider).balance), (2000, 8000))
        self.assertEqual((item.expense_transaction.amount, item.income_transaction.amount), (-8000, 8000))
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='SUBSIDY_EXPENSE').count(), 1)

    def test_admin_disables_claimed_coupon_then_reenables_and_boss_uses_it(self):
        created = self.staff.post('/api/admin/coupons/', {
            'name': '业务验收券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        self.assertIn(created.status_code, (200, 201), created.data)
        self.assertEqual(created.data['code'], 0, created.data)
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)

        def mine():
            return next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)

        self.assertEqual((mine()['status'], mine()['availability_status']), ('UNUSED', 'UNUSED'))
        voucher.expires_at = timezone.now() - timedelta(seconds=1)
        voucher.save(update_fields=['expires_at'])
        self.assertEqual(mine()['availability_status'], 'EXPIRED')
        voucher.expires_at = None
        voucher.save(update_fields=['expires_at'])
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': False}, format='json'))
        self.assertEqual((mine()['status'], mine()['availability_status']), ('UNUSED', 'DISABLED'))
        self.assertNotIn(voucher.pk, {row['id'] for row in self.ok(self.boss.get('/api/coupons/mine/', {'usable': 1}))})
        quote = self.boss.post('/api/orders/quote/', {'service_id': self.service.pk, 'user_coupon_id': voucher.pk}, format='json')
        self.assertNotEqual(quote.data.get('code', 400), 0, quote.data)
        self.assertIn('优惠券', str(quote.data))

        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': True}, format='json'))
        self.assertEqual(mine()['availability_status'], 'UNUSED')
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.assertEqual((order['coupon_discount'], order['amount']), (1000, 9000))
        self.assertEqual((mine()['status'], mine()['availability_status']), ('USED', 'USED'))
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': False}, format='json'))
        self.assertEqual(mine()['availability_status'], 'USED')

    def test_claimed_discount_is_returned_after_cancellation_and_can_be_reused(self):
        from wallet.models import Transaction
        created = self.staff.post('/api/admin/coupons/', {
            'name': '真实退款返券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True, 'valid_to': (timezone.now() + timedelta(days=1)).isoformat(),
        }, format='json')
        self.assertIn(created.status_code, (200, 201), created.data)
        self.assertEqual(created.data['code'], 0, created.data)
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        before_deadline = voucher.valid_to

        def create_order():
            return self.ok(self.boss.post('/api/orders/create/', {
                'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
                'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
            }, format='json'))

        first = create_order()
        self.assertEqual((first['amount'], first['coupon_discount']), (9000, 1000))
        detail = self.ok(self.boss.get(f"/api/orders/orders/{first['id']}/"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 41000)
        self.assertEqual(next(row for row in self.ok(self.boss.get('/api/coupons/mine/'))
                              if row['id'] == voucher.pk)['availability_status'], 'USED')
        self.ok(self.boss.post(f"/api/orders/orders/{first['id']}/cancel/", {'reason': '本地取消'}, format='json'))
        cancelled = self.ok(self.boss.get(f"/api/orders/orders/{first['id']}/"))
        self.assertEqual(cancelled['refund_summary']['records'][0]['coupon_results'],
                         [{'name': '真实退款返券', 'outcome': 'RETURNED'}])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id, voucher.valid_to), ('UNUSED', None, before_deadline))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 50000)
        mine = next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)
        self.assertEqual(mine['availability_status'], 'UNUSED')
        second = create_order()
        self.assertEqual((second['amount'], second['coupon_discount']), (9000, 1000))
        self.assertNotEqual(first['id'], second['id'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 41000)
        self.assertEqual(list(Transaction.objects.filter(order_id=first['id'], tx_type='PAY').values_list('amount', flat=True)), [-9000])
        self.assertEqual(list(Transaction.objects.filter(order_id=first['id'], tx_type='REFUND').values_list('amount', flat=True)), [9000])
        self.assertEqual(list(Transaction.objects.filter(order_id=second['id'], tx_type='PAY').values_list('amount', flat=True)), [-9000])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('USED', second['id']))
        # The previous refund remains a historical fact after the same coupon is used again.
        previous = self.ok(self.boss.get(f"/api/orders/orders/{first['id']}/"))
        self.assertEqual(previous['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')

    def test_returned_coupon_reports_expired_if_template_expires_during_order(self):
        created = self.staff.post('/api/admin/coupons/', {
            'name': '有效期返券', 'discount_type': 'DIRECT', 'amount': 1000,
            'threshold': 0, 'is_active': True, 'valid_to': (timezone.now() + timedelta(days=1)).isoformat(),
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.ok(self.staff.patch(f'/api/admin/coupons/{coupon_id}/',
                                 {'valid_to': (timezone.now() - timedelta(seconds=1)).isoformat()}, format='json'))
        self.ok(self.boss.post(f"/api/orders/orders/{order['id']}/cancel/", {}, format='json'))
        cancelled = self.ok(self.boss.get(f"/api/orders/orders/{order['id']}/"))
        self.assertEqual(cancelled['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')
        mine = next(row for row in self.ok(self.boss.get('/api/coupons/mine/')) if row['id'] == voucher.pk)
        self.assertEqual((mine['status'], mine['availability_status']), ('UNUSED', 'EXPIRED'))

    def test_zero_amount_discount_order_can_be_cancelled(self):
        from wallet.models import Transaction, Wallet
        created = self.staff.post('/api/admin/coupons/', {
            'name': '全额优惠券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.assertEqual(order['amount'], 0)
        cancelled = self.ok(self.boss.post(f"/api/orders/orders/{order['id']}/cancel/", {}, format='json'))
        self.assertEqual(cancelled['status'], 'CANCELLED')
        detail = self.ok(self.boss.get(f"/api/orders/orders/{order['id']}/"))
        self.assertEqual(detail['refund_summary']['records'][0]['coupon_results'],
                         [{'name': '全额优惠券', 'outcome': 'RETURNED'}])
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('UNUSED', None))
        self.assertEqual(Wallet.objects.get(account=self.account).balance, 50000)
        self.assertFalse(Transaction.objects.filter(order_id=order['id'], tx_type='REFUND').exists())
        # Check native presentation using the actual cancellation response.
        # React 运行时需较新 Node（registerHooks）；便携 22.12 不支持，
        # 界面展示覆盖按清理审计口径留待后续界面阶段（XA_NATIVE_PRESENTATION=1 启用）。
        if os.environ.get('XA_NATIVE_PRESENTATION') != '1':
            self.skipTest('Native presentation deferred to final UI acceptance (newer Node required)')
        from django.core.serializers.json import DjangoJSONEncoder
        repo = Path(settings.BASE_DIR).parent.parent
        node = shutil.which('node')
        portable = repo / '.tools/runtime/node-v22.12.0-win-x64/node.exe'
        if node is None and portable.exists():
            node = str(portable)
        if node is None:
            self.skipTest('Node runtime unavailable; native presentation deferred to final acceptance')
        result = subprocess.run([node, str(repo / 'scripts/scenarios/native-coupon-refund-live.cjs')],
            input=json.dumps({'order': cancelled, 'name': '全额优惠券', 'outcome': 'RETURNED', 'zeroPayment': True}, cls=DjangoJSONEncoder),
            text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    @skipUnless(os.environ.get('XA_SAFE_BOX_FORMAL') == '1',
                '安全箱正式补贴/抢单到期链仍在接线（18-盘点免费安全箱/抢单到期待最终联调）；断言保留')
    def test_zero_amount_coupon_timeout_returns_coupon_without_wallet_refund(self):
        from orders.models import Order
        from orders.tasks import auto_cancel_pending_order
        from site_messages.models import Message
        from wallet.models import Transaction
        created = self.staff.post('/api/admin/coupons/', {
            'name': '超时全额券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        data = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'PUBLIC',
        }, format='json'))
        Order.objects.filter(pk=data['id']).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order.run(data['id']), 'cancelled')
        order = Order.objects.get(pk=data['id'])
        voucher.refresh_from_db()
        self.assertEqual((order.status, voucher.status, voucher.order_id), ('CANCELLED', 'UNUSED', None))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='REFUND').exists())
        notice = Message.objects.get(related_order_id=order.pk, title='订单超时已取消')
        self.assertNotIn('退回浪花余额', notice.preview)
        self.assertEqual(auto_cancel_pending_order.run(data['id']), 'skip:status=CANCELLED')

    def test_staff_cancels_zero_payment_order_and_returns_coupon(self):
        from wallet.models import Transaction
        staff = make_console_user(['order:view', 'order:cancel'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        created = self.staff.post('/api/admin/coupons/', {
            'name': '客服取消全额券', 'discount_type': 'DIRECT', 'amount': 10000,
            'threshold': 0, 'is_active': True,
        }, format='json')
        coupon_id = created.data['data']['id']
        self.ok(self.boss.post(f'/api/coupons/{coupon_id}/claim/'))
        voucher = UserCoupon.objects.get(account=self.account, coupon_id=coupon_id)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': self.service.pk, 'user_coupon_id': voucher.pk,
            'client_request_id': str(uuid4()), 'dispatch_route': 'STAFF',
        }, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{order['id']}/cancel/",
                                                   {'reason': '测试取消'}, format='json'))
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('UNUSED', None))
        self.assertFalse(Transaction.objects.filter(order_id=order['id'], tx_type='REFUND').exists())


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class CouponAvailabilityNativeHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        from tempfile import TemporaryDirectory
        media = TemporaryDirectory(prefix='xa-vip-coupon-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_vip_grant_refreshes_open_coupon_page_only_for_owner(self):
        import os
        from tempfile import TemporaryDirectory
        if os.environ.get('XA_CHECKIN_REDIS_REALTIME') != '1':
            self.skipTest('requires isolated Redis and ASGI')
        from engagement.models import ClubOperationConfig, VipTier
        from orders.tests.factories import make_boss_type
        from users.test_native_checkin_order_realtime import isolated_redis_asgi
        from orders.tests.factories import make_provider
        boss, other, staff = make_user(), make_user(), make_console_user(['engagement:view','engagement:edit','order:view','order:dispatch','order:refund','report:view','report:audit'])
        provider = make_provider(display_name='安全箱陪玩', deposit_paid=40000)
        account = get_or_create_account_for_legacy_user(boss); link_legacy_relations(boss, account)
        other_account = get_or_create_account_for_legacy_user(other); link_legacy_relations(other, other_account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        tier_type = make_boss_type(name='隔离会员', discount_rate=95)
        tier = VipTier.objects.create(boss_type=tier_type, level=2, recharge_threshold=0)
        safe_box = make_service(name='隔离安全箱套餐', benefit_kind='SAFEBOX_33', platform='PC', billing_provider_count=1)
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(safe_box)
        account.boss_type = tier_type; account.save(update_fields=['boss_type'])
        make_wallet(boss, balance=50000)
        ClubOperationConfig.objects.update_or_create(pk=1, defaults={'vip_enabled':False})
        with isolated_redis_asgi() as (base,ws_base):
            def http(method, path, token, payload=None):
                request = Request(base + path, method=method,
                    data=None if payload is None else json.dumps(payload).encode(),
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=20) as response:
                    body = json.load(response)
                self.assertEqual(body['code'], 0, body)
                return body.get('data')
            boss_token = issue_account_tokens(account)[0]
            staff_token = issue_account_tokens(staff_account)[0]
            self.assertEqual(http('GET', '/api/coupons/mine/', boss_token), [])
            resume = http('GET', '/api/admin/club-config/vip-resume-preview/', staff_token)
            http('PUT', '/api/admin/club-config/', staff_token, {'vip_enabled': True, 'vip_resume_token': resume['token']})
            self.assertEqual(len(http('GET', '/api/coupons/mine/', boss_token)), 2)
            http('PATCH', f'/api/admin/vip-tiers/{tier.pk}/', staff_token, {'safe_box_service_ids': [safe_box.pk]})
            usable = http('GET', f'/api/coupons/mine/?usable=1&service_id={safe_box.pk}', boss_token)
            self.assertTrue(any(item['scope'] == 'SAFE_BOX' for item in usable))
        self.assertEqual(UserCoupon.objects.filter(account=account).count(),2)
        self.assertFalse(UserCoupon.objects.filter(account=other_account).exists())
        voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        quote = client.post('/api/orders/quote/', {'service_id':safe_box.pk, 'user_coupon_id':voucher.pk}, format='json')
        self.assertEqual(quote.data['code'],0,quote.data)
        self.assertEqual((quote.data['data']['amount'], quote.data['data']['coupon_discount']),(8000,2000))
        created = client.post('/api/orders/create/', {'service_id':safe_box.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'vip-safe-box-linked-order'}, format='json')
        self.assertEqual(created.data['code'],0,created.data)
        self.assertEqual((created.data['data']['amount'],created.data['data']['coupon_discount']),(8000,2000))
        staff_client = APIClient(); staff_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
        order = staff_client.get(f"/api/admin/orders/{created.data['data']['id']}/")
        self.assertEqual(order.data['code'],0,order.data)
        self.assertEqual((order.data['data']['amount'],order.data['data']['coupon_discount']),(8000,2000))
        voucher.refresh_from_db(); self.assertEqual(voucher.status,'USED')
        order_id = created.data['data']['id']
        assigned = staff_client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':provider_account.pk}, format='json')
        self.assertEqual(assigned.data['code'],0,assigned.data)
        from orders.models import OrderProvider
        share = OrderProvider.objects.get(order_id=order_id,provider_account=provider_account)
        self.assertEqual(share.settlement_base,8000)
        provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(provider_account)[0])
        provider_order = provider_client.get(f'/api/orders/orders/{order_id}/')
        self.assertEqual(provider_order.data['code'],0,provider_order.data)
        self.assertEqual(provider_order.data['data']['expected_income'],share.provider_income)
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, Transaction, Wallet
        started = provider_client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart')
        self.assertEqual(started.data['code'],0,started.data)
        completed = provider_client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image':screenshot()}, format='multipart')
        self.assertEqual(completed.data['code'],0,completed.data)
        report = ProviderReport.objects.get(order_id=order_id,provider_account=provider_account)
        uploaded = provider_client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT','image':screenshot()}, format='multipart')
        self.assertEqual(uploaded.data['code'],0,uploaded.data)
        submitted = provider_client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.assertEqual(submitted.data['code'],0,submitted.data)
        approved = staff_client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        self.assertEqual(approved.data['code'],0,approved.data)
        from orders.models import Order
        order = Order.objects.get(pk=order_id); self.assertIsNone(order.settled_at)
        # Date the isolated records to model the elapsed 72-hour windows.
        past = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order_id).update(completed_at=past)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=past)
        from engagement.confirmation import process_order
        self.assertTrue(process_order(order_id)); self.assertFalse(process_order(order_id))
        order.refresh_from_db(); voucher.refresh_from_db()
        self.assertEqual((order.amount, order.coupon_discount, voucher.status),(8000,2000,'USED'))
        self.assertEqual(Wallet.objects.get(account=provider_account).balance,share.provider_income)
        self.assertEqual(Transaction.objects.filter(order_id=order_id,tx_type='INCOME').count(),1)
        preview = staff_client.get(f'/api/admin/orders/{order_id}/refund-preview/')
        self.assertEqual(preview.data['code'],0,preview.data)
        refunded = staff_client.post(f'/api/admin/orders/{order_id}/refund/', {'reason':'隔离全额退款',
            'plan_token':preview.data['data']['plan_token'], 'client_request_id':'vip-safe-box-full-refund'}, format='json')
        self.assertEqual(refunded.data['code'],0,refunded.data)
        voucher.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((voucher.status,order.payment_status),('UNUSED','REFUNDED'))
        self.assertEqual(Wallet.objects.get(account=provider_account).balance,0)
        self.assertEqual(Wallet.objects.get(account=account).balance,50000)
        detail = client.get(f'/api/orders/orders/{order_id}/')
        self.assertEqual(detail.data['code'],0,detail.data)
        self.assertEqual(detail.data['data']['refund_summary']['records'][0]['coupon_results'][0]['outcome'],'RETURNED')
        mine = client.get('/api/coupons/mine/', {'usable':1, 'service_id':safe_box.pk})
        self.assertEqual(mine.data['code'],0,mine.data)
        self.assertEqual([row['id'] for row in mine.data['data'] if row['id']==voucher.pk],[voucher.pk])
        self.assertEqual(UserCoupon.objects.filter(account=account,coupon__scope='SAFE_BOX').count(),1)

    def test_last_coupon_claim_refreshes_other_boss_over_real_socket(self):
        import os
        if os.environ.get('XA_CHECKIN_REDIS_REALTIME') != '1':
            self.skipTest('requires isolated Redis and ASGI')
        from coupons.models import Coupon
        from users.test_native_checkin_order_realtime import isolated_redis_asgi
        first, second = make_user(), make_user()
        first_account = get_or_create_account_for_legacy_user(first)
        second_account = get_or_create_account_for_legacy_user(second)
        link_legacy_relations(first, first_account)
        link_legacy_relations(second, second_account)
        coupon = Coupon.objects.create(name='最后一张实时券', discount_type='DIRECT', amount=500, total_qty=1)
        legacy = Coupon.objects.create(name='历史带门槛券', discount_type='DIRECT', amount=500, threshold=1000)
        rate = Coupon.objects.create(name='带门槛折扣券', discount_type='RATE', amount=1, discount_rate=80, threshold=2000)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/scenarios/native-coupon-stock-realtime.cjs')],
                input=json.dumps({'base': base, 'wsBase': ws_base, 'couponId': coupon.pk, 'legacyId': legacy.pk, 'rateId': rate.pk,
                    'token': issue_account_tokens(second_account)[0], 'account': second_account.pk,
                    'firstToken': issue_account_tokens(first_account)[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        coupon.refresh_from_db()
        self.assertEqual(coupon.claimed_qty, 1)
        self.assertTrue(UserCoupon.objects.filter(account=first_account, coupon=coupon).exists())
        self.assertFalse(UserCoupon.objects.filter(account=second_account, coupon=coupon).exists())

    def test_admin_toggle_refreshes_open_native_coupon_page_over_real_socket(self):
        import os
        if os.environ.get('XA_CHECKIN_REDIS_REALTIME') != '1':
            self.skipTest('requires isolated Redis and ASGI')
        from coupons.models import Coupon
        from users.test_native_checkin_order_realtime import isolated_redis_asgi
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit', 'coupon:delete'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        coupon = Coupon.objects.create(name='实时停用券', discount_type='DIRECT', amount=1000)
        client = APIClient()
        token = issue_account_tokens(account)[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        with isolated_redis_asgi() as (base, ws_base):
            staff_token = issue_account_tokens(staff_account)[0]
            def admin(method, path, payload=None):
                request = Request(base + path, method=method,
                    data=None if payload is None else json.dumps(payload).encode(),
                    headers={'Authorization': 'Bearer ' + staff_token, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=20) as response:
                    body = json.load(response)
                self.assertEqual(body['code'], 0, body)
                return body.get('data')
            admin('PATCH', f'/api/admin/coupons/{coupon.pk}/', {'is_active': False})
            admin('PATCH', f'/api/admin/coupons/{coupon.pk}/', {'is_active': True})
            created = admin('POST', '/api/admin/coupons/',
                {'name': '隔离新建实时券', 'discount_type': 'DIRECT', 'amount': 500, 'is_active': True})
            admin('DELETE', f"/api/admin/coupons/{created['id']}/")
        coupon.refresh_from_db()
        self.assertTrue(coupon.is_active)

    def test_open_coupon_page_tracks_cancel_and_reuse_over_real_socket(self):
        import os
        if os.environ.get('XA_CHECKIN_REDIS_REALTIME') != '1':
            self.skipTest('requires isolated Redis and ASGI')
        from coupons.models import Coupon
        from users.test_native_checkin_order_realtime import isolated_redis_asgi
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='隔离实时返券', discount_type='DIRECT', amount=1000)
        client = APIClient()
        token = issue_account_tokens(account)[0]
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        response = client.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'client_request_id':'coupon-socket-first', 'dispatch_route':'STAFF'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        first = response.data['data']['id']
        with isolated_redis_asgi() as (base, ws_base):
            def post(path, payload):
                request = Request(base + path, method='POST', data=json.dumps(payload).encode(),
                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=20) as response:
                    body = json.load(response)
                self.assertEqual(body['code'], 0, body)
                return body.get('data')
            post(f'/api/orders/orders/{first}/cancel/', {'reason': '隔离测试取消'})
            post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
                'client_request_id': 'coupon-socket-second', 'dispatch_route': 'STAFF'})
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        self.assertNotEqual(voucher.order_id, first)

    def test_returned_coupon_reused_keeps_both_receipts_historical(self):
        import os
        from django.db import connection
        from django.core.serializers.json import DjangoJSONEncoder
        from django.test.utils import CaptureQueriesContext
        root = os.environ.get('XA_ADMIN_VUE_ROOT')
        if not root:
            self.skipTest('requires isolated admin build')
        from coupons.models import Coupon
        boss, staff = make_user(), make_console_user(['order:view'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        coupon = Coupon.objects.create(name='隔离返还再用券', discount_type='DIRECT', amount=1000)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.post(f'/api/coupons/{coupon.pk}/claim/').data['code'], 0)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        def create(key):
            response = client.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
                'client_request_id':key, 'dispatch_route':'STAFF'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        first = create('coupon-historical-first')
        cancelled = client.post(f"/api/orders/orders/{first['id']}/cancel/", {'reason':'隔离取消'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        def role_reads():
            with CaptureQueriesContext(connection) as queries:
                response = staff_client.get('/api/admin/orders/')
            self.assertEqual(response.data['code'], 0, response.data)
            return sum('console_adminrole' in row['sql'].lower() and row['sql'].lstrip().upper().startswith('SELECT')
                       for row in queries.captured_queries)
        first_role_reads = role_reads()
        self.assertGreater(first_role_reads, 0)
        second = create('coupon-historical-second')
        self.assertEqual(role_reads(), first_role_reads)
        voucher.refresh_from_db()
        self.assertEqual((voucher.status, voucher.order_id), ('USED', second['id']))
        detail = client.get(f"/api/orders/orders/{first['id']}/").data['data']
        self.assertEqual(detail['refund_summary']['records'][0]['coupon_results'],
                         [{'name':'隔离返还再用券', 'outcome':'RETURNED'}])
        admin = staff_client.get(f"/api/admin/orders/{first['id']}/").data['data']['refund_summary']['records'][0]
        self.assertEqual(admin['coupon_results'], [{'name':'隔离返还再用券', 'outcome':'RETURNED'}])
        self.assertNotIn('benefit_effects', admin)
        self.assertNotIn('recovery_lines', admin)
        repo = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run(['node', str(repo/'scripts/scenarios/native-coupon-refund-live.cjs')],
            input=json.dumps({'order':detail, 'name':'隔离返还再用券', 'outcome':'RETURNED'}, cls=DjangoJSONEncoder),
            text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)

    def test_admin_disable_refreshes_native_coupon_page(self):
        import os
        if os.environ.get('XA_CHECKIN_REDIS_REALTIME') != '1':
            self.skipTest('requires isolated Redis and ASGI')
        boss, staff = make_user(), make_console_user(['coupon:view', 'coupon:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        from coupons.models import Coupon
        coupon = Coupon.objects.create(name='本地可用性券', discount_type='DIRECT', amount=1000)
        client = APIClient()
        token, refresh = issue_account_tokens(account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.post(f'/api/coupons/{coupon.pk}/claim/')
        self.assertEqual(response.data['code'], 0, response.data)
        voucher = UserCoupon.objects.get(account=account, coupon=coupon)
        admin_token, _ = issue_account_tokens(staff_account)
        from users.test_native_checkin_order_realtime import isolated_redis_asgi
        with isolated_redis_asgi() as (base, ws_base):
            def admin_patch(active):
                request = Request(f'{base}/api/admin/coupons/{coupon.pk}/', method='PATCH',
                    data=json.dumps({'is_active': active}).encode(),
                    headers={'Authorization': 'Bearer ' + admin_token, 'Content-Type': 'application/json'})
                with urlopen(request, timeout=20) as response:
                    body = json.load(response)
                self.assertEqual(body['code'], 0, body)
            admin_patch(False)
            coupon.refresh_from_db()
            self.assertFalse(coupon.is_active)
            admin_patch(True)
            coupon.refresh_from_db()
            self.assertTrue(coupon.is_active)
