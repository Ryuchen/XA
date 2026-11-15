"""First-release room orders must have a usable completion path before payment."""
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from unittest import skipUnless

from orders.models import Order
from orders.tests import test_release_flow, test_room_auto_confirmation
from wallet.models import SystemConfig, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class RoomReleaseReadinessTest(APITestCase):
    setUp = test_release_flow.ReleaseFlowTest.setUp
    login = test_release_flow.ReleaseFlowTest.login
    ok = test_release_flow.ReleaseFlowTest.ok
    recharge = test_release_flow.ReleaseFlowTest.recharge
    new_order = test_release_flow.ReleaseFlowTest.new_order
    settle_after_window = test_release_flow.ReleaseFlowTest.settle_after_window

    def test_default_room_flow_reaches_completion_and_later_income(self):
        self.assertTrue(settings.ROOM_COMPLETION_REVIEW_ENABLED)
        self.assertTrue(settings.ROOM_BOSS_CONFIRMATION_ENABLED)
        self.assertTrue(settings.ROOM_AUTO_CONFIRMATION_ENABLED)
        test_room_auto_confirmation.RoomAutoConfirmationMoneyFlowTest.test_auto_completion_does_not_release_income_until_report_and_clock(self)

    def test_missing_room_rules_cannot_take_payment_and_configuration_unblocks_it(self):
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        self.ok(self.recharge())
        self.login(self.boss)
        payload = {'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                   'support_contact_id': self.card.pk, 'client_request_id': 'room-config-payment'}
        before = (Wallet.objects.get(user=self.boss).balance, Transaction.objects.count())
        rejected = self.client.post('/api/orders/create/', payload, format='json')
        self.assertNotEqual(rejected.data.get('code', rejected.status_code), 0, rejected.data)
        self.assertIn('房间确认时限', str(rejected.data))
        self.assertEqual((Wallet.objects.get(user=self.boss).balance, Transaction.objects.count()), before)
        self.assertFalse(Order.objects.exists())

        config = SystemConfig.objects.create(key='room_confirmation_deadline', value='{"minutes":0,"version":1}')
        invalid = self.client.post('/api/orders/create/', payload, format='json')
        self.assertNotEqual(invalid.data.get('code', invalid.status_code), 0, invalid.data)
        self.assertIn('房间确认时限配置异常', str(invalid.data))
        self.assertEqual((Wallet.objects.get(user=self.boss).balance, Transaction.objects.count()), before)
        self.assertFalse(Order.objects.exists())
        config.value = '{"minutes":30,"version":1}'
        config.save(update_fields=['value'])
        result = self.ok(self.client.post('/api/orders/create/', payload, format='json'))
        order = Order.objects.get(pk=result['id'])
        self.assertTrue(order.room_required_snapshot)
        self.assertTrue(hasattr(order, 'room'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, before[0] - 10000)

    @override_settings(ROOM_AUTO_CONFIRMATION_ENABLED=False)
    def test_disabled_room_flow_cannot_accept_new_payment(self):
        SystemConfig.objects.create(key='room_confirmation_deadline', value='{"minutes":30,"version":1}')
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        self.ok(self.recharge())
        self.login(self.boss)
        response = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'room-disabled-payment',
        }, format='json')
        self.assertNotEqual(response.data.get('code', response.status_code), 0, response.data)
        self.assertIn('房间结单流程暂不可用', str(response.data))
        self.assertFalse(Order.objects.exists())


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
class RoomReadinessStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_confirmation_rules_are_required_in_the_order_store(self):
        from rest_framework.exceptions import ValidationError
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.gameplay import validate_service_gameplay
        from orders.tests.factories import make_service

        first, second = resolve_store(), xinan_store()
        with store_context(first):
            SystemConfig.objects.create(key='room_confirmation_deadline', value='{"minutes":30,"version":1}')
            validate_service_gameplay(make_service(room_enabled=True))
        with store_context(second):
            service = make_service(room_enabled=True)
            with self.assertRaisesMessage(ValidationError, '房间确认时限'):
                validate_service_gameplay(service)
            SystemConfig.objects.create(key='room_confirmation_deadline', value='{"minutes":45,"version":1}')
            validate_service_gameplay(service)
