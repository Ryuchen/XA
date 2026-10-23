"""资格券按成功支付核销；退款不返资格，普通券保持原有返还规则。"""
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.services import refund_to_customer
from orders.tests.factories import make_user, make_wallet, make_service, make_console_user, make_coupon
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class QualificationCouponTests(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        self.wallet = make_wallet(self.boss, balance=100000)
        link_legacy_relations(self.boss, self.account)
        self.service = make_service(name='每日超值券 · 保底1688w', price=18800)
        self.coupon = make_coupon(name='每日超值券', discount_type='QUALIFICATION', amount=0, threshold=0,
                                  service_ids=[self.service.pk], limit_units=1)
        self.claim = UserCoupon.objects.create(user=self.boss, account=self.account, coupon=self.coupon)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        for name in ['orders.views._schedule_auto_cancel', 'orders.views.enqueue_kook_dispatch',
                     'orders.views.enqueue_wecom_dispatch', 'orders.views.notify_order_update',
                     'orders.views._push_message_safe', 'orders.notifier.notify_order_update']:
            mocked = patch(name); mocked.start(); self.addCleanup(mocked.stop)

    def payload(self, **extra):
        return {'service_id': self.service.pk, 'user_coupon_id': self.claim.pk,
                'client_request_id': uuid4().hex, **extra}

    def buy(self, **extra):
        return self.client.post('/api/orders/create/', self.payload(**extra), format='json')

    def paid_order(self):
        response = self.buy()
        self.assertEqual(response.data['code'], 0, response.data)
        return Order.objects.get(pk=response.data['data']['id'])

    def assert_consumed(self, order):
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'USED')
        self.assertEqual(self.claim.order_id, order.pk)
        self.assertIsNotNone(self.claim.used_at)
        self.assertFalse(self.claim.restore_on_refund)

    def assert_not_consumed(self):
        self.claim.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual(self.claim.status, 'UNUSED')
        self.assertIsNone(self.claim.order_id)
        self.assertEqual(self.wallet.balance, 100000)

    def test_quote_does_not_consume_but_successful_full_price_order_does(self):
        quote = self.client.post('/api/orders/quote/', self.payload(), format='json')
        self.assertEqual(quote.data['code'], 0, quote.data)
        self.assertEqual(quote.data['data']['coupon_discount'], 0)
        self.assertEqual(quote.data['data']['amount'], 18800)
        self.assert_not_consumed()
        order = self.paid_order()
        self.assertEqual(order.amount, 18800)
        self.assertEqual(order.coupon_discount, 0)
        self.assert_consumed(order)
        response = self.buy()
        self.assertNotEqual(response.data['code'], 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 81200)
        self.assertEqual(Order.objects.count(), 1)

    def test_one_qualification_cannot_purchase_multiple_units(self):
        response = self.buy(game_rounds=2)
        self.assertNotEqual(response.data['code'], 0)
        self.assert_not_consumed()

    def test_wrong_tier_is_rejected_before_money_or_coupon_changes(self):
        other = make_service(price=18800)
        response = self.buy(service_id=other.pk)
        self.assertNotEqual(response.data['code'], 0)
        self.assert_not_consumed()

    def test_empty_binding_fails_closed(self):
        self.coupon.service_ids = []; self.coupon.save(update_fields=['service_ids'])
        self.assertFalse(self.coupon.is_claimable)
        response = self.buy()
        self.assertNotEqual(response.data['code'], 0)
        self.assert_not_consumed()

    def test_other_account_cannot_use_the_coupon(self):
        other = make_user(); account = get_or_create_account_for_legacy_user(other)
        make_wallet(other, balance=100000); link_legacy_relations(other, account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        response = self.buy()
        self.assertNotEqual(response.data['code'], 0)
        self.assert_not_consumed()

    def test_failure_after_deduction_rolls_back_money_and_coupon(self):
        with patch('orders.views.log_only', side_effect=RuntimeError('test transaction rollback')):
            with self.assertRaises(RuntimeError):
                self.buy()
        self.assert_not_consumed()
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())

    def test_expired_or_disabled_coupon_cannot_be_used(self):
        for fields in [{'is_active': False}, {'is_active': True, 'valid_to': timezone.now()-timedelta(seconds=1)}]:
            Coupon.objects.filter(pk=self.coupon.pk).update(**fields)
            response = self.buy()
            self.assertNotEqual(response.data['code'], 0)
            self.assert_not_consumed()

    def test_coupon_disabled_after_validation_is_rechecked_under_lock(self):
        from orders.serializers import CreateOrderSerializer
        original = CreateOrderSerializer.is_valid
        def change_after_validation(serializer, *args, **kwargs):
            valid = original(serializer, *args, **kwargs)
            if valid:
                Coupon.objects.filter(pk=self.coupon.pk).update(is_active=False)
            return valid
        with patch.object(CreateOrderSerializer, 'is_valid', change_after_validation):
            response = self.buy()
        self.assertNotEqual(response.data['code'], 0)
        self.assert_not_consumed()

    def test_boss_cancel_refunds_payment_and_keeps_qualification_consumed(self):
        order = self.paid_order()
        response = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assert_consumed(order)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)
        self.assertNotEqual(self.buy().data['code'], 0)

    def test_admin_cancel_keeps_qualification_consumed(self):
        order = self.paid_order()
        staff = make_console_user(['order:cancel', 'order:view'])
        account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        membership = staff.admin_membership
        membership.account = account; membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        response = self.client.post(f'/api/admin/orders/{order.pk}/cancel/', {'reason': '客户取消'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assert_consumed(order)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100000)

    def test_timeout_cancel_keeps_qualification_consumed(self):
        from orders.tasks import auto_cancel_pending_order
        order = self.paid_order()
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=timezone.now()-timedelta(seconds=1))
        auto_cancel_pending_order.run(order.pk)
        self.assert_consumed(order)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'REFUNDED')
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)

    def test_template_edit_after_use_cannot_restore_qualification_on_refund(self):
        order = self.paid_order()
        Coupon.objects.filter(pk=self.coupon.pk).update(discount_type='DIRECT', amount=1000)
        with transaction.atomic():
            refund_to_customer(order)
        self.assert_consumed(order)

    def test_ordinary_discount_coupon_still_returns_after_cancel(self):
        Coupon.objects.filter(pk=self.coupon.pk).update(discount_type='DIRECT', amount=1000)
        order = self.paid_order()
        self.claim.refresh_from_db(); self.assertTrue(self.claim.restore_on_refund)
        response = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, 'UNUSED')
        self.assertIsNone(self.claim.order_id)
        self.assertIsNone(self.claim.used_at)

    def test_unused_ordinary_rate_coupon_is_not_consumed_when_it_does_not_reduce_price(self):
        Coupon.objects.filter(pk=self.coupon.pk).update(discount_type='RATE', amount=1, discount_rate=100)
        order = self.paid_order()
        self.assertEqual(order.amount, 18800)
        self.claim.refresh_from_db(); self.assertEqual(self.claim.status, 'UNUSED')

    def test_admin_qualification_requires_valid_explicit_binding_and_has_zero_face_value(self):
        from console.serializers import AdminCouponSerializer
        attrs = {'name': '新客超值券', 'discount_type': 'QUALIFICATION', 'amount': 0,
                 'valid_to': timezone.now()+timedelta(days=1)}
        invalid = [None, [], [True], ['1'], [self.service.pk, self.service.pk], [999999],
                   [make_service(is_active=False).pk], [make_service(purchase_mode='CONSULT').pk]]
        for ids in invalid:
            serializer = AdminCouponSerializer(data={**attrs, 'service_ids': ids})
            self.assertFalse(serializer.is_valid(), ids)
        serializer = AdminCouponSerializer(data={**attrs, 'service_ids': [self.service.pk], 'amount': 99})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        saved = serializer.save()
        self.assertEqual((saved.amount, saved.threshold, saved.discount_rate, saved.limit_units), (0, 0, 100, 1))

    def test_binding_options_require_coupon_edit_permission(self):
        denied = self.client.get('/api/admin/coupons/service-options/')
        self.assertEqual(denied.status_code, 403)
        staff = make_console_user(['coupon:edit'])
        account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, account)
        membership = staff.admin_membership
        membership.account = account; membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        allowed = self.client.get('/api/admin/coupons/service-options/')
        self.assertEqual(allowed.data['code'], 0, allowed.data)
        self.assertIn(self.service.pk, [row['id'] for row in allowed.data['data']])


from concurrent.futures import ThreadPoolExecutor
import threading
from django.db import close_old_connections, connections
from django.test import TransactionTestCase, skipUnlessDBFeature


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class QualificationCouponConcurrencyTests(TransactionTestCase):
    def test_two_independent_requests_consume_one_qualification_once(self):
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        wallet = make_wallet(boss, balance=100000); link_legacy_relations(boss, account)
        service = make_service(price=18800)
        coupon = make_coupon(discount_type='QUALIFICATION', amount=0, threshold=0, service_ids=[service.pk], limit_units=1)
        claim = UserCoupon.objects.create(user=boss, account=account, coupon=coupon)
        token = issue_account_tokens(account)[0]
        ready = threading.Barrier(2)
        def request():
            close_old_connections()
            try:
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                ready.wait(timeout=10)
                return client.post('/api/orders/create/', {'service_id': service.pk,
                    'user_coupon_id': claim.pk, 'client_request_id': uuid4().hex}, format='json').data
            finally:
                connections.close_all()
        with patch('orders.views._schedule_auto_cancel'), patch('orders.views.enqueue_kook_dispatch'), \
             patch('orders.views.enqueue_wecom_dispatch'), patch('orders.views.notify_order_update'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(request) for _ in range(2)]
            responses = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(row['code'] == 0 for row in responses), 1, responses)
        self.assertEqual(Order.objects.filter(customer_account=account).count(), 1)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance, 81200)
        claim.refresh_from_db(); self.assertEqual(claim.status, 'USED')
        self.assertFalse(claim.restore_on_refund)
