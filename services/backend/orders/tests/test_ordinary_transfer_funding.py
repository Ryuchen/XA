from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.test import APITransactionTestCase

from orders.models import Order, OrderSegmentSettlementManifest
from orders.points_funding_evidence import verify_order_funding
from orders.service_funding_plan import plan_verified_ordinary_order
from orders.tests import test_segment_settlement_credit as segment_fixture
from stores.context import store_context
from stores.db import transaction
from stores.models import Store
from wallet.models import Transaction
from stores import test_platform_pass_repayment as platform_fixture


@override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=True,
                   SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class OrdinaryTransferFundingTest(APITransactionTestCase):
    def setUp(self):
        segment_fixture.SegmentSettlementCreditTest.setUp(self)
        account = self.accounts[self.provider.pk]
        self.provider.escort_profile.deposit_business_cycle = account.business_cycle
        self.provider.escort_profile.save(update_fields=['deposit_business_cycle'])

    login = segment_fixture.SegmentSettlementCreditTest.login
    ok = segment_fixture.SegmentSettlementCreditTest.ok
    recharge = segment_fixture.SegmentSettlementCreditTest.recharge
    replacement = segment_fixture.SegmentSettlementCreditTest.replacement
    transfer = segment_fixture.SegmentSettlementCreditTest.transfer
    prepare = segment_fixture.SegmentSettlementCreditTest.prepare
    act = segment_fixture.SegmentSettlementCreditTest.act
    review = segment_fixture.SegmentSettlementCreditTest.review
    finished_order = segment_fixture.SegmentSettlementCreditTest.finished_order
    submit_and_approve = segment_fixture.SegmentSettlementCreditTest.submit_and_approve
    mature = segment_fixture.SegmentSettlementCreditTest.mature
    settle = segment_fixture.SegmentSettlementCreditTest.settle

    def replacement(self, name='接替陪玩'):
        user = segment_fixture.SegmentSettlementCreditTest.replacement(self, name)
        account = self.accounts[user.pk]
        user.escort_profile.deposit_business_cycle = account.business_cycle
        user.escort_profile.save(update_fields=['deposit_business_cycle'])
        return user

    def new_order(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'ordinary-transfer-funded',
            'gift_balance_amount': 0 if getattr(self, 'principal_only_test', False) else 1000,
        }, format='json'))
        return Order.objects.get(pk=data['id'])

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', ORDINARY_COMPLETION_TRANSFER_INTERNAL_TESTING=True,
        SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
        SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
        BOSS_FUNDING_SELECTION_ENABLED=False, DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True,
        DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=False,
        SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, PRESTART_SCOPE_SNAPSHOT_ENABLED=False,
        ORDINARY_SERVICE_FUNDING_ENABLED=True, PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True)
    def test_completion_principal_transfer_uses_entry_commission_and_waits_for_reports(self):
        from orders.models import OrderSegmentFinancialSnapshot
        from orders.tests import test_release_flow as flow
        self.principal_only_test = True
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
        replacement = self.replacement()
        order = self.new_order()
        self.enterContext(override_settings(DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=False,
            DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=False, ORDINARY_SERVICE_FUNDING_ENABLED=False,
            SEGMENT_STANDARD_TRANSFER_ENABLED=False))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.service.commission_override = True
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 6000, 'agreement': '双方确认份额'}))
        rows = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__pk'))
        self.assertEqual([row.commission_rate for row in rows], [20, 30])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        old, current = rows[0].segment, rows[1].segment
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id': old.pk, 'completion_image': flow.screenshot()}, format='multipart'))
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': current.pk, 'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        from stores.context import resolve_store
        from orders.services import settle_order
        def actual_settle():
            with transaction.atomic():
                return settle_order(Order.objects.select_for_update().get(pk=order.pk))
        with store_context(resolve_store()):
            self.assertEqual(actual_settle(), [])
            self.mature(order)
            self.assertCountEqual(actual_settle(), [self.provider.pk, replacement.pk])
            self.assertEqual(actual_settle(), [])
            from engagement.confirmation import process_order
            self.assertTrue(process_order(order.pk))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)

    def test_transfer_rebuilds_gift_funding_and_waits_before_any_wallet_credit(self):
        self.service.commission_override = True
        self.service.commission_type = 'PERCENT'
        self.service.commission_rate = 0
        self.service.save(update_fields=['commission_override', 'commission_type', 'commission_rate'])
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        store = Store.objects.get(slug='shenglang')
        with store_context(store), transaction.atomic():
            plan = plan_verified_ordinary_order(order.pk)
            self.assertEqual((plan.principal_units, plan.gift_units, plan.provider_income_units,
                              plan.additional_store_funding_units), (9000, 1000, 10000, 1000))
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.allocated, proof.remaining, proof.fully_funded), (0, 1000, False))
            self.assertEqual(self.settle(order), [])
            from orders.settlement import SettlementError
            with override_settings(SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=False):
                with self.assertRaisesMessage(SettlementError, '赠送或赔付支付的转单须先核对门店补资'):
                    self.settle(order)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_transfer_uses_principal_for_store_income_and_releases_each_player_once(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        self.mature(order)
        store = Store.objects.get(slug='shenglang')
        with store_context(store):
            self.assertCountEqual(self.settle(order), [self.provider.pk, replacement.pk])
            self.assertEqual(self.settle(order), [])
            order.refresh_from_db()
            self.assertEqual((order.provider_income, order.shop_income, order.settlement_store_funding),
                             (8000, 1000, 0))
            from orders.segment_settlement import verify_finalized
            with transaction.atomic():
                verify_finalized(order, OrderSegmentSettlementManifest.objects.get(order=order))
            from engagement.confirmation import process_order
            from engagement.models import ConsumptionConfirmation
            self.assertTrue(process_order(order.pk))
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 10000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated platform and two store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=True,
                   SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                   ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_SEGMENT_SETTLEMENT_INTERNAL_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                   SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class OrdinaryTransferPlatformFundingTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = platform_fixture.PlatformPassRepaymentTests.setUp
    post_receipt = platform_fixture.PlatformPassRepaymentTests.post
    action = platform_fixture.PlatformPassRepaymentTests.action

    def test_finance_receipt_funds_transferred_order_before_individual_settlements(self):
        with store_context(self.first):
            fixture = OrdinaryTransferFundingTest(methodName='test_transfer_rebuilds_gift_funding_and_waits_before_any_wallet_credit')
            fixture.client = APIClient()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.service.commission_override = True
            fixture.service.commission_type = 'PERCENT'
            fixture.service.commission_rate = 0
            fixture.service.save(update_fields=['commission_override', 'commission_type', 'commission_rate'])
            order, replacement, old = fixture.prepare()
            from orders.models import (GameCategory, OrderParticipationSegment, ServiceAddonConfiguration,
                                       ServiceAddonPriceOption)
            from orders.tests.factories import make_service
            from orders.tests import test_release_flow as flow
            current = OrderParticipationSegment.objects.get(attribution__order=order,
                provider_account=fixture.accounts[replacement.pk], released_at__isnull=True)
            fixture.login(replacement)
            fixture.ok(fixture.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
                {'segment_id': current.pk, 'entry_image': flow.screenshot()}, format='multipart'))
            game = GameCategory.objects.create(name='转单补资加购')
            fixture.service.game_category = game
            fixture.service.save(update_fields=['game_category'])
            addon = make_service(name='转单补资加购', is_addon=True, game_category=game)
            config = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon,
                enabled=True, condition_mode='NONE', conditions='', version=1)
            ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=5000)
            fixture.login(fixture.boss)
            path = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = fixture.ok(fixture.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': fixture.accounts[replacement.pk].pk,
                'request_id': 'funded-segment-addon', 'expected_amount': 5000,
                'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json'))
            payment = fixture.ok(fixture.client.post(path + f"{intent['id']}/pay/", {}, format='json'))
            fixture.login(replacement)
            fixture.ok(fixture.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': flow.screenshot()}, format='multipart'))
            order.refresh_from_db()
            fixture.submit_and_approve(order, old, fixture.provider)
            fixture.submit_and_approve(order, current, replacement)
            fixture.mature(order)
            self.assertEqual(fixture.settle(order), [])
        recorded = self.post_receipt('shenglang', {**self.data, 'amount': 1000})
        self.assertEqual(recorded.status_code, 200, recorded.data)
        claim = recorded.data['data']['claim_id']
        verified = self.action(claim, 'verify', {'request_id': str(uuid4())}, slug='shenglang')
        self.assertEqual(verified.status_code, 200, verified.data)
        url = f'/api/admin/platform/pass-refund-repayments/shenglang/{claim}/points-allocation'
        for amount in (400, 600):
            response = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': amount},
                format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(response.status_code, 200, response.data)
            if amount == 400:
                with store_context(self.first):
                    self.assertEqual(fixture.settle(order), [])
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        with store_context(self.first):
            self.assertCountEqual(fixture.settle(order), [fixture.provider.pk, replacement.pk])
            order.refresh_from_db()
            self.assertEqual((order.provider_income, order.shop_income, order.settlement_store_funding), (10000, 0, 1000))
            from orders.segment_settlement import verify_finalized
            with transaction.atomic():
                verify_finalized(order, OrderSegmentSettlementManifest.objects.get(order=order))
            from orders.models import OrderAddonConsumption, OrderSegmentAddonSettlement
            self.assertEqual(OrderSegmentAddonSettlement.objects.get(payment_id=payment['payment_id']).amount, 5000)
            from engagement.confirmation import process_order
            self.assertTrue(process_order(order.pk))
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payment['payment_id']).paid_units, 5000)
        with store_context(self.second):
            self.assertFalse(OrderSegmentAddonSettlement.objects.exists())
