"""Free safety-box compensation follows service settlement and funding clocks."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import AfterSale, Order, OrderPlatformSubsidy, OrderPlatformSubsidyShare, OrderProvider
from orders.services import settle_order
from orders.settlement import SettlementError
from orders.subsidies import settle_platform_subsidy_share
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from stores.db import transaction
from wallet.models import IncomeHold, ProviderReport, ProviderReportImage, Transaction, get_platform_wallet
from wallet.services import get_wallet


class SafeBoxSettlementFixture:
    def setUp(self):
        media=TemporaryDirectory(prefix='xa-safebox-clocks-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.boss=make_user(); self.customer=get_or_create_account_for_legacy_user(self.boss)
        self.players=[make_provider(deposit_mode='CASH'), make_provider(deposit_mode='CASH')]
        self.accounts=[get_or_create_account_for_legacy_user(player) for player in self.players]
        for player, account in zip(self.players, self.accounts):
            player.escort_profile.account=account; player.escort_profile.save(update_fields=['account'])
            get_wallet(user=player, account=account)
        buyer_wallet=get_wallet(user=self.boss, account=self.customer)
        self.completed=timezone.now()
        self.auditor=make_console_user(['report:view', 'report:audit'])
        self.auditor_account=get_or_create_account_for_legacy_user(self.auditor)
        self.order=make_order(self.boss, service=make_service(price=10000), provider=self.players[1], amount=0,
            status='COMPLETED', payment_status='PAID', settlement_mode='COMPLETION', completed_at=self.completed,
            customer_account=self.customer, provider_account=self.accounts[1], provider_income=0, shop_income=0)
        Transaction.objects.create(wallet=buyer_wallet, order=self.order, tx_type='PAY', amount=0,
                                   balance_before=0, balance_after=0)
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=1, service_aftersale_hours=72,
            service_freeze_hours=72, service_deposit_percent=60)
        self.subsidy=OrderPlatformSubsidy.objects.create(order=self.order, coupon_id_snapshot=1, basis=10000)
        self.shares=[]
        for index, (player, account, amount) in enumerate(zip(self.players, self.accounts, (3200, 4800))):
            assignment=OrderProvider.objects.create(order=self.order, provider=player, provider_account=account,
                settlement_base=0, provider_income=0, released_at=self.completed if index == 0 else None)
            self.shares.append(OrderPlatformSubsidyShare.objects.create(subsidy=self.subsidy, assignment=assignment,
                                                                        amount=amount))
            report=ProviderReport.objects.create(order=self.order, provider=player, provider_account=account,
                game_name='安全箱服务', amount=0, payout_amount=0, status='APPROVED',
                submitted_at=self.completed+timedelta(hours=10*index),
                audited_at=self.completed+timedelta(hours=10*index), auditor=self.auditor,
                auditor_account=self.auditor_account,
                entry_image=screenshot(), completion_image=screenshot())
            ProviderReportImage.objects.create(report=report, image=screenshot())
        self.platform=get_platform_wallet(); self.platform.balance=8000; self.platform.save(update_fields=['balance'])

    def settle(self, now):
        with patch('django.utils.timezone.now', return_value=now):
            with transaction.atomic():
                order=Order.objects.select_for_update().get(pk=self.order.pk)
                return settle_order(order)


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, REQUIRE_ORDER_EVIDENCE_IMAGES=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SafeBoxSettlementClocksTest(SafeBoxSettlementFixture, TestCase):
    def test_transfer_cannot_pay_before_entire_order_finishes(self):
        Order.objects.filter(pk=self.order.pk).update(status='IN_SERVICE', completed_at=None)
        order=Order.objects.get(pk=self.order.pk)
        self.assertFalse(settle_platform_subsidy_share(self.shares[0].pk, transferring_order=order))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_each_provider_waits_for_own_report_and_order_aftersale_clock(self):
        self.settle(self.completed+timedelta(hours=71, minutes=59))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())


        self.settle(self.completed+timedelta(hours=72))
        for share in self.shares: share.refresh_from_db()
        self.order.refresh_from_db()
        self.assertIsNotNone(self.shares[0].paid_at)
        self.assertIsNone(self.shares[1].paid_at)
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(get_wallet(account=self.accounts[0]).balance, 3200)
        self.assertEqual(get_wallet(account=self.accounts[1]).balance, 0)
        from wallet.serializers import ReportSerializer
        first=ProviderReport.objects.get(order=self.order, provider=self.players[0])
        second=ProviderReport.objects.get(order=self.order, provider=self.players[1])
        first_data=ReportSerializer(first).data; second_data=ReportSerializer(second).data
        self.assertEqual((first_data['settlement_status'], first_data['payout_amount']), ('SETTLED', 3200))
        self.assertIsNotNone(first_data['settled_at'])
        self.assertEqual(second_data['settlement_status'], 'WAITING')
        self.settle(self.completed+timedelta(hours=82))
        self.settle(self.completed+timedelta(hours=83))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertEqual(get_wallet(account=self.accounts[1]).balance, 4800)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=self.order).exists())

    def test_funding_must_cover_whole_order_before_any_share_is_paid(self):
        self.platform.balance=7999; self.platform.save(update_fields=['balance'])
        self.settle(self.completed+timedelta(hours=82))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.platform.balance=8000; self.platform.save(update_fields=['balance'])
        self.settle(self.completed+timedelta(hours=82))
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_funding_warning_uses_each_provider_clock(self):
        from wallet.serializers import ReportSerializer
        self.platform.balance=7999; self.platform.save(update_fields=['balance'])
        reports=list(ProviderReport.objects.filter(order=self.order).order_by('submitted_at'))
        with patch('django.utils.timezone.now', return_value=self.completed+timedelta(hours=72)):
            self.assertTrue(ReportSerializer(reports[0]).data['platform_funding_insufficient'])
            self.assertFalse(ReportSerializer(reports[1]).data['platform_funding_insufficient'])
            self.platform.balance=8000; self.platform.save(update_fields=['balance'])
            self.assertFalse(ReportSerializer(reports[0]).data['platform_funding_insufficient'])

    def test_ledger_failure_rolls_back_entire_attempt_and_retry_is_safe(self):
        with patch('orders.subsidies.hold_provider_income', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesMessage(RuntimeError, 'injected failure'):
                self.settle(self.completed+timedelta(hours=82))
        self.platform.refresh_from_db()
        self.assertEqual(self.platform.balance, 8000)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.assertFalse(OrderPlatformSubsidyShare.objects.filter(subsidy=self.subsidy, paid_at__isnull=False).exists())
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [0, 0])
        self.settle(self.completed+timedelta(hours=82))
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_direct_share_retry_keeps_assignment_and_ledger_consistent(self):
        with patch('django.utils.timezone.now', return_value=self.completed+timedelta(hours=82)):
            self.assertTrue(settle_platform_subsidy_share(self.shares[0].pk))
            self.assertTrue(settle_platform_subsidy_share(self.shares[0].pk))
            self.settle(self.completed+timedelta(hours=82))
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 4800])

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_subsidy_income_replenishes_existing_refund_deposit_deficit_first(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        self.profile=self.players[0].escort_profile
        self.profile.deposit_paid=1000; self.profile.save(update_fields=['deposit_paid'])
        self.wallet=get_wallet(account=self.accounts[0])
        subsidy_order=self.order
        self.order=make_order(self.boss, provider=self.players[0], provider_account=self.accounts[0],
                              status='COMPLETED', payment_status='PAID')
        deficit=RefundDepositDeficitTest.deficit(self, 200)
        self.order=subsidy_order
        self.platform.refresh_from_db()
        self.platform.balance=8200; self.platform.save(update_fields=['balance'])
        self.settle(self.completed+timedelta(hours=72))
        deficit.refresh_from_db(); self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((deficit.remaining, self.profile.deposit_paid, self.wallet.balance), (0, 1000, 3000))

    def test_open_scoped_aftersale_blocks_only_affected_provider(self):
        case=AfterSale.objects.create(order=self.order, account=self.customer, reason='核对原陪玩', status='OPEN',
                                      affected_provider_account_ids=[self.accounts[0].pk])
        self.settle(self.completed+timedelta(hours=82))
        for share in self.shares: share.refresh_from_db()
        self.assertIsNone(self.shares[0].paid_at)
        self.assertIsNotNone(self.shares[1].paid_at)
        case.status='RESOLVED'; case.save(update_fields=['status'])
        self.settle(self.completed+timedelta(hours=83))
        self.shares[0].refresh_from_db()
        self.assertIsNotNone(self.shares[0].paid_at)

    def test_pending_report_only_blocks_its_own_share(self):
        ProviderReport.objects.filter(order=self.order, provider=self.players[0]).update(status='PENDING')
        self.settle(self.completed+timedelta(hours=82))
        for share in self.shares: share.refresh_from_db()
        self.assertIsNone(self.shares[0].paid_at)
        self.assertIsNotNone(self.shares[1].paid_at)

    def test_zero_share_transferred_before_service_needs_no_report_or_fake_income(self):
        Order.objects.filter(pk=self.order.pk).update(created_at=self.completed-timedelta(hours=3),
                                                     in_service_at=self.completed-timedelta(hours=1))
        OrderProvider.objects.filter(pk=self.shares[0].assignment_id).update(
            created_at=self.completed-timedelta(hours=3), released_at=self.completed-timedelta(hours=2))
        OrderPlatformSubsidyShare.objects.filter(pk=self.shares[0].pk).update(amount=0)
        ProviderReport.objects.filter(order=self.order, provider=self.players[0]).delete()
        self.settle(self.completed+timedelta(hours=83))
        self.order.refresh_from_db(); self.shares[0].refresh_from_db(); self.shares[1].refresh_from_db()
        self.players[0].escort_profile.refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertIsNotNone(self.shares[0].paid_at)
        self.assertEqual(self.players[0].escort_profile.completed_order_count, 0)
        self.assertEqual(get_wallet(account=self.accounts[0]).balance, 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 1)

    def test_zero_share_without_proof_of_pre_service_transfer_still_requires_report(self):
        OrderPlatformSubsidyShare.objects.filter(pk=self.shares[0].pk).update(amount=0)
        ProviderReport.objects.filter(order=self.order, provider=self.players[0]).delete()
        from orders.services import required_report_providers
        self.assertIn(self.players[0].pk, required_report_providers(self.order))
        self.settle(self.completed+timedelta(hours=83))
        self.shares[0].refresh_from_db(); self.order.refresh_from_db()
        self.assertIsNone(self.shares[0].paid_at)
        self.assertIsNone(self.order.settled_at)

    def test_missing_clock_or_report_evidence_never_pays(self):
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=None)
        self.settle(self.completed+timedelta(hours=82))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=1)
        ProviderReport.objects.filter(order=self.order, provider=self.players[0]).update(completion_image='')
        ProviderReport.objects.filter(order=self.order, provider=self.players[1]).update(completion_image='')
        self.settle(self.completed+timedelta(hours=82))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_unpaid_subsidy_blocks_deposit_refund_until_personal_share_settles(self):
        from console.deposit_refund import deposit_refund_eligibility
        profile=self.players[0].escort_profile
        profile.deposit_paid=1000; profile.save(update_fields=['deposit_paid'])
        wallet=get_wallet(account=self.accounts[0])
        before=deposit_refund_eligibility(profile, wallet)
        self.assertFalse(before['can_refund'])
        self.assertEqual(before['pending_income_count'], 1)
        self.settle(self.completed+timedelta(hours=72))
        after=deposit_refund_eligibility(profile, wallet)
        self.assertEqual(after['pending_income_count'], 0)

    def test_scheduler_retries_report_mode_with_individual_clocks(self):
        from engagement.tasks import retry_completion_subsidies
        Order.objects.filter(pk=self.order.pk).update(settlement_mode='REPORT_APPROVAL')
        with patch('django.utils.timezone.now', return_value=self.completed+timedelta(hours=72)):
            self.assertEqual(retry_completion_subsidies.run(), 0)
        for share in self.shares: share.refresh_from_db()
        self.assertIsNotNone(self.shares[0].paid_at)
        self.assertIsNone(self.shares[1].paid_at)
        with patch('django.utils.timezone.now', return_value=self.completed+timedelta(hours=82)):
            self.assertEqual(retry_completion_subsidies.run(), 1)
            self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_inconsistent_paid_history_cannot_finalize_remaining_share(self):
        OrderPlatformSubsidyShare.objects.filter(pk=self.shares[0].pk).update(paid_at=self.completed)
        with self.assertRaisesMessage(SettlementError, '待核对'):
            self.settle(self.completed+timedelta(hours=82))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_tampered_paid_subsidy_is_neither_displayed_nor_reused(self):
        self.settle(self.completed+timedelta(hours=72))
        share=OrderPlatformSubsidyShare.objects.get(pk=self.shares[0].pk)
        Transaction.objects.filter(pk=share.income_transaction_id).update(amount=3199)
        from wallet.serializers import ReportSerializer
        report=ProviderReport.objects.get(order=self.order, provider=self.players[0])
        data=ReportSerializer(report).data
        self.assertEqual(data['settlement_status'], 'REVIEW')
        self.assertIsNone(data['payout_amount'])
        self.assertIsNone(data['settled_at'])
        with self.assertRaisesMessage(SettlementError, '待核对'):
            self.settle(self.completed+timedelta(hours=82))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE', amount=-4800).exists())


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, REQUIRE_ORDER_EVIDENCE_IMAGES=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class SafeBoxSettlementRaceTest(SafeBoxSettlementFixture, TransactionTestCase):
    def test_simultaneous_settlement_posts_each_share_once(self):
        gate=Barrier(2)
        def worker():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                with transaction.atomic():
                    order=Order.objects.select_for_update().get(pk=self.order.pk)
                    return settle_order(order)
            finally:
                close_old_connections()
        with patch('django.utils.timezone.now', return_value=self.completed+timedelta(hours=82)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:worker(), range(2)))
        self.assertEqual(sorted(len(row) for row in results), [0, 2])
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)
        self.assertEqual([get_wallet(account=account).balance for account in self.accounts], [3200, 4800])


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, REQUIRE_ORDER_EVIDENCE_IMAGES=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SafeBoxSettlementStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_ids_use_only_current_store_funds_and_clocks(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows=[]
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                SafeBoxSettlementFixture.setUp(self)
                rows.append((store, self.order.pk, self.completed))
        self.assertEqual(rows[0][1], rows[1][1])
        with store_context(rows[0][0]), patch('django.utils.timezone.now', return_value=rows[0][2]+timedelta(hours=82)):
            with transaction.atomic():
                settle_order(Order.objects.select_for_update().get(pk=rows[0][1]))
        with store_context(rows[1][0]), patch('django.utils.timezone.now', return_value=rows[1][2]+timedelta(hours=71)):
            with transaction.atomic():
                settle_order(Order.objects.select_for_update().get(pk=rows[1][1]))
            self.assertFalse(Transaction.objects.filter(order_id=rows[1][1], tx_type='SUBSIDY_EXPENSE').exists())
            self.assertEqual(get_platform_wallet().balance, 8000)


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxSettlementApiTest(APITestCase):
    from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest as _flow
    setUp = _flow.setUp
    client_for = staticmethod(_flow.client_for)
    ok = _flow.ok

    def test_vip_coupon_to_report_and_delayed_subsidy_settlement(self):
        from coupons.benefits import grant_periodic
        from coupons.models import UserCoupon
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.tests.factories import make_boss_type
        service=make_service(name='安全箱时钟', price=10000, benefit_kind='SAFEBOX_33',
                             platform='PC', commission_rate=20)
        tier=make_boss_type(name='安全箱会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type=tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher=UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff=make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        staff_account=get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account=staff_account; staff.admin_membership.save(update_fields=['account'])
        player=make_provider(deposit_mode='CASH', deposit_paid=40000)
        account=get_or_create_account_for_legacy_user(player)
        player.escort_profile.account=account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        platform=get_platform_wallet(); platform.balance=10000; platform.save(update_fields=['balance'])
        created=self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'safebox-clock-api'}, format='json'))
        operator=self.client_for(staff_account)
        self.ok(operator.post(f"/api/admin/orders/{created['id']}/assign/", {'provider_id':account.pk}, format='json'))
        provider=self.client_for(account)
        self.ok(provider.post(f"/api/orders/orders/{created['id']}/start/", {'entry_image':screenshot()}, format='multipart'))
        self.ok(provider.post(f"/api/orders/orders/{created['id']}/complete/", {'completion_image':screenshot()}, format='multipart'))
        order=Order.objects.get(pk=created['id'])
        report=ProviderReport.objects.get(order=order, provider=player)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db(); order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertEqual(get_wallet(account=account).balance, 0)
        pending=next(row for row in self.ok(provider.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((pending['payout_amount'], pending['settlement_status']), (8000, 'WAITING'))
        boundary=max(order.completed_at+timedelta(hours=order.service_aftersale_hours),
                     report.submitted_at+timedelta(hours=order.service_freeze_hours))
        with patch('django.utils.timezone.now', return_value=boundary-timedelta(microseconds=1)):
            self.assertEqual(retry_completion_subsidies.run(), 0)
        with patch('django.utils.timezone.now', return_value=boundary):
            self.assertEqual(retry_completion_subsidies.run(), 1)
            self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertEqual(get_wallet(account=account).balance, 8000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)
        paid=next(row for row in self.ok(provider.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((paid['payout_amount'], paid['settlement_status']), (8000, 'SETTLED'))
        self.assertIsNotNone(paid['settled_at'])
