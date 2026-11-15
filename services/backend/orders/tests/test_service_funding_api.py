from datetime import timedelta
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.funding_wallet import refund_locked_order_funding
from engagement.models import ConsumptionConfirmation, MemberProfile, OrderFundingAllocation, PointFundingLot, PointLedger
from engagement.services import config, reverse_order_points, wallet_funding_parts
from orders.models import Order, OrderRefund
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.models import ProviderReport, ProviderReportImage
from wallet.services import get_wallet


@override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, PLATFORM_IDENTITY_ENABLED=True,
                   PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ServiceFundingApiTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.wallet = get_wallet(account=self.account, user=self.user)
        self.wallet.balance = 1700; self.wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1000), ('GIFT', 400), ('COMPENSATION', 300)]:
            PointFundingLot.objects.create(account=self.account, source_key=f'service-funding-{kind}',
                                           kind=kind, remaining=amount)
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        self.provider_user = provider
        self.provider = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = self.provider; provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=1000)
        staff = make_console_user(['order:view', 'order:refund', 'order:dispatch'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff; staff.admin_membership.save(update_fields=['account'])
        self.boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        self.admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
        conf = config(); conf.base_points_rate = 150; conf.save(update_fields=['base_points_rate'])

    def payload(self, **changes):
        return {'service_id': self.service.pk, 'provider_id': self.provider.pk,
                'client_request_id': 'funding-real-order-001', **changes}

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False, SERVICE_ORDINARY_FUNDING_TESTING=False)
    def test_payment_is_rejected_before_debit_when_funding_route_closed(self):
        from wallet.models import Transaction
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=400), format='json')
        self.assertNotEqual(response.data.get('code', 400), 0, response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1700)
        self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').exists())
        self.assertEqual(sum(PointFundingLot.objects.filter(account=self.account).values_list('remaining', flat=True)), 1700)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                       SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
                       PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True)
    def test_production_funding_scope_rejects_aftersales_refund_transfer_and_addon(self):
        from rest_framework.exceptions import ValidationError
        from orders.models import (AfterSale, OrderAddonIntent, OrderParticipationSegment,
                                   OrderRefund, OrderStatusLog)
        from orders.points_checkout_test import require_order_funding_access
        response = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        with transaction.atomic():
            require_order_funding_access(Order.objects.select_for_update().get(pk=order.pk))
        def aftersale():
            AfterSale.objects.create(order=order, account=self.account, reason='scope test')
        def refund():
            OrderRefund.objects.create(order=order, amount=10, reason='scope test')
        def transfer():
            OrderStatusLog.objects.create(order=order, action='TRANSFER')
        def addon():
            segment = OrderParticipationSegment.objects.get(attribution__order=order)
            OrderAddonIntent.objects.create(order=order, segment=segment, provider=self.provider,
                addon=self.service, buyer=self.account, operator=self.staff, request_id='scope-addon',
                request_hash='a' * 64, unit='ITEM', quantity=1, unit_price=10, amount=10,
                principal=10, config_version=1, condition_mode='NONE', addon_name='scope', commission_snapshot={})
        for change in (aftersale, refund, transfer, addon):
            with self.subTest(case=change.__name__), transaction.atomic():
                change()
                with self.assertRaises(ValidationError):
                    require_order_funding_access(Order.objects.select_for_update().get(pk=order.pk))
                transaction.set_rollback(True)
        AfterSale.objects.create(order=order, account=self.account, reason='resolved without refund', status='RESOLVED')
        with transaction.atomic():
            require_order_funding_access(Order.objects.select_for_update().get(pk=order.pk))

    def test_untracked_balance_cannot_be_invented_as_principal(self):
        from wallet.models import Transaction
        quote = self.boss.post('/api/orders/quote/', self.payload(), format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        # A valid quote must not authorize a later debit against a changed,
        # unreconciled source ledger.
        self.wallet.balance += 100
        self.wallet.save(update_fields=['balance'])
        before = list(PointFundingLot.objects.filter(account=self.account).order_by('pk')
                      .values_list('pk', 'remaining'))
        for endpoint in ('quote', 'create'):
            response = self.boss.post(f'/api/orders/{endpoint}/', self.payload(), format='json')
            self.assertNotEqual(response.data.get('code', 400), 0, response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1800)
        self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
        self.assertFalse(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').exists())
        self.assertEqual(before, list(PointFundingLot.objects.filter(account=self.account).order_by('pk')
                                      .values_list('pk', 'remaining')))

    def test_ordinary_service_funding_uses_principal_not_gift_as_cash(self):
        from orders.points_funding_plan import PointsFundingPlanError
        from orders.service_funding_plan import plan_verified_ordinary_order
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=400), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        with transaction.atomic():
            plan = plan_verified_ordinary_order(order.pk)
        self.assertEqual((plan.customer_coin_units, plan.principal_units, plan.gift_units,
                          plan.provider_income_units, plan.additional_store_funding_units), (1000, 600, 400, 800, 200))
        lot = PointFundingLot.objects.get(account=self.account, kind='GIFT')
        lot.kind = 'PREPAID'; lot.save(update_fields=['kind'])
        with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
            plan_verified_ordinary_order(order.pk)

    def test_unknown_source_is_never_assumed_to_be_principal(self):
        from engagement.models import OrderPointAllocation
        from orders.points_funding_plan import PointsFundingPlanError
        from orders.service_funding_plan import plan_verified_ordinary_order
        response = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        OrderFundingAllocation.objects.filter(order=order).delete()
        Order.objects.filter(pk=order.pk).update(funding_terms=None)
        lot = PointFundingLot.objects.create(account=self.account, source_key='historical-mixed',
                                             kind='RECHARGE', remaining=0)
        OrderPointAllocation.objects.create(order=order, lot=lot, amount=500, points_amount=0, source_gift_amount=100)
        with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
            plan_verified_ordinary_order(order.pk)

    def test_ordinary_compensation_and_gift_both_count_towards_store_funding(self):
        from orders.service_funding_plan import plan_verified_ordinary_order
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=200), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        with transaction.atomic():
            plan = plan_verified_ordinary_order(response.data['data']['id'])
        self.assertEqual((plan.principal_units, plan.gift_units, plan.compensation_units,
                          plan.provider_income_units, plan.additional_store_funding_units), (600, 200, 200, 800, 200))

    def test_source_tracked_order_cannot_fall_back_to_unfunded_legacy_settlement(self):
        from orders.services import settle_order
        from orders.settlement import SettlementError
        from wallet.models import Transaction
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=400), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False), self.assertRaises(SettlementError), transaction.atomic():
            settle_order(Order.objects.select_for_update().get(pk=order.pk))
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertFalse(order.transactions.filter(tx_type__in=['INCOME', 'SHOP_INCOME']).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_real_quote_payment_and_full_refund_restore_sources(self):
        payload = self.payload(gift_balance_amount=200, compensation_balance_amount=100)
        quote = self.boss.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertTrue(quote.data['data']['funding_selection_enabled'])
        self.assertTrue(quote.data['data']['balance_sufficient'])
        result = self.boss.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(order.funding_terms['points_rate'], 150)
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})
        self.wallet.refresh_from_db()
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 300, 'gift_balance': 200, 'compensation_balance': 200})
        self.assertFalse(order.point_allocations.exists())
        preview = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refund = self.admin.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离资金来源退款',
            'plan_token': preview.data['data']['plan_token'], 'client_request_id': 'funding-real-refund-001'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        self.wallet.refresh_from_db()
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 1000, 'gift_balance': 400, 'compensation_balance': 300})
        self.assertEqual(sum(order.funding_allocations.values_list('refunded_amount', flat=True)), 1000)

    def test_real_refund_rolls_back_if_original_source_was_changed(self):
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=100), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.wallet.refresh_from_db(); balance = self.wallet.balance
        preview = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        source = PointFundingLot.objects.get(account=self.account, kind='COMPENSATION')
        source.kind = 'GIFT'; source.save(update_fields=['kind'])
        corrupted_preview = self.admin.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertNotEqual(corrupted_preview.data.get('code', 400), 0, corrupted_preview.data)
        result = self.admin.post(f'/api/admin/orders/{order.pk}/refund/', {'reason':'来源异常核对',
            'plan_token':preview.data['data']['plan_token'], 'client_request_id':'bad-funding-refund'}, format='json')
        self.assertNotEqual(result.data.get('code', 400), 0, result.data)
        self.wallet.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(self.wallet.balance, balance)
        self.assertEqual(order.payment_status, 'PAID')
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(order.transactions.filter(tx_type='REFUND').exists())
        self.assertFalse(order.funding_allocations.filter(refunded_amount__gt=0).exists())

    def test_default_principal_and_insufficient_selection_are_not_charged(self):
        self.wallet.balance = 700; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.filter(account=self.account, kind='PREPAID').update(remaining=0)
        quote = self.boss.post('/api/orders/quote/', self.payload(), format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertFalse(quote.data['data']['balance_sufficient'])
        result = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=500), format='json')
        self.assertNotEqual(result.data['code'], 0)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderFundingAllocation.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 700)

    def test_noninteger_coin_choice_is_rejected(self):
        result = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=15), format='json')
        self.assertNotEqual(result.data['code'], 0)
        self.assertFalse(Order.objects.exists())

    def test_insufficient_gift_rolls_back_request_and_can_be_retried(self):
        failed = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=500), format='json')
        self.assertNotEqual(failed.data.get('code', 400), 0)
        self.assertFalse(Order.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 1700)
        retried = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=400), format='json')
        self.assertEqual(retried.data['code'], 0, retried.data)

    def test_staff_dispatch_requires_confirmation_and_preserves_selected_sources(self):
        payload = {'customer_id': self.account.pk, 'service_id': self.service.pk,
                   'gift_balance_amount': 200,
                   'compensation_balance_amount': 100, 'client_request_id': 'funding-staff-dispatch'}
        quote = self.admin.get('/api/admin/orders/dispatch-quote/', payload)
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertTrue(quote.data['data']['balance_sufficient'])
        denied = self.admin.post('/api/admin/orders/dispatch/', payload, format='json')
        self.assertEqual(denied.data['code'], 400, denied.data)
        self.assertFalse(Order.objects.exists())
        response = self.admin.post('/api/admin/orders/dispatch/', {**payload,
            'boss_confirmation': '老板确认赠送20、赔付10，其余使用本金'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual(order.customer_account_id, self.account.pk)
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.provider_account_id)
        self.assertEqual(order.funding_confirmation['actor_account_id'], self.staff.pk)
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})
        detail = self.admin.get(f'/api/admin/orders/{order.pk}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual(detail.data['data']['funding_confirmation']['actor_account_id'], self.staff.pk)
        boss_detail = self.boss.get(f'/api/orders/orders/{order.pk}/')
        self.assertNotIn('funding_confirmation', boss_detail.data['data'])

    def mature(self, order):
        past = timezone.now() - timedelta(hours=80)
        Order.objects.filter(pk=order.pk).update(status='COMPLETED', completed_at=past, settled_at=past)
        ProviderReport.objects.create(order=order, provider=self.provider_user, provider_account=self.provider,
                                      status='APPROVED', game_name='隔离实销', amount=order.amount, submitted_at=past)
        return process_order(order.pk)

    def test_points_wait_for_confirmation_and_refund_uses_original_terms(self):
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=100), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        conf = config(); conf.base_points_rate = 250; conf.save(update_fields=['base_points_rate'])
        self.assertTrue(self.mature(order))
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 1050)
        self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 1000)
        order.refresh_from_db()
        with transaction.atomic():
            refund = OrderRefund.objects.create(order=order, amount=200, reason='隔离本金退回')
            refund_locked_order_funding(order, self.wallet, 200)
            reverse_order_points(order, refund=refund)
        self.assertEqual(PointLedger.objects.get(order=order, kind='REFUND').amount, -300)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 750)

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_source_tracked_settlement_waits_for_verified_funding_route(self):
        from orders.settlement import SettlementError
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=100), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(status='COMPLETED', completed_at=now - timedelta(hours=80))
        report = ProviderReport.objects.create(order=order, provider=self.provider_user, provider_account=self.provider,
            status='APPROVED', game_name='隔离完整结算', amount=order.amount, payout_amount=order.provider_income,
            entry_image='test/entry.png', completion_image='test/completion.png', submitted_at=now)
        ProviderReportImage.objects.create(report=report, image='test/result.png')
        with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False), self.assertRaises(SettlementError):
            process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        report.submitted_at = now - timedelta(hours=80); report.save(update_fields=['submitted_at'])
        with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False), self.assertRaises(SettlementError):
            process_order(order.pk)
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    def test_vip_disabled_at_confirmation_never_retroactively_awards(self):
        response = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        conf = config(); conf.vip_enabled = False; conf.save(update_fields=['vip_enabled'])
        self.assertFalse(self.mature(order))
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        conf.vip_enabled = True; conf.save(update_fields=['vip_enabled'])
        self.assertFalse(process_order(order.pk))
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())

    def test_vip_disabled_at_payment_never_retroactively_awards(self):
        conf = config(); conf.vip_enabled = False; conf.save(update_fields=['vip_enabled'])
        response = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        conf.vip_enabled = True; conf.save(update_fields=['vip_enabled'])
        self.assertFalse(self.mature(order))
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())

    def test_partial_refund_before_confirmation_not_reversed_twice(self):
        response = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=200,
            compensation_balance_amount=100), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        with transaction.atomic():
            OrderRefund.objects.create(order=order, amount=200, reason='隔离先退款')
            refund_locked_order_funding(order, self.wallet, 200)
        self.assertTrue(self.mature(order))
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 750)
        order.refresh_from_db()
        with transaction.atomic():
            refund = OrderRefund.objects.create(order=order, amount=100, reason='隔离后退款')
            refund_locked_order_funding(order, self.wallet, 100)
            reverse_order_points(order, refund=refund)
        self.assertEqual(PointLedger.objects.get(order=order, kind='REFUND').amount, -150)

    def test_refund_rounding_matches_points_for_retained_principal(self):
        conf = config(); conf.base_points_rate = 101; conf.save(update_fields=['base_points_rate'])
        response = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertTrue(self.mature(order))
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 1010)
        order.refresh_from_db()
        with transaction.atomic():
            refund = OrderRefund.objects.create(order=order, amount=10, reason='积分零头核对')
            refund_locked_order_funding(order, self.wallet, 10)
            reverse_order_points(order, refund=refund)
        self.assertEqual(PointLedger.objects.get(order=order, kind='REFUND').amount, -11)
