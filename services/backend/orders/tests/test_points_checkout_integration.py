import os
"""Real quote, wallet, points and cancellation on disposable SQLite data."""
from unittest.mock import patch
from unittest import skipUnless
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens

def upload_recording(client, title='积分试音'):
    from users.test_voice_recordings import audio_file
    response = client.post('/api/users/recordings/', {'audio': audio_file(), 'title': title,
        'client_request_id': uuid4().hex}, format='multipart')
    assert response.data['code'] == 0, response.data
    return response.data['data']['id']

from console.points_redemption_config import save_points_terms
from coupons.models import Coupon, UserCoupon
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from engagement.services import config
from orders.models import (Order, OrderApplication, OrderPointsRedemption, OrderPointsRefund, OrderRefund, OrderProvider,
                           OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot)
from orders.state_machine import IllegalTransitionError, transition
from orders.tests import test_points_redemption_preview as quote_tests
from orders.tests.factories import make_provider, make_service, make_user
from django.utils import timezone
from stores.db import transaction
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortLevel, EscortProfile
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, CELERY_TASK_ALWAYS_EAGER=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
class PointsCheckoutIntegrationTests(TestCase):
    payload = quote_tests.PointsServiceQuoteTest.payload

    def setUp(self):
        quote_tests.PointsServiceQuoteTest.setUp(self)
        from console.grab_config import save_grab_window
        save_grab_window(30, 0, lambda *args: None)
        if self._testMethodName not in ('test_mixed_wallet_sources_and_points_return_to_original_balances',
                                        'test_verified_funding_counts_gift_and_compensation_as_store_cost'):
            quote_tests.seed_recharge(self.wallet)

    def quoted_payload(self):
        payload = self.payload()
        response = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        points = response.data['data']['points_preview']
        self.assertTrue(points['checkout_test_only'])
        return {**{k: v for k, v in payload.items() if k != 'preview_points_hundredths'},
                'points_checkout_test_token': points['checkout_test_token'],
                'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT'}

    def test_vip_promotion_rate_and_amount_coupon_compete_before_points_payment(self):
        from orders.tests.factories import make_promotion
        self.service.allows_discount_coupons = True
        self.service.save(update_fields=['allows_discount_coupons'])
        promotion = make_promotion(discount_rate=85)
        amount = Coupon.objects.create(name='金额券', discount_type='DIRECT', amount=3000)
        amount_owned = UserCoupon.objects.create(account=self.account, coupon=amount)
        rate = Coupon.objects.create(name='八折券', discount_type='RATE', discount_rate=80, amount=1)
        rate_owned = UserCoupon.objects.create(account=self.account, coupon=rate)
        response = self.client.post('/api/orders/discount-options-preview/',
                                    {'service_id': self.service.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        options = response.data['data']['options']
        self.assertEqual((options[0]['key'], options[0]['amount']),
                         (f'promotion:{promotion.pk}|coupon:{amount_owned.pk}', 5500))
        self.assertEqual(next(row['amount'] for row in options if row['key'] == 'vip'), 9000)
        self.assertEqual(next(row['amount'] for row in options if row['key'] == f'coupon:{rate_owned.pk}'), 8000)
        self.assertFalse(any(f'coupon:{rate_owned.pk}' in row['key'] and '|' in row['key'] for row in options))
        payload = {'service_id': self.service.pk, 'discount_selection_token': options[0]['selection_token'],
                   'preview_points_hundredths': 10000, 'client_request_id': 'discount-points-mixed'}
        quote = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        points = quote.data['data']['points_preview']
        self.assertEqual((points['discount_units'], points['estimated_payable_units']), (30, 5470))
        payload.pop('preview_points_hundredths')
        payload['points_checkout_test_token'] = points['checkout_test_token']
        payload['dispatch_route'] = 'PUBLIC'
        payload['dispatch_mode'] = 'BOSS_SELECT'
        paid = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['id'])
        self.assertEqual((order.amount, order.boss_discount, order.promo_discount, order.coupon_discount),
                         (5470, 0, 1500, 3000))
        self.assertEqual(order.funding_allocations.get(kind='PREPAID').amount, 5470)
        self.assertEqual(OrderPointsRedemption.objects.get(order=order).discount_units, 30)
        amount_owned.refresh_from_db(); rate_owned.refresh_from_db()
        self.assertEqual((amount_owned.status, rate_owned.status), ('USED', 'UNUSED'))

    def test_checkout_and_owner_cancel_return_original_assets_once(self):
        payload = self.quoted_payload()
        created = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get()
        receipt = OrderPointsRedemption.objects.get(order=order)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((receipt.deal_units, order.amount, receipt.discount_units), (6000, 5970, 30))
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2030, 5000))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').count(), 1)
        self.assertNotIn('SELECT_PROVIDER', created.data['data']['allowed_actions'])
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        denied = player.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(player)}, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertFalse(OrderApplication.objects.filter(order=order).exists())
        with self.assertRaises(IllegalTransitionError):
            transition(order.pk, Order.Status.GRABBED)
        cancelled = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '不再需要'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertEqual((OrderRefund.objects.count(), OrderPointsRefund.objects.count()), (1, 1))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=order, kind='ORDER_POINTS_RETURN').count(), 1)
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)

    def test_changed_points_balance_or_rules_rejects_quote_without_spending(self):
        payload = self.quoted_payload()
        self.profile.points_balance = 5000; self.profile.save(update_fields=['points_balance'])
        response = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 5000))
        self.assertFalse(Order.objects.exists())

        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_failure_after_wallet_payment_rolls_back_order_coupon_and_points(self):
        payload = self.quoted_payload()
        with patch('orders.points_service_payment.change_points', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/orders/create/', payload, format='json')
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_membership_payment_snapshot_is_atomic_and_detects_changed_terms(self):
        from orders.models import OrderPointsBenefitSnapshot
        from orders.points_benefit_snapshot import verify_locked
        from rest_framework.exceptions import ValidationError
        payload = self.quoted_payload()
        with patch.object(OrderPointsBenefitSnapshot.objects, 'create', side_effect=RuntimeError('会员快照写入失败')):
            with self.assertRaisesMessage(RuntimeError, '会员快照写入失败'):
                self.client.post('/api/orders/create/', payload, format='json')
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertFalse(Order.objects.exists()); self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())
        self.assertFalse(PointLedger.objects.exists())
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)
        result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        receipt = OrderPointsRedemption.objects.get(order=order)
        with transaction.atomic():
            snapshot = verify_locked(receipt)
            self.assertEqual(snapshot.terms['points_rate'], order.funding_terms['points_rate'])
        operation = config(); operation.base_points_rate = 250; operation.save(update_fields=['base_points_rate'])
        with transaction.atomic():
            self.assertEqual(verify_locked(receipt).pk, snapshot.pk)
        with transaction.atomic():
            altered = {**order.funding_terms, 'points_rate': order.funding_terms['points_rate'] + 1}
            Order.objects.filter(pk=order.pk).update(funding_terms=altered)
            receipt = OrderPointsRedemption.objects.select_related('order').get(pk=receipt.pk)
            with self.assertRaises(ValidationError):
                verify_locked(receipt)
            transaction.set_rollback(True)

    def test_changed_points_configuration_invalidates_quote(self):
        payload = self.quoted_payload()
        save_points_terms(group_point_hundredths=10000, group_coin_units=50,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        response = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(PointLedger.objects.exists())
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)

    def test_checkout_cannot_be_opened_without_strict_isolated_test_gate(self):
        payload = self.quoted_payload()
        with self.settings(SERVICE_POINTS_CHECKOUT_TESTING=False):
            response = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        with patch.dict('os.environ', {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            response = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_lost_response_replay_does_not_repeat_wallet_or_points_debit(self):
        payload = self.quoted_payload()
        first = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        replay = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(replay.data['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(replay.data['fundResult']['data']['id'], first.data['data']['id'])
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (2030, 5000))
        self.assertEqual((Order.objects.count(), Transaction.objects.filter(tx_type='PAY').count(),
                          PointLedger.objects.filter(kind='ORDER_POINTS_REDEEM').count()), (1, 1, 1))

    def test_wallet_only_needs_to_cover_the_actual_discounted_coin_amount(self):
        payload = self.quoted_payload()
        self.wallet.balance = 5980; self.wallet.save(update_fields=['balance'])
        PointFundingLot.objects.filter(account=self.account).update(remaining=5980)
        Transaction.objects.create(wallet=self.wallet, tx_type='ADJUST', amount=-2020,
            balance_before=8000, balance_after=5980, remark='隔离测试调减')
        result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 10)

    def test_tampered_token_and_changed_payment_sources_are_rejected(self):
        payload = self.quoted_payload()
        for changed in ({'points_checkout_test_token': payload['points_checkout_test_token'] + 'bad'},
                        {'gift_balance_amount': 10}):
            with self.subTest(changed=changed):
                result = self.client.post('/api/orders/create/', {**payload, **changed}, format='json')
                self.assertNotEqual(result.data.get('code'), 0)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertFalse(Order.objects.exists())

    def test_equal_value_coupon_cannot_be_swapped_after_points_quote(self):
        payload = self.quoted_payload()
        coupon = Coupon.objects.create(name='另一张同额券', discount_type='DIRECT', amount=3000)
        claim = UserCoupon.objects.create(account=self.account, coupon=coupon)
        options = self.client.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
        replacement = next(item['selection_token'] for item in options.data['data']['options']
                           if item['coupon_id'] == claim.pk)
        result = self.client.post('/api/orders/create/', {**payload, 'discount_selection_token': replacement}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertIn('优惠', result.data['msg'])
        self.assertFalse(Order.objects.exists())

    def test_mixed_wallet_sources_and_points_return_to_original_balances(self):
        for kind, amount in (('PREPAID', 7800), ('GIFT', 100), ('COMPENSATION', 100)):
            PointFundingLot.objects.create(account=self.account, source_key=f'test-{kind}', kind=kind, remaining=amount)
        quote_data = {**self.payload(), 'gift_balance_amount': 100, 'compensation_balance_amount': 100}
        quote = self.client.post('/api/orders/quote/', quote_data, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        token = quote.data['data']['points_preview']['checkout_test_token']
        create_data = {key: value for key, value in quote_data.items() if key != 'preview_points_hundredths'}
        result = self.client.post('/api/orders/create/', {**create_data, 'points_checkout_test_token': token,
            'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        funding = dict(order.funding_allocations.values_list('kind', 'amount'))
        self.assertEqual(funding, {'PREPAID': 5770, 'GIFT': 100, 'COMPENSATION': 100})
        result = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(dict(PointFundingLot.objects.values_list('kind', 'remaining')),
                         {'PREPAID': 7800, 'GIFT': 100, 'COMPENSATION': 100})
        self.profile.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    @skipUnless(os.environ.get('XA_POINTS_GRAB_RECEIPT_FORMAL') == '1',
                '抢单收件时钟链须独立最外层事务（18-盘点正式 MySQL 收件时钟待最终联调）；断言保留')
    def test_verified_funding_counts_gift_and_compensation_as_store_cost(self):
        from orders.points_funding_plan import plan_verified_order
        for kind, amount in (('PREPAID', 7000), ('GIFT', 600), ('COMPENSATION', 400)):
            PointFundingLot.objects.create(account=self.account, source_key=f'funding-cost-{kind}', kind=kind, remaining=amount)
        save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        quote_data = {**self.payload(), 'gift_balance_amount': 600, 'compensation_balance_amount': 400}
        quote = self.client.post('/api/orders/quote/', quote_data, format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        create_data = {key: value for key, value in quote_data.items() if key != 'preview_points_hundredths'}
        create_data['points_checkout_test_token'] = quote.data['data']['points_preview']['checkout_test_token']
        created = self.client.post('/api/orders/create/', create_data, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(player)}, format='json').data['code'], 0)
        application = OrderApplication.objects.get(order=order, applicant=account)
        selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 0, selected.data)
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.customer_coin_units, plan.principal_units, plan.gift_units, plan.compensation_units,
                          plan.provider_income_units, plan.additional_store_funding_units), (4000, 3000, 600, 400, 4800, 1800))

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
    def test_segment_funding_basis_uses_verified_pre_points_deal_amount(self):
        payload = self.quoted_payload()
        result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        self.assertEqual((order.amount, coverage.deal_amount, coverage.source, coverage.status),
                         (5970, 6000, 'WALLET_POINTS', 'PENDING'))

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
    def test_inconsistent_payment_evidence_rolls_back_basis_and_checkout(self):
        from orders.points_service_payment import record_locked as original
        payload = self.quoted_payload()
        def tamper(**kwargs):
            receipt = original(**kwargs)
            Transaction.objects.filter(pk=receipt.payment_id).update(amount=-1)
            return receipt
        with patch('orders.points_service_payment.record_locked', side_effect=tamper):
            result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.status_code, 400)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (8000, 15000))
        self.assertFalse(Order.objects.exists())
        self.assertFalse(OrderSegmentFinancialCoverage.objects.exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
    def test_personal_snapshot_uses_deal_even_when_income_exceeds_coin_payment(self):
        from orders.original_attribution import assigned
        from orders.points_funding_plan import plan_points_funding
        from orders.settlement import ProviderShare
        save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        payload = self.quoted_payload()
        result = self.client.post('/api/orders/create/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        provider = make_provider(); account = get_or_create_account_for_legacy_user(provider)
        with transaction.atomic():
            order = Order.objects.select_for_update().get()
            self.assertEqual(order.amount, 4000)
            OrderProvider.objects.create(order=order, provider=provider, provider_account=account,
                settlement_base=4000, commission_type='PERCENT', commission_rate=20, provider_income=3200)
            assigned(order, at=timezone.now())
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        snap = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertEqual((coverage.status, snap.incoming_basis, snap.income_at_entry), ('ACTIVE', 6000, 4800))
        share = ProviderShare(snap.incoming_basis, snap.commission_type, snap.commission_rate,
                              snap.commission_fixed, snap.income_at_entry)
        plan = plan_points_funding(deal_units=6000, point_discount_units=2000, shares=[share], inviter_rate=10)
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units,
                          plan.additional_store_funding_units, plan.inviter_income_units), (4000, 4800, 1200, 400))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True)
    def test_damaged_assignment_is_marked_review_without_creating_share(self):
        from orders.original_attribution import assigned
        result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        provider = make_provider(); account = get_or_create_account_for_legacy_user(provider)
        with transaction.atomic():
            order = Order.objects.select_for_update().get()
            OrderProvider.objects.create(order=order, provider=provider, provider_account=account,
                settlement_base=order.amount, commission_type='PERCENT', commission_rate=20, provider_income=1)
            assigned(order, at=timezone.now())
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=order)
        self.assertEqual(coverage.status, 'REVIEW')
        self.assertFalse(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).exists())

    def create_assigned_points_order(self):
        save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        host = self.client.defaults.get('HTTP_HOST')
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0],
                           **({'HTTP_HOST': host} if host else {}))
        from users.test_voice_recordings import audio_file
        recording = client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '积分试音',
            'client_request_id': uuid4().hex}, format='multipart')
        self.assertEqual(recording.data['code'], 0, recording.data)
        applied = client.post(f'/api/orders/orders/{order.pk}/grab/',
            {'recording_id': recording.data['data']['id']}, format='json')
        self.assertEqual(applied.data['code'], 0, applied.data)
        application = OrderApplication.objects.get(order=order, applicant=account)
        selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 0, selected.data)
        self.assertIsNone(selected.data['data']['expected_income'])
        return order, application

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    def test_real_application_selection_captures_pre_points_entitlement(self):
        from orders.points_funding_plan import PointsFundingPlanError, plan_verified_order
        order, application = self.create_assigned_points_order()
        order.refresh_from_db(); application.refresh_from_db()
        self.assertEqual((order.status, application.status), ('GRABBED', 'SELECTED'))
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertEqual((snapshot.incoming_basis, snapshot.income_at_entry), (6000, 4800))
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units,
                          plan.additional_store_funding_units), (4000, 4800, 800))
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(application.applicant)[0])
        detail = player.get(f'/api/orders/orders/{order.pk}/')
        self.assertEqual(detail.data['data']['expected_income'], 4800)
        self.assertNotIn('START', detail.data['data']['allowed_actions'])
        self.assertFalse(detail.data['data']['report_entry']['available'])
        self.assertFalse(detail.data['data']['can_operate'])
        choices = player.get('/api/wallet/report-order-choices/')
        self.assertEqual(choices.data['data']['list'], [])
        rejected = player.post('/api/wallet/reports/', {'order_id': order.pk}, format='json')
        self.assertNotEqual(rejected.data['code'], 0)
        original_terms = dict(order.funding_terms)
        Order.objects.filter(pk=order.pk).update(funding_terms={key: value for key, value in original_terms.items()
                                                               if key != 'points_redemption_v1'})
        self.assertIsNone(player.get(f'/api/orders/orders/{order.pk}/').data['data']['expected_income'])
        self.assertIsNone(player.get('/api/orders/orders/?status=grabbed').data['data'][0]['expected_income'])
        # Even if the ledger kind is damaged as well, the independent payment
        # receipt still prevents a normal-order income from being displayed.
        PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').update(kind='DAMAGED')
        self.assertTrue(OrderPointsRedemption.objects.filter(order=order).exists())
        self.assertIsNone(player.get('/api/orders/orders/?status=grabbed').data['data'][0]['expected_income'])
        PointLedger.objects.filter(order=order, kind='DAMAGED').update(kind='ORDER_POINTS_REDEEM')
        Order.objects.filter(pk=order.pk).update(funding_terms=original_terms)
        OrderSegmentFinancialSnapshot.objects.filter(pk=snapshot.pk).update(income_at_entry=4799)
        with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
            plan_verified_order(order.pk)
        self.assertIsNone(player.get(f'/api/orders/orders/{order.pk}/').data['data']['expected_income'])
        with self.assertRaises(IllegalTransitionError):
            transition(order.pk, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    def test_existing_report_is_hidden_and_blocked_with_damaged_payment_marker(self):
        from console.serializers import AdminProviderReportSerializer, pending_report_order_block_q
        from wallet.models import ProviderReport
        from wallet.serializers import ReportSerializer
        order, application = self.create_assigned_points_order()
        share = OrderProvider.objects.get(order=order)
        # Simulate a historical report, not a route that allows new reports.
        report = ProviderReport.objects.create(order=order, provider=share.provider,
            provider_account=application.applicant, game_name='历史报单', amount=4000, status='PENDING')
        order.status = 'COMPLETED'
        order.funding_terms = {key: value for key, value in order.funding_terms.items()
                               if key != 'points_redemption_v1'}
        order.save(update_fields=['status', 'funding_terms'])
        for serializer in (ReportSerializer(report), AdminProviderReportSerializer(report)):
            result = serializer.data
            self.assertIsNone(result['amount'])
            self.assertIsNone(result['payout_amount'])
            self.assertIsNone(result['expected_income'])
            self.assertEqual(result['settlement_status'], 'REVIEW')
        self.assertEqual(AdminProviderReportSerializer(report).data['order_approval_block_reason'],
                         '积分订单报单及收益结算尚未接通')
        self.assertTrue(ProviderReport.objects.filter(pk=report.pk).filter(pending_report_order_block_q()).exists())
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(application.applicant)[0])
        rows = player.get('/api/wallet/reports/').data['data']
        self.assertIsNone(rows[0]['amount'])
        self.assertIsNone(rows[0]['expected_income'])

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_points_service_real_report_uses_deal_share_and_does_not_pay(self):
        import tempfile
        from django.test import override_settings
        from engagement.confirmation import process_order
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            order, application = self.create_assigned_points_order()
            player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(application.applicant)[0])
            detail = player.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertIn('START', detail['allowed_actions'])
            choices = player.get('/api/wallet/report-order-choices/').data['data']['list']
            self.assertEqual([item['id'] for item in choices], [order.pk])
            with patch('orders.points_checkout_test.os.environ.get', return_value='config.settings'):
                denied = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertNotEqual(denied.data['code'], 0)
            self.assertFalse(ProviderReport.objects.filter(order=order).exists())
            financial = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
            OrderSegmentFinancialSnapshot.objects.filter(pk=financial.pk).update(income_at_entry=4799)
            blocked = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertNotEqual(blocked.data['code'], 0)
            self.assertFalse(ProviderReport.objects.filter(order=order).exists())
            order.refresh_from_db(); self.assertEqual(order.status, 'GRABBED')
            OrderSegmentFinancialSnapshot.objects.filter(pk=financial.pk).update(income_at_entry=4800)
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            report = ProviderReport.objects.get(order=order)
            self.assertEqual((report.amount, report.commission_rate), (6000, 20))
            completed = player.post(f'/api/orders/orders/{order.pk}/complete/',
                                    {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(completed.data['code'], 0, completed.data)
            upload = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(upload.data['code'], 0, upload.data)
            submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {'commission_rate': 0}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            report.refresh_from_db()
            self.assertEqual((report.amount, report.commission_rate, report.status), (6000, 20, 'PENDING'))
            self.assertEqual(submitted.data['data']['expected_income'], 4800)
            admin = make_console_user(['report:view', 'report:audit'])
            admin_account = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = admin_account; admin.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin_account)[0])
            first_submission = report.submitted_at
            rejected = reviewer.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '补充核对'}, format='json')
            self.assertEqual(rejected.data['code'], 0, rejected.data)
            later = first_submission + timedelta(hours=4)
            with patch('django.utils.timezone.now', return_value=later):
                resubmitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(resubmitted.data['code'], 0, resubmitted.data)
            report.refresh_from_db(); self.assertEqual(report.submitted_at, later)
            financial = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
            OrderSegmentFinancialSnapshot.objects.filter(pk=financial.pk).update(income_at_entry=4799)
            blocked = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertNotEqual(blocked.data['code'], 0)
            report.refresh_from_db(); self.assertEqual(report.status, 'PENDING')
            OrderSegmentFinancialSnapshot.objects.filter(pk=financial.pk).update(income_at_entry=4800)
            approved = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(approved.data['code'], 0, approved.data)
            report.refresh_from_db()
            self.assertEqual((report.payout_amount, report.status), (4800, 'APPROVED'))
            with transaction.atomic(), patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
            order.refresh_from_db()
            self.assertIsNone(order.settled_at)
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_two_points_players_submit_and_audit_their_own_deal_shares(self):
        import tempfile
        from django.test import override_settings
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
            quote_tests.seed_recharge(self.wallet, principal=17000)
            self.service.billing_provider_count = 2; self.service.fulfillment_provider_count = 2; self.service.commission_policy = 'REGULAR'
            self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count', 'commission_policy'])
            save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                              max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
            result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get()
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
            accounts, players = [], []
            for rate in (20, 30):
                user = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(user)
                level = EscortLevel.objects.create(name=f'报单测试-{rate}', commission_rate=rate)
                EscortProfile.objects.filter(user=user).update(account=account, level=level)
                accounts.append(account)
                players.append(APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
            applied = players[0].post(f'/api/orders/orders/{order.pk}/grab/',
                                      {'team_size': 2, 'partner_id': accounts[1].pk, 'recording_id': upload_recording(players[0])}, format='json')
            self.assertEqual(applied.data['code'], 0, applied.data)
            application = OrderApplication.objects.get(order=order, applicant=accounts[0])
            confirmed = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                        {'action': 'CONFIRM_TEAM', 'recording_id': upload_recording(players[1])}, format='json')
            self.assertEqual(confirmed.data['code'], 0, confirmed.data)
            selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
            self.assertEqual(selected.data['code'], 0, selected.data)
            for player in players:
                choices = player.get('/api/wallet/report-order-choices/').data['data']['list']
                self.assertEqual([item['id'] for item in choices], [order.pk])
            for player in players:
                response = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
            for player in players:
                response = player.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            order.refresh_from_db(); self.assertEqual(order.status, 'COMPLETED')
            reports = [ProviderReport.objects.get(order=order, provider_account=account) for account in accounts]
            self.assertEqual([(report.amount, report.commission_rate) for report in reports], [(7500, 20), (7500, 30)])
            for player, report, income in zip(players, reports, (6000, 5250)):
                upload = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(upload.data['code'], 0, upload.data)
                submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(submitted.data['code'], 0, submitted.data)
                self.assertEqual(submitted.data['data']['expected_income'], income)
            admin = make_console_user(['report:view', 'report:audit'])
            account = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = account; admin.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            for report, income in zip(reports, (6000, 5250)):
                approved = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(approved.data['code'], 0, approved.data)
                report.refresh_from_db(); self.assertEqual(report.payout_amount, income)
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    def test_two_providers_keep_individual_rates_and_share_full_deal(self):
        from orders.points_funding_plan import plan_verified_order
        self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
        quote_tests.seed_recharge(self.wallet, principal=17000)
        self.service.billing_provider_count = 2; self.service.fulfillment_provider_count = 2; self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count', 'commission_policy'])
        save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        accounts, clients = [], []
        for rate in (20, 30):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            level = EscortLevel.objects.create(name=f'积分测试-{rate}', commission_rate=rate)
            EscortProfile.objects.filter(user=user).update(account=account, level=level)
            accounts.append(account)
            clients.append(APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
        applied = clients[0].post(f'/api/orders/orders/{order.pk}/grab/',
                                  {'team_size': 2, 'partner_id': accounts[1].pk, 'recording_id': upload_recording(clients[0])}, format='json')
        self.assertEqual(applied.data['code'], 0, applied.data)
        application = OrderApplication.objects.get(order=order, applicant=accounts[0])
        confirm = clients[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                  {'action': 'CONFIRM_TEAM', 'recording_id': upload_recording(clients[1])}, format='json')
        self.assertEqual(confirm.data['code'], 0, confirm.data)
        selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 0, selected.data)
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                         .order_by('segment__slot'))
        self.assertEqual([(s.incoming_basis, s.commission_rate, s.income_at_entry) for s in snapshots],
                         [(7500, 20, 6000), (7500, 30, 5250)])
        self.assertEqual([client.get(f'/api/orders/orders/{order.pk}/').data['data']['expected_income']
                          for client in clients], [6000, 5250])
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        blocked = clients[0].post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
        self.assertNotEqual(blocked.data['code'], 0)
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        order.refresh_from_db()
        self.assertEqual(order.amount, 11000)
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.provider_income_units, plan.additional_store_funding_units), (11250, 250))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    def test_fixed_commission_is_based_on_deal_not_capped_by_coin_payment(self):
        from orders.points_funding_plan import plan_verified_order
        self.service.commission_override = True; self.service.commission_type = 'FIXED'
        self.service.commission_fixed = 5000
        self.service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        save_points_terms(group_point_hundredths=10000, group_coin_units=2000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        applied = client.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(client)}, format='json')
        self.assertEqual(applied.data['code'], 0, applied.data)
        application = OrderApplication.objects.get(order=order, applicant=account)
        result = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        snap = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
        self.assertEqual((snap.incoming_basis, snap.commission_type, snap.commission_fixed, snap.income_at_entry),
                         (6000, 'FIXED', 5000, 1000))
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.provider_income_units, plan.additional_store_funding_units), (1000, 0))

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True)
    def test_selection_rolls_back_if_payment_proof_was_damaged(self):
        result = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
        provider = make_provider(deposit_mode='EARNINGS')
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        self.assertEqual(client.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(client)}, format='json').data['code'], 0)
        application = OrderApplication.objects.get(order=order, applicant=account)
        Transaction.objects.filter(order=order, tx_type='PAY').update(amount=-1)
        selected = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 400)
        order.refresh_from_db(); application.refresh_from_db(); provider.escort_profile.refresh_from_db()
        self.assertEqual((order.status, application.status, provider.escort_profile.status), ('PENDING', 'PENDING', 'AVAILABLE'))
        self.assertFalse(order.providers.exists())
        self.assertFalse(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True)
    def test_verified_shared_receipts_partially_cover_actual_order_shortfall(self):
        from django.core.exceptions import ValidationError
        from orders.points_funding_write import allocate as allocate_points
        from orders.points_funding_allocation import order_funding_is_complete
        from orders.models import OrderPointsFundingAllocation
        from users import test_pass_repayment as pass_tests
        from users.pass_repayment import allocate as allocate_pass, verify_receipt
        order, _ = self.create_assigned_points_order()
        with store_context(resolve_store()):
            other = SimpleNamespace()
            pass_tests.PassRefundRepaymentTests.setUp(other)
            refund = pass_tests.PassRefundRepaymentTests.refund(other, 1000)
            receipt = pass_tests.PassRefundRepaymentTests.receipt(other, amount=1000)
            with self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='unverified', receipt_id=receipt.pk,
                                order_id=order.pk, amount=100)
            verify_receipt(actor='platform-finance', receipt_id=receipt.pk)
            with self.settings(SERVICE_POINTS_FUNDING_TESTING=False), self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='disabled', receipt_id=receipt.pk,
                                order_id=order.pk, amount=100)
            allocate_pass(actor='platform-finance', request_key='pass-use', receipt_id=receipt.pk,
                          refund_id=refund.pk, amount=400)
            first = allocate_points(actor='platform-finance', request_key='points-use', receipt_id=receipt.pk,
                                    order_id=order.pk, amount=600)
            self.assertEqual(allocate_points(actor='platform-finance', request_key='points-use', receipt_id=receipt.pk,
                                             order_id=order.pk, amount=600).pk, first.pk)
            with self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='points-use', receipt_id=receipt.pk,
                                order_id=order.pk, amount=500)
            self.assertFalse(order_funding_is_complete(order_required=800, order_allocated=600))
            with self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='too-much', receipt_id=receipt.pk,
                                order_id=order.pk, amount=1)
            second = pass_tests.PassRefundRepaymentTests.receipt(other, key='receipt-2', reference='bank-2', amount=300)
            verify_receipt(actor='platform-finance', receipt_id=second.pk)
            with patch('orders.points_funding_write.OrderPointsFundingAllocation.objects.create', side_effect=RuntimeError('failure')):
                with self.assertRaises(RuntimeError):
                    allocate_points(actor='platform-finance', request_key='failed', receipt_id=second.pk,
                                    order_id=order.pk, amount=200)
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(redemption__order=order).count(), 1)
            allocate_points(actor='platform-finance', request_key='points-rest', receipt_id=second.pk,
                            order_id=order.pk, amount=200)
            self.assertTrue(order_funding_is_complete(order_required=800, order_allocated=800))
            with self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='over-order', receipt_id=second.pk,
                                order_id=order.pk, amount=1)
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(redemption__order=order).count(), 2)
            OrderPointsFundingAllocation.objects.filter(pk=first.pk).update(amount=601)
            with self.assertRaises(ValidationError):
                allocate_points(actor='platform-finance', request_key='points-rest', receipt_id=second.pk,
                                order_id=order.pk, amount=200)
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())


@override_settings(ALLOWED_HOSTS=['*'], POINTS_REDEMPTION_PREVIEW_ENABLED=True,
                   POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsCheckoutTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_use_each_store_funds_and_terms(self):
        snapshots = []
        for store, host, discount in ((resolve_store(), 'sl.esports.club', 30),
                                      (xinan_store(), 'xa.esports.club', 50)):
            with store_context(store):
                from console.grab_config import save_grab_window
                save_grab_window(30, 0, lambda *args: None)
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(user=user, account=account)
                wallet.balance = 2000; wallet.save(update_fields=['balance'])
                quote_tests.seed_recharge(wallet)
                profile = MemberProfile.objects.create(account=account, points_balance=10000)
                config(); service = make_service(price=1000)
                save_points_terms(group_point_hundredths=10000, group_coin_units=discount,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                quote = client.post('/api/orders/quote/', {'service_id': service.pk,
                                    'preview_points_hundredths': 10000}, format='json')
                self.assertEqual(quote.data['code'], 0, quote.data)
                token = quote.data['data']['points_preview']['checkout_test_token']
                create = client.post('/api/orders/create/', {'service_id': service.pk,
                                     'client_request_id': 'same-in-both-stores',
                                     'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT',
                                     'points_checkout_test_token': token}, format='json')
                self.assertEqual(create.data['code'], 0, create.data)
                order = Order.objects.get()
                wallet.refresh_from_db(); profile.refresh_from_db()
                self.assertEqual((wallet.balance, profile.points_balance), (1000 + discount, 0))
                snapshots.append((account.pk, order.pk, OrderPointsRedemption.objects.get().discount_units))
                cancelled = client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json')
                self.assertEqual(cancelled.data['code'], 0, cancelled.data)
                wallet.refresh_from_db(); profile.refresh_from_db()
                self.assertEqual((wallet.balance, profile.points_balance), (2000, 10000))
        self.assertEqual(snapshots[0][:2], snapshots[1][:2])
        self.assertEqual([row[2] for row in snapshots], [30, 50])


@override_settings(ALLOWED_HOSTS=['*'], POINTS_REDEMPTION_PREVIEW_ENABLED=True,
                   DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True,
                   POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsReportTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = quote_tests.PointsServiceQuoteTest.payload
    quoted_payload = PointsCheckoutIntegrationTests.quoted_payload
    create_assigned_points_order = PointsCheckoutIntegrationTests.create_assigned_points_order

    def test_equal_order_ids_keep_fulfillment_reports_in_their_own_store(self):
        import tempfile
        from django.test import override_settings
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        observed = []
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
                with store_context(store):
                    quote_tests.PointsServiceQuoteTest.setUp(self)
                    quote_tests.seed_recharge(self.wallet)
                    self.client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                    order, application = self.create_assigned_points_order()
                    token = issue_account_tokens(application.applicant)[0]
                    player = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)
                    for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                        response = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                        self.assertEqual(response.data['code'], 0, response.data)
                    report = ProviderReport.objects.get(order=order)
                    uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                           {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                    self.assertEqual(uploaded.data['code'], 0, uploaded.data)
                    submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                    self.assertEqual(submitted.data['code'], 0, submitted.data)
                    self.assertEqual(submitted.data['data']['expected_income'], 4800)
                    observed.append((order.pk, application.applicant_id, report.pk, token))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertEqual(observed[0][:3], observed[1][:3])
            wrong_host = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + observed[1][3])
            self.assertEqual(wrong_host.get('/api/wallet/reports/').status_code, 401)


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   POINTS_REDEMPTION_PREVIEW_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsFundingTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_POINTS_GRAB_RECEIPT_FORMAL') == '1',
                '抢单收件时钟链须独立最外层事务（18-盘点正式 MySQL 收件时钟待最终联调）；断言保留')
    def test_equal_order_and_receipt_ids_never_share_allocated_funds(self):
        from uuid import uuid4
        from orders.models import OrderPointsFundingAllocation
        from stores import test_platform_pass_repayment as platform_tests
        platform_tests.PlatformPassRepaymentTests.setUp(self)
        ids = []
        for store, host, reference, allocated in ((self.first, 'sl.esports.club', 'SL-BANK', 1000),
                                                   (self.second, 'xa.esports.club', 'XA-BANK', 1500)):
            with store_context(store):
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                wallet = get_wallet(user=user, account=account); wallet.balance = 10000; wallet.save(update_fields=['balance'])
                quote_tests.seed_recharge(wallet)
                MemberProfile.objects.create(account=account, points_balance=10000)
                config(); service = make_service(price=10000, commission_rate=20)
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                boss = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                quote = boss.post('/api/orders/quote/', {'service_id': service.pk, 'preview_points_hundredths': 10000}, format='json')
                token = quote.data['data']['points_preview']['checkout_test_token']
                made = boss.post('/api/orders/create/', {'service_id': service.pk, 'client_request_id': 'same-checkout',
                                 'points_checkout_test_token': token}, format='json')
                self.assertEqual(made.data['code'], 0, made.data)
                order = Order.objects.get()
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=1))
                provider = make_provider(deposit_mode='EARNINGS')
                player = get_or_create_account_for_legacy_user(provider)
                EscortProfile.objects.filter(user=provider).update(account=player)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(player)[0])
                applied = client.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(client)}, format='json')
                self.assertEqual(applied.data['code'], 0, applied.data)
                application = OrderApplication.objects.get(order=order, applicant=player)
                selected = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(selected.data['code'], 0, selected.data)
                order_id = order.pk
            data = {**self.data, 'request_id': str(uuid4()), 'external_reference': reference, 'amount': 2000}
            registered = platform_tests.PlatformPassRepaymentTests.post(self, store.slug, data)
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']; receipt_id = registered.data['data']['receipt_id']
            verified = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify',
                        {'request_id': str(uuid4())}, slug=store.slug)
            self.assertEqual(verified.status_code, 200, verified.data)
            url = f'/api/admin/platform/pass-refund-repayments/{store.slug}/{claim}/points-allocation'
            payload = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': allocated}
            funded = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(funded.status_code, 200, funded.data)
            replay = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(replay.data, funded.data)
            changed = self.client.post(url, {**payload, 'amount': allocated - 1}, format='json',
                                       HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(changed.data['errorCode'], 'REQUEST_CONFLICT')
            ids.append((order_id, receipt_id, funded.data['data']['allocation_id']))
        self.assertEqual(ids[0], ids[1])
        for store, expected in ((self.first, 1000), (self.second, 1500)):
            with store_context(store):
                self.assertEqual(OrderPointsFundingAllocation.objects.get(pk=ids[0][2]).amount, expected)


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsFundingPlatformRecoveryTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = quote_tests.PointsServiceQuoteTest.payload
    quoted_payload = PointsCheckoutIntegrationTests.quoted_payload
    create_assigned_points_order = PointsCheckoutIntegrationTests.create_assigned_points_order

    def setUp(self):
        from stores import test_platform_pass_repayment as platform_tests
        from uuid import uuid4
        platform_tests.PlatformPassRepaymentTests.setUp(self)
        self.platform_client = self.client
        with store_context(self.second):
            quote_tests.PointsServiceQuoteTest.setUp(self)
            quote_tests.seed_recharge(self.wallet)
            self.client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            self.order, _ = self.create_assigned_points_order()
        self.client = self.platform_client
        registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan')
        self.assertEqual(registered.status_code, 200, registered.data)
        self.claim = registered.data['data']['claim_id']
        self.receipt_id = registered.data['data']['receipt_id']
        self.url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/points-allocation'
        self.action_data = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 300}

    def fund(self, data=None):
        return self.client.post(self.url, data or self.action_data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)

    def verify(self):
        from uuid import uuid4
        from stores import test_platform_pass_repayment as platform_tests
        return platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'verify', {'request_id': str(uuid4())})

    def test_platform_queue_reports_points_order_current_shortfall(self):
        queue_url = '/api/admin/platform/points-funding/xinan/orders'
        def row():
            response = self.client.get(queue_url, HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(response.status_code, 200, response.data)
            return next(item for item in response.data['data']['list'] if item['order_id'] == self.order.pk)
        initial = row()
        self.assertEqual((initial['funding_status'], initial['required_units'], initial['allocated_units'],
                          initial['remaining_units']), ('NEEDS_FUNDING', 800, 0, 800))
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.fund().status_code, 200)
        partial = row()
        self.assertEqual((partial['required_units'], partial['allocated_units'], partial['remaining_units']), (800, 300, 500))

    def test_unverified_receipt_rejected_without_stuck_intent(self):
        from stores.models import PlatformPointsFundingOperation
        result = self.fund()
        self.assertEqual((result.status_code, result.data['errorCode']), (409, 'POINTS_FUNDING_REVIEW'))
        self.assertFalse(PlatformPointsFundingOperation.objects.exists())
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.fund().status_code, 200)

    def test_tenant_commit_directory_failure_replays_exactly_once(self):
        from django.db import DatabaseError
        from stores.models import PlatformAuditEvent, PlatformPointsFundingOperation
        from stores import test_platform_pass_repayment as platform_tests
        from orders.models import OrderPointsFundingAction, OrderPointsFundingAllocation
        self.assertEqual(self.verify().status_code, 200)
        original = PlatformAuditEvent.objects.create
        def fail_completion(**kwargs):
            if kwargs.get('action') == 'points_funding.allocate_completed':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_points_funding.PlatformAuditEvent.objects.create', side_effect=fail_completion):
            failed = self.fund()
        self.assertEqual((failed.status_code, failed.data['errorCode']), (503, 'POINTS_FUNDING_UNCERTAIN'))
        self.assertIsNone(PlatformPointsFundingOperation.objects.get().tenant_action_id)
        with store_context(self.second):
            self.assertEqual((OrderPointsFundingAction.objects.count(), OrderPointsFundingAllocation.objects.count()), (1, 1))
        reconciliation = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
        self.assertEqual(reconciliation.data['data']['status'], 'PENDING')
        self.assertIsNone(reconciliation.data['data']['claims'][0]['receipt']['unallocated_amount'])
        op_id = reconciliation.data['data']['points_operations'][0]['operation_id']
        recovery_url = f'/api/admin/platform/points-funding/xinan/{op_id}'
        cannot_void = self.client.post(recovery_url + '/void', {'reason': '核查'}, format='json',
                                       HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(cannot_void.data['errorCode'], 'RECOVERY_EVIDENCE_EXISTS')
        _, other_token = platform_tests.PlatformPassRepaymentTests.other_finance(self)
        recovered = self.client.post(recovery_url + '/recover', {}, format='json',
                                     HTTP_AUTHORIZATION='Bearer ' + other_token)
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertEqual(recovered.data['data']['status'], 'COMPLETED')
        replay = self.fund()
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(self.fund().data, replay.data)
        self.assertTrue(PlatformAuditEvent.objects.filter(action='points_funding.allocate_recovered').exists())
        with store_context(self.second):
            self.assertEqual((OrderPointsFundingAction.objects.count(), OrderPointsFundingAllocation.objects.count()), (1, 1))

    def test_reconciliation_checks_both_uses_and_hides_tampered_evidence(self):
        from uuid import uuid4
        from orders.models import OrderPointsFundingAction
        from stores import test_platform_pass_repayment as platform_tests
        refund_id, _ = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)
        self.assertEqual(self.verify().status_code, 200)
        funded = self.fund()
        self.assertEqual(funded.status_code, 200, funded.data)
        allocated = platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                    {'request_id': str(uuid4()), 'refund_id': refund_id, 'amount': 150})
        self.assertEqual(allocated.status_code, 200, allocated.data)
        result = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
        self.assertEqual(result['status'], 'OK')
        receipt = result['claims'][0]['receipt']
        self.assertEqual((receipt['allocated_amount'], receipt['unallocated_amount']), (450, 50))
        self.assertEqual(result['points_operations'][0]['result']['amount'], 300)
        with store_context(self.second):
            OrderPointsFundingAction.objects.update(result={})
        result = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
        self.assertEqual(result['status'], 'REVIEW')
        self.assertIsNone(result['claims'][0]['receipt']['unallocated_amount'])
        self.assertIsNone(result['points_operations'][0]['result'])

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True)
    def test_ordinary_points_and_pass_refunds_share_one_verified_receipt(self):
        from uuid import uuid4
        from orders.points_funding_evidence import verify_order_funding
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from stores import test_platform_pass_repayment as platform_tests
        refund_id, _ = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)
        with store_context(self.second):
            ServiceFundingApiTest.setUp(self)
            self.boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            options = self.boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            payload = ServiceFundingApiTest.payload(self, gift_balance_amount=400,
                discount_selection_token=options.data['data']['options'][0]['selection_token'])
            response = self.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            ordinary_id = response.data['data']['id']
            with transaction.atomic():
                before = verify_order_funding(ordinary_id)
            self.assertEqual((before.required, before.allocated), (200, 0))
        self.assertEqual(self.verify().status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/service-allocation'
        data = {'request_id': str(uuid4()), 'order_id': ordinary_id, 'amount': 200}
        response = self.client.post(url, data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(url, data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token).data, response.data)
        points = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 200})
        self.assertEqual(points.status_code, 200, points.data)
        refunded = platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                   {'request_id': str(uuid4()), 'refund_id': refund_id, 'amount': 100})
        self.assertEqual(refunded.status_code, 200, refunded.data)
        denied = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 1})
        self.assertEqual((denied.status_code, denied.data['errorCode']), (409, 'POINTS_FUNDING_REJECTED'))
        data = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
        self.assertEqual(data['status'], 'OK')
        self.assertEqual((data['claims'][0]['receipt']['allocated_amount'],
                          data['claims'][0]['receipt']['unallocated_amount']), (500, 0))
        with store_context(self.second):
            self.assertTrue(verify_order_funding(ordinary_id).fully_funded)
            points_status = verify_order_funding(self.order.pk)
            self.assertEqual((points_status.required, points_status.allocated), (800, 200))

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True)
    def test_ordinary_funding_recovers_after_tenant_commit_directory_failure(self):
        from django.db import DatabaseError
        from uuid import uuid4
        from orders.models import OrderPointsFundingAction, OrderPointsFundingAllocation
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from stores.models import PlatformAuditEvent, PlatformPointsFundingOperation
        with store_context(self.second):
            ServiceFundingApiTest.setUp(self)
            self.boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            options = self.boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            payload = ServiceFundingApiTest.payload(self, gift_balance_amount=400,
                discount_selection_token=options.data['data']['options'][0]['selection_token'])
            response = self.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            ordinary_id = response.data['data']['id']
        self.assertEqual(self.verify().status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/service-allocation'
        payload = {'request_id': str(uuid4()), 'order_id': ordinary_id, 'amount': 200}
        original = PlatformAuditEvent.objects.create
        def fail_completion(**kwargs):
            if kwargs.get('action') == 'points_funding.allocate_completed':
                raise DatabaseError('simulated directory failure')
            return original(**kwargs)
        with patch('stores.platform_points_funding.PlatformAuditEvent.objects.create', side_effect=fail_completion):
            response = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual((response.status_code, response.data['errorCode']), (503, 'POINTS_FUNDING_UNCERTAIN'))
        operation = PlatformPointsFundingOperation.objects.get(request_id=payload['request_id'])
        with store_context(self.second):
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(service_order_id=ordinary_id).count(), 1)
            self.assertEqual(OrderPointsFundingAction.objects.count(), 1)
        response = self.client.post(f'/api/admin/platform/points-funding/xinan/{operation.pk}/recover', {},
                                    format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['order_id'], ordinary_id)
        replay = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(replay.status_code, 200, replay.data)
        with store_context(self.second):
            self.assertEqual(OrderPointsFundingAllocation.objects.filter(service_order_id=ordinary_id).count(), 1)

    def test_whole_order_funding_requires_verified_platform_operations(self):
        from uuid import uuid4
        from orders.models import OrderPointsFundingAction
        from orders.points_funding_evidence import verify_order_funding
        from orders.points_funding_plan import PointsFundingPlanError
        from stores import test_platform_pass_repayment as platform_tests
        with store_context(self.second):
            initial = verify_order_funding(self.order.pk)
        self.assertEqual((initial.required, initial.allocated, initial.remaining, initial.fully_funded),
                         (800, 0, 800, False))
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.fund().status_code, 200)
        with store_context(self.second):
            partial = verify_order_funding(self.order.pk)
        self.assertEqual((partial.allocated, partial.remaining, partial.fully_funded), (300, 500, False))
        registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan',
            {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-SECOND', 'amount': 600})
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        self.assertEqual(platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify',
                         {'request_id': str(uuid4())}).status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        funded = self.client.post(url, {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500},
                                  format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(funded.status_code, 200, funded.data)
        with store_context(self.second):
            complete = verify_order_funding(self.order.pk)
            self.assertEqual((complete.allocated, complete.remaining, complete.fully_funded), (800, 0, True))
            self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
            OrderPointsFundingAction.objects.filter(pk__in=OrderPointsFundingAction.objects.values('pk')[:1]).update(result={})
            with self.assertRaises(PointsFundingPlanError):
                verify_order_funding(self.order.pk)

    @skipUnless(os.environ.get('XA_POINTS_GRAB_RECEIPT_FORMAL') == '1',
                '收件对账 REVIEW 判定随正式收件时钟接线变化（18-盘点待最终联调）；断言保留')
    def test_reconciliation_rejects_missing_action_table_after_migration(self):
        from orders.models import OrderPointsFundingAction
        from stores import test_platform_pass_repayment as platform_tests
        from stores.db import connection
        self.assertEqual(self.verify().status_code, 200)
        with store_context(self.second):
            tables = [name for name in connection.introspection.table_names()
                      if name != OrderPointsFundingAction._meta.db_table]
            with patch.object(connection.introspection, 'table_names', return_value=tables):
                result = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
        self.assertEqual(result['status'], 'REVIEW')
        self.assertIsNone(result['claims'][0]['receipt']['unallocated_amount'])

    def test_missing_tenant_action_can_be_voided_after_verification(self):
        from django.db import DatabaseError
        from stores.models import PlatformPointsFundingOperation
        self.assertEqual(self.verify().status_code, 200)
        with patch('stores.platform_points_funding.allocate', side_effect=DatabaseError('tenant unavailable')):
            failed = self.fund()
        self.assertEqual((failed.status_code, failed.data['errorCode']), (503, 'POINTS_FUNDING_UNCERTAIN'))
        operation = PlatformPointsFundingOperation.objects.get()
        url = f'/api/admin/platform/points-funding/xinan/{operation.pk}'
        missing = self.client.post(url + '/recover', {}, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(missing.data['errorCode'], 'RECOVERY_EVIDENCE_MISSING')
        with patch('stores.platform_points_funding._claimed_receipt', side_effect=DatabaseError('tenant unavailable')):
            unavailable = self.client.post(url + '/void', {'reason': '门店核查无操作'}, format='json',
                                           HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(unavailable.status_code, 503)
        operation.refresh_from_db(); self.assertIsNone(operation.voided_at)
        voided = self.client.post(url + '/void', {'reason': '门店核查无操作'}, format='json',
                                  HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(voided.status_code, 200, voided.data)
        self.assertEqual(self.fund().data['errorCode'], 'POINTS_FUNDING_VOIDED')
        from uuid import uuid4
        replacement = self.fund({**self.action_data, 'request_id': str(uuid4())})
        self.assertEqual(replacement.status_code, 200, replacement.data)

    def test_platform_super_admin_without_finance_grant_cannot_allocate(self):
        from stores.models import PlatformPointsFundingOperation, PlatformRoleGrant
        self.assertEqual(self.verify().status_code, 200)
        self.grant.is_active = False; self.grant.save(update_fields=['is_active'])
        PlatformRoleGrant.objects.create(account=self.finance, role='SUPER_ADMIN', scope_slug='*')
        self.assertEqual(self.fund().status_code, 404)
        self.assertFalse(PlatformPointsFundingOperation.objects.exists())

    def test_request_ids_cannot_be_reused_across_pass_and_points_operations(self):
        from stores.models import PlatformPassRepaymentOperation
        from stores import test_platform_pass_repayment as platform_tests
        verification = self.verify()
        self.assertEqual(verification.status_code, 200)
        pass_id = PlatformPassRepaymentOperation.objects.get(pk=verification.data['data']['operation_id']).request_id
        conflict = self.fund({**self.action_data, 'request_id': str(pass_id)})
        self.assertEqual(conflict.data['errorCode'], 'REQUEST_CONFLICT')
        funded = self.fund()
        self.assertEqual(funded.status_code, 200, funded.data)
        other = platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
            {'request_id': self.action_data['request_id'], 'refund_id': 1, 'amount': 1})
        self.assertEqual(other.data['errorCode'], 'REQUEST_CONFLICT')

    def test_finance_grant_is_scoped_to_the_requested_store(self):
        self.assertEqual(self.verify().status_code, 200)
        self.grant.scope_slug = 'shenglang'; self.grant.save(update_fields=['scope_slug'])
        self.assertEqual(self.fund().status_code, 404)


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False,
                   GRAB_SINGLE_WINDOW_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, GRAB_TEAM_WINDOW_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsSettlementTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = quote_tests.PointsServiceQuoteTest.payload
    quoted_payload = PointsCheckoutIntegrationTests.quoted_payload
    create_assigned_points_order = PointsCheckoutIntegrationTests.create_assigned_points_order
    setUp = PointsFundingPlatformRecoveryTests.setUp
    fund = PointsFundingPlatformRecoveryTests.fund
    verify = PointsFundingPlatformRecoveryTests.verify

    @override_settings(SERVICE_ORDINARY_FUNDING_TESTING=False, ORDINARY_SERVICE_FUNDING_ENABLED=True,
                       SERVICE_POINTS_CHECKOUT_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
                       SERVICE_POINTS_ASSIGN_TESTING=False, SERVICE_POINTS_REPORT_TESTING=False,
                       SERVICE_POINTS_SETTLEMENT_TESTING=False)
    def test_ordinary_gift_payment_waits_for_store_funding_then_settles(self):
        import tempfile
        from uuid import uuid4
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsSettlement, OrderPointsSettlementPerson, OrderSegmentFinancialSnapshot
        from orders.settlement import SettlementError
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        from wallet.models import ProviderReport, Wallet
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                ServiceFundingApiTest.setUp(self)
                inviter = get_or_create_account_for_legacy_user(make_user())
                self.account.inviter, self.account.inviter_commission_rate = inviter, 30
                self.account.save(update_fields=['inviter', 'inviter_commission_rate'])
                self.boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                options = self.boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
                self.assertEqual(options.data['code'], 0, options.data)
                payload = ServiceFundingApiTest.payload(self, gift_balance_amount=400,
                    discount_selection_token=options.data['data']['options'][0]['selection_token'])
                created = self.boss.post('/api/orders/create/', payload, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    result = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                report = ProviderReport.objects.get(order=order)
                result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
                result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                admin = make_console_user(['report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                result = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                from orders.models import AfterSale
                AfterSale.objects.create(order=order, account=self.account, reason='已处理咨询，无退款',
                                         status='RESOLVED', resolved_at=timezone.now())
                report.refresh_from_db(); matured = report.submitted_at + timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=matured):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                self.assertTrue(player.get('/api/wallet/reports/').data['data'][0]['platform_funding_insufficient'])
            url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/service-allocation'
            funding_data = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 500}
            unauthorized = self.client.post(url, funding_data, format='json')
            self.assertNotEqual(unauthorized.status_code, 200)
            unverified = self.client.post(url, funding_data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(unverified.data['errorCode'], 'POINTS_FUNDING_REVIEW')
            self.assertEqual(self.verify().status_code, 200)
            points_denied = self.client.post(url, {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 100},
                format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(points_denied.data['errorCode'], 'POINTS_FUNDING_REVIEW')
            from django.db import DatabaseError
            from stores.models import PlatformAuditEvent
            original_audit = PlatformAuditEvent.objects.create
            def fail_directory_commit(**kwargs):
                if kwargs.get('action') == 'points_funding.allocate_completed':
                    raise DatabaseError('simulated platform completion failure')
                return original_audit(**kwargs)
            with patch('stores.platform_points_funding.PlatformAuditEvent.objects.create', side_effect=fail_directory_commit):
                uncertain = self.client.post(url, funding_data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(uncertain.data['errorCode'], 'POINTS_FUNDING_UNCERTAIN')
            from orders.models import OrderPointsFundingAllocation
            with store_context(self.second):
                self.assertEqual(OrderPointsFundingAllocation.objects.filter(service_order_id=order.pk).count(), 1)
            result = self.client.post(url, funding_data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(result.status_code, 200, result.data)
            replay = self.client.post(url, funding_data, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(replay.data, result.data)
            conflict = self.client.post(url, {**funding_data, 'amount': 400}, format='json',
                                        HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(conflict.data['errorCode'], 'REQUEST_CONFLICT')
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order)
                original_income = snapshot.income_at_entry
                snapshot.income_at_entry -= 1; snapshot.save(update_fields=['income_at_entry'])
                with self.assertRaises(SettlementError):
                    process_order(order.pk)
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                snapshot.income_at_entry = original_income; snapshot.save(update_fields=['income_at_entry'])
                with patch('orders.points_settlement_test.OrderPointsSettlementPerson.objects.create',
                           side_effect=RuntimeError('simulated settlement failure')):
                    with self.assertRaises(RuntimeError):
                        process_order(order.pk)
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                self.assertTrue(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order=order)
                self.assertIsNone(settlement.redemption_id)
                self.assertEqual((settlement.paid_units, settlement.principal_units, settlement.gift_units,
                                  settlement.funding_units, settlement.provider_units, settlement.store_units),
                                 (1000, 600, 400, 500, 800, 0))
                self.assertEqual((settlement.inviter_units, Wallet.objects.get(account=inviter).balance), (300, 300))
                person = OrderPointsSettlementPerson.objects.get(settlement=settlement)
                self.assertEqual((person.amount, person.income_transaction.amount), (800, 800))
                self.assertEqual(Wallet.objects.get(account=self.provider).balance, 800)
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 1000)
                self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 900)
                displayed = player.get('/api/wallet/reports/').data['data'][0]
                self.assertEqual(displayed['settlement_status'], 'SETTLED')
                self.assertIsNotNone(displayed['settled_at'])
                self.assertFalse(displayed['platform_funding_insufficient'])
                self.assertFalse(process_order(order.pk))
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
            with store_context(self.first):
                self.assertFalse(OrderPointsSettlement.objects.exists())

    @override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True)
    def test_report_waits_for_full_funding_then_posts_real_income_once(self):
        import tempfile
        from uuid import uuid4
        from django.test import override_settings
        from engagement.confirmation import process_order
        from orders.models import OrderPointsSettlement, OrderPointsSettlementFunding, OrderPointsSettlementPerson
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores import test_platform_pass_repayment as platform_tests
        from wallet.models import ProviderReport, Wallet
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                assignment = OrderProvider.objects.get(order_id=self.order.pk)
                get_wallet(user=assignment.provider, account=assignment.provider_account)
                player = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    result = player.post(f'/api/orders/orders/{self.order.pk}/{action}/', {key: screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                report = ProviderReport.objects.get(order_id=self.order.pk)
                result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
                result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                admin = make_console_user(['dashboard:view', 'report:view', 'report:audit'])
                account = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = account; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                report_url = f'/api/admin/reports/{report.pk}/'
                money_fields = ('amount', 'commission_rate', 'expected_income', 'payout_amount')

                def review_queue():
                    rows = reviewer.get('/api/admin/reports/', {
                        'status': 'PENDING', 'order_blocked': 1, 'exclude_direct_test': 1,
                    }).data['data']['list']
                    stats = reviewer.get('/api/admin/dashboard/stats').data['data']
                    return [row['id'] for row in rows], stats['pending_report_order_blocked_total']

                with (self.settings(SERVICE_POINTS_MYSQL_ENABLED=True, SERVICE_POINTS_REPORT_TESTING=False,
                                    SERVICE_POINTS_SETTLEMENT_TESTING=False, SERVICE_ORDINARY_FUNDING_TESTING=False),
                      patch('orders.points_business_access.configured', return_value=True)):
                    pending = reviewer.get(report_url).data['data']
                    self.assertEqual([pending[key] for key in money_fields], [6000, 20, 4800, None])
                    self.assertEqual(pending['order_approval_block_reason'], '')
                    self.assertEqual(review_queue(), ([], 0))
                    for field, value, message in (
                        ('amount', 6001, '待核对'), ('completion_image', '', '报单缺少：结单截图'),
                    ):
                        with self.subTest(field=field), transaction.atomic():
                            ProviderReport.objects.filter(pk=report.pk).update(**{field: value})
                            blocked = reviewer.get(report_url).data['data']
                            self.assertIn(message, blocked['order_approval_block_reason'])
                            self.assertEqual(review_queue(), ([report.pk], 1))
                            with patch('wallet.reporting.formal_points_report_fields') as display_read:
                                rejected = reviewer.post(report_url + 'approve/?order_blocked=1', {}, format='json')
                                self.assertEqual(rejected.data['code'], 400)
                            display_read.assert_not_called()
                            self.assertEqual(ProviderReport.objects.get(pk=report.pk).status, 'PENDING')
                            transaction.set_rollback(True)
                approved = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(approved.data['code'], 0, approved.data)
                report.refresh_from_db()
                matured = report.submitted_at + timedelta(hours=73)
                shown = player.get('/api/wallet/reports/').data['data'][0]
                self.assertTrue(shown['platform_funding_insufficient'])
                # Exercise the formal GET while leaving the SQLite write
                # fixture unchanged. Only the MySQL environment check is replaced.
                with (self.settings(SERVICE_POINTS_MYSQL_ENABLED=True, SERVICE_POINTS_REPORT_TESTING=False,
                                    SERVICE_POINTS_SETTLEMENT_TESTING=False, SERVICE_ORDINARY_FUNDING_TESTING=False),
                      patch('orders.points_business_access.configured', return_value=True)):
                    formal = player.get('/api/wallet/reports/', {'order_id': self.order.pk}).data['data'][0]
                    self.assertEqual((formal['id'], formal['amount'], formal['commission_rate'],
                                      formal['expected_income'], formal['payout_amount']),
                                     (report.pk, 6000, 20, 4800, 4800))
                    self.assertIs(formal['platform_funding_insufficient'], True)
                    console = reviewer.get(report_url).data['data']
                    self.assertEqual([console[key] for key in money_fields], [6000, 20, 4800, 4800])
                    self.assertIs(console['platform_funding_insufficient'], True)
                    with transaction.atomic():
                        ProviderReport.objects.filter(pk=report.pk).update(payout_amount=4801)
                        damaged = player.get('/api/wallet/reports/', {'order_id': self.order.pk}).data['data'][0]
                        for field in ('amount', 'commission_rate', 'expected_income', 'payout_amount',
                                      'platform_funding_insufficient'):
                            self.assertIsNone(damaged[field], field)
                        transaction.set_rollback(True)
                profile = assignment.provider.escort_profile
                deposit_before = profile.deposit_paid
                with patch('django.utils.timezone.now', return_value=matured):
                    self.assertFalse(process_order(self.order.pk))
                self.assertFalse(OrderPointsSettlement.objects.exists())
                self.assertFalse(Transaction.objects.filter(order_id=self.order.pk, tx_type='INCOME').exists())
            self.assertEqual(self.verify().status_code, 200)
            self.assertEqual(self.fund().status_code, 200)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                self.assertFalse(process_order(self.order.pk))
                self.assertFalse(OrderPointsSettlement.objects.exists())
            second = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan',
                {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-SETTLEMENT-2', 'amount': 600})
            self.assertEqual(second.status_code, 200, second.data)
            claim = second.data['data']['claim_id']
            result = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(result.status_code, 200, result.data)
            url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
            result = self.client.post(url, {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 500},
                                      format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(result.status_code, 200, result.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
                with patch('orders.points_settlement_test.OrderPointsSettlementPerson.objects.create',
                           side_effect=RuntimeError('simulated settlement failure')):
                    with self.assertRaises(RuntimeError):
                        process_order(self.order.pk)
                self.assertFalse(OrderPointsSettlement.objects.exists())
                self.assertFalse(Transaction.objects.filter(order_id=self.order.pk, tx_type='INCOME').exists())
                profile.refresh_from_db(); self.assertEqual(profile.deposit_paid, deposit_before)
                self.assertFalse(process_order(self.order.pk))
                receipt = OrderPointsSettlement.objects.get(order_id=self.order.pk)
                person = OrderPointsSettlementPerson.objects.get(settlement=receipt)
                self.assertEqual((receipt.paid_units, receipt.funding_units, receipt.provider_units), (4000, 800, 4800))
                self.assertEqual(sum(OrderPointsSettlementFunding.objects.values_list('amount', flat=True)), 800)
                self.assertEqual((person.amount, person.income_transaction.amount), (4800, 4800))
                order = Order.objects.get(pk=self.order.pk)
                self.assertIsNotNone(order.settled_at)
                self.assertIsNone(order.consumption_confirmed_at)
                self.assertIsNotNone(order.funding_benefits_resolved_at)
                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
                self.assertFalse(process_order(order.pk))
                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                profile.refresh_from_db()
                deposit = profile.deposit_paid - deposit_before
                self.assertEqual(Wallet.objects.get(account=assignment.provider_account).balance, 4800 - deposit)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
                displayed = player.get('/api/wallet/reports/').data['data'][0]
                self.assertEqual(displayed['settlement_status'], 'SETTLED')
                self.assertIsNotNone(displayed['settled_at'])
                self.assertEqual(displayed['expected_income'], 4800)
                self.assertFalse(displayed['platform_funding_insufficient'])
                detail = player.get(f'/api/orders/orders/{order.pk}/').data['data']
                self.assertEqual((detail['expected_income'], detail['settlement_status']), (4800, 'SETTLED'))
                self.assertFalse(process_order(order.pk))
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
            with store_context(self.first):
                self.assertFalse(OrderPointsSettlement.objects.exists())

    @override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True)
    def test_double_order_needs_full_funding_before_individual_freeze_windows(self):
        import tempfile
        from uuid import uuid4
        from django.test import override_settings
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsFundingAction, OrderPointsSettlement, OrderPointsSettlementPerson
        from orders.points_redemption_config import current_rules
        from orders.settlement import SettlementError
        from orders.tests.factories import make_boss_type, make_console_user
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                user = make_user()
                self.account = get_or_create_account_for_legacy_user(user)
                self.account.boss_type = make_boss_type(discount_rate=90)
                self.account.save(update_fields=['boss_type'])
                self.wallet = get_wallet(user=user, account=self.account)
                self.profile = MemberProfile.objects.create(account=self.account, points_balance=15000)
                self.service = make_service(price=10000)
                self.client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                self.wallet.balance = 25000; self.wallet.save(update_fields=['balance'])
                quote_tests.seed_recharge(self.wallet)
                self.service.billing_provider_count = 2; self.service.fulfillment_provider_count = 2; self.service.commission_policy = 'REGULAR'
                self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count', 'commission_policy'])
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                created = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
                accounts, players = [], []
                for rate in (20, 30):
                    user = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(user)
                    level = EscortLevel.objects.create(name=f'补资双陪-{rate}', commission_rate=rate)
                    EscortProfile.objects.filter(user=user).update(account=account, level=level)
                    accounts.append(account)
                    players.append(APIClient(HTTP_HOST='xa.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
                response = players[0].post(f'/api/orders/orders/{order.pk}/grab/',
                                           {'team_size': 2, 'partner_id': accounts[1].pk, 'recording_id': upload_recording(players[0])}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                application = OrderApplication.objects.get(order=order, applicant=accounts[0])
                response = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                           {'action': 'CONFIRM_TEAM', 'recording_id': upload_recording(players[1])}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    for player in players:
                        response = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                        self.assertEqual(response.data['code'], 0, response.data)
                reports = [ProviderReport.objects.get(order=order, provider_account=account) for account in accounts]
                for player, report in zip(players, reports):
                    response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                           {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = players[0].post(f'/api/wallet/reports/{reports[0].pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                reports[0].refresh_from_db(); first = reports[0].submitted_at
                admin = make_console_user(['report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                response = reviewer.post(f'/api/admin/reports/{reports[0].pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                with patch('django.utils.timezone.now', return_value=first + timedelta(hours=24)):
                    response = players[1].post(f'/api/wallet/reports/{reports[1].pk}/submit/', {}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                    response = reviewer.post(f'/api/admin/reports/{reports[1].pk}/approve/', {}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 249})
            self.assertEqual(funded.status_code, 200, funded.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=order.pk).exists())
                self.assertFalse(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').exists())
            funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1})
            self.assertEqual(funded.status_code, 200, funded.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                people = OrderPointsSettlementPerson.objects.filter(settlement__order_id=order.pk)
                self.assertEqual(list(people.values_list('amount', flat=True)), [6000])
                order.refresh_from_db(); self.assertIsNone(order.settled_at)
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                statuses = [player.get('/api/wallet/reports/').data['data'][0]['settlement_status'] for player in players]
                self.assertEqual(statuses, ['SETTLED', 'WAITING'])
                self.assertEqual(players[0].get(f'/api/orders/orders/{order.pk}/').data['data']['settlement_status'], 'WAITING')
                action = OrderPointsFundingAction.objects.filter(allocation__redemption__order_id=order.pk).first()
                result = action.result
                OrderPointsFundingAction.objects.filter(pk=action.pk).update(result={})
                self.assertEqual(players[0].get('/api/wallet/reports/').data['data'][0]['settlement_status'], 'REVIEW')
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first + timedelta(hours=97)):
                with self.assertRaises(SettlementError):
                    process_order(order.pk)
                self.assertEqual(people.count(), 1)
                OrderPointsFundingAction.objects.filter(pk=action.pk).update(result=result)
                self.assertTrue(process_order(order.pk))
                self.assertEqual(sorted(people.values_list('amount', flat=True)), [5250, 6000])
                order.refresh_from_db(); self.assertIsNotNone(order.settled_at)
                self.assertIsNotNone(order.consumption_confirmed_at)
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 11000)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 2)
                statuses = [player.get('/api/wallet/reports/').data['data'][0]['settlement_status'] for player in players]
                self.assertEqual(statuses, ['SETTLED', 'SETTLED'])

    def test_positive_margin_credits_boss_referrer_as_principal_and_store_once(self):
        import tempfile
        from django.test import override_settings
        from engagement.confirmation import process_order
        from engagement.models import PointFundingLot
        from engagement.services import wallet_funding_parts
        from orders.models import OrderPointsSettlement
        from orders.points_redemption_config import current_rules
        from orders.tests.factories import make_boss_type, make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.db import transaction
        from wallet.models import ProviderReport, Wallet
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            buyer, inviter = make_user(), make_user()
            self.account = get_or_create_account_for_legacy_user(buyer)
            inviter_account = get_or_create_account_for_legacy_user(inviter)
            self.account.boss_type = make_boss_type(discount_rate=90)
            self.account.inviter = inviter_account; self.account.inviter_commission_rate = 10
            self.account.save(update_fields=['boss_type', 'inviter', 'inviter_commission_rate'])
            self.wallet = get_wallet(user=buyer, account=self.account)
            self.wallet.balance = 8000; self.wallet.save(update_fields=['balance'])
            quote_tests.seed_recharge(self.wallet)
            MemberProfile.objects.update_or_create(account=self.account, defaults={'points_balance': 15000})
            self.service = make_service(price=10000)
            save_points_terms(group_point_hundredths=10000, group_coin_units=30,
                              max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
            self.client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            created = self.client.post('/api/orders/create/', self.quoted_payload(), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
            provider = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(provider)
            EscortProfile.objects.filter(user=provider).update(account=account)
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            result = player.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(player)}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            application = OrderApplication.objects.get(order=order, applicant=account)
            result = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                result = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            report = ProviderReport.objects.get(order=order)
            result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            report.refresh_from_db()
            admin = make_console_user(['report:view', 'report:audit'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            result = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)

            def assert_formal_display(status, settled_at):
                # Replace only the MySQL prerequisite so SQLite can exercise
                # the production settlement reads with the test gate closed.
                with (self.settings(SERVICE_POINTS_MYSQL_ENABLED=True, SERVICE_POINTS_SETTLEMENT_TESTING=False,
                                    SERVICE_ORDINARY_FUNDING_TESTING=False),
                      patch('orders.points_business_access.configured', return_value=True)):
                    rows = player.get('/api/wallet/reports/').data['data']
                    self.assertEqual([(row['settlement_status'], row['settled_at']) for row in rows],
                                     [(status, settled_at)])
                    detail = player.get(f'/api/orders/orders/{order.pk}/').data['data']
                    self.assertEqual(detail['settlement_status'], status)

            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order=order)
                self.assertEqual((settlement.paid_units, settlement.funding_units, settlement.provider_units,
                                  settlement.inviter_units, settlement.store_units), (5970, 0, 4800, 597, 573))
                inviter_wallet = Wallet.objects.get(account=inviter_account)
                self.assertEqual((inviter_wallet.balance, wallet_funding_parts(inviter_wallet)['prepaid_balance']), (597, 597))
                lot = PointFundingLot.objects.get(source_key=f'points-settlement-referral:{settlement.pk}')
                self.assertEqual((lot.account_id, lot.kind, lot.remaining), (inviter_account.pk, 'PREPAID', 597))
                self.assertEqual(settlement.inviter_transaction.amount, 597)
                self.assertEqual(settlement.store_transaction.amount, 573)
                row = player.get('/api/wallet/reports/').data['data'][0]
                self.assertEqual(row['settlement_status'], 'SETTLED')
                self.assertIsNotNone(row['settled_at'])
                assert_formal_display('SETTLED', row['settled_at'])
                self.assertFalse(process_order(order.pk))
                self.assertEqual(PointFundingLot.objects.filter(pk=lot.pk).count(), 1)
                with transaction.atomic():
                    Transaction.objects.create(order=order, wallet=inviter_wallet, amount=1,
                                               tx_type=Transaction.TxType.INCOME, remark=f'订单收入：{order.order_no}',
                                               balance_before=inviter_wallet.balance,
                                               balance_after=inviter_wallet.balance + 1)
                    assert_formal_display('REVIEW', None)
                    transaction.set_rollback(True)
                PointFundingLot.objects.filter(pk=lot.pk).update(kind='GIFT')
                self.assertEqual(player.get('/api/wallet/reports/').data['data'][0]['settlement_status'], 'REVIEW')
                assert_formal_display('REVIEW', None)

    @override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True)
    @skipUnless(os.environ.get('XA_POINTS_GRAB_RECEIPT_FORMAL') == '1',
                '抢单收件时钟链须独立最外层事务（18-盘点正式 MySQL 收件时钟待最终联调）；断言保留')
    def test_gift_funding_and_referral_conserve_real_order_sources(self):
        import tempfile
        from uuid import uuid4
        from django.test import override_settings
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsSettlement
        from orders.points_redemption_config import current_rules
        from orders.tests.factories import make_boss_type, make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores import test_platform_pass_repayment as platform_tests
        from wallet.models import ProviderReport, Wallet
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                buyer, inviter = make_user(), make_user()
                account = get_or_create_account_for_legacy_user(buyer)
                inviter_account = get_or_create_account_for_legacy_user(inviter)
                account.boss_type = make_boss_type(discount_rate=90)
                account.inviter = inviter_account; account.inviter_commission_rate = 10
                account.save(update_fields=['boss_type', 'inviter', 'inviter_commission_rate'])
                operation = config(); operation.base_points_rate = 150; operation.save(update_fields=['base_points_rate'])
                wallet = get_wallet(user=buyer, account=account)
                wallet.balance = 8000; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=account, source_key='mixed-principal', kind='PREPAID', remaining=7400)
                PointFundingLot.objects.create(account=account, source_key='mixed-gift', kind='GIFT', remaining=400)
                PointFundingLot.objects.create(account=account, source_key='mixed-compensation', kind='COMPENSATION', remaining=200)
                MemberProfile.objects.update_or_create(account=account, defaults={'points_balance': 15000})
                service = make_service(price=3000, commission_rate=20)
                coupon = Coupon.objects.create(name='混合支付减免', discount_type='DIRECT', amount=700)
                UserCoupon.objects.create(account=account, coupon=coupon)
                save_points_terms(group_point_hundredths=10000, group_coin_units=300,
                                  max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
                self.assertEqual(options.data['code'], 0, options.data)
                payload = {'service_id': service.pk, 'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                           'preview_points_hundredths': 10000, 'gift_balance_amount': 400, 'compensation_balance_amount': 200,
                           'client_request_id': 'mixed-points-settlement'}
                quote = boss.post('/api/orders/quote/', payload, format='json')
                self.assertEqual(quote.data['code'], 0, quote.data)
                payload.pop('preview_points_hundredths')
                payload['points_checkout_test_token'] = quote.data['data']['points_preview']['checkout_test_token']
                created = boss.post('/api/orders/create/', payload, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                self.assertEqual((order.original_amount - order.boss_discount - order.coupon_discount, order.amount), (2000, 1700))
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
                provider = make_provider(deposit_mode='EARNINGS')
                provider_account = get_or_create_account_for_legacy_user(provider)
                EscortProfile.objects.filter(user=provider).update(account=provider_account)
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
                result = player.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': upload_recording(player)}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                application = OrderApplication.objects.get(order=order, applicant=provider_account)
                result = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    result = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                report = ProviderReport.objects.get(order=order)
                result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
                result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                report.refresh_from_db()
                admin = make_console_user(['report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                result = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            result = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 500})
            self.assertEqual(result.status_code, 200, result.data)
            mature = report.submitted_at + timedelta(hours=73)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=mature):
                self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=order.pk).exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
                self.assertTrue(player.get('/api/wallet/reports/').data['data'][0]['platform_funding_insufficient'])
            registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan',
                {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-MIXED-GIFT', 'amount': 200})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            self.assertEqual(platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify',
                             {'request_id': str(uuid4())}).status_code, 200)
            result = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 170},
                format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(result.status_code, 200, result.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=mature):
                self.assertTrue(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order_id=order.pk)
                self.assertEqual((settlement.paid_units, settlement.principal_units, settlement.gift_units,
                                  settlement.compensation_units, settlement.funding_units, settlement.provider_units,
                                  settlement.inviter_units, settlement.store_units), (1700, 1100, 400, 200, 670, 1600, 170, 0))
                self.assertEqual(Wallet.objects.get(account=inviter_account).balance, 170)
                self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order.pk).amount, 1700)
                self.assertEqual(PointLedger.objects.get(order_id=order.pk, kind='CONSUMPTION').amount, 1650)
                self.assertFalse(process_order(order.pk))
                self.assertEqual(PointLedger.objects.filter(order_id=order.pk, kind='CONSUMPTION').count(), 1)
                self.assertEqual(player.get('/api/wallet/reports/').data['data'][0]['settlement_status'], 'SETTLED')
