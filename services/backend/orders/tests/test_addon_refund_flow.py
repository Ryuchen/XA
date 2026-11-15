from django.test import TestCase, override_settings
from django.conf import settings
from django.utils import timezone
from unittest import skipUnless

from console.models import AdminAuditLog
from orders import addon_payment
from orders.models import AfterSale, OrderOriginalShareTransfer, OrderParticipationSegment
from orders.tests.test_addon_configuration import staff_client
from orders.tests import test_addon_intents
from orders.tests.test_addon_payment import prepare
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundFlowTests(TestCase):
    def setUp(self):
        self.fixture = test_addon_intents.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
        intent_id, self.wallet = prepare(self.fixture)
        self.payment = addon_payment.pay(intent_id, actor=self.fixture.boss)
        self.case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss,
            reason='加购项目协商退款', affected_provider_account_ids=[self.fixture.provider.pk])
        self.staff = staff_client(['order:refund'])
        self.staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment.pk}/refund-proposals/'
        self.user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment.pk}/refund-proposals/'
        self.body = {'request_id': 'addon-refund-1', 'amount_raw': 500, 'explanation': '加购部分服务未履行'}

    def test_staff_boss_and_player_confirm_one_paid_addon_without_premature_refund(self):
        self.assertEqual(self.fixture.denied.post(self.staff_url, self.body, format='json').status_code, 403)
        response = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        proposal = response.data['data']
        self.assertEqual(proposal['provider_burden_raw'], 500)
        self.assertEqual(proposal['provider_income_retained_raw'], 700)
        self.assertEqual([(row['kind'], row['amount_raw']) for row in proposal['refund_sources']], [('PREPAID', 500)])
        self.assertFalse(proposal['execution_available'])
        self.assertEqual(self.staff.post(self.staff_url, self.body, format='json').data['data']['id'], proposal['id'])
        self.assertEqual(self.fixture.other_client.get(self.user_url).data['data'], [])
        self.assertEqual(self.fixture.client.get(self.user_url).data['data'][0]['id'], proposal['id'])
        url = self.user_url + f"{proposal['id']}/"
        self.assertEqual(self.fixture.other_client.post(url, {'accepted': True}, format='json').status_code, 404)
        first = self.fixture.client.post(url, {'accepted': True}, format='json')
        self.assertEqual(first.data['data']['status'], 'PENDING')
        second = self.fixture.provider_client.post(url, {'accepted': True}, format='json')
        self.assertEqual(second.data['data']['status'], 'ACCEPTED')
        self.assertFalse(second.data['data']['refund_executed'])
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.staff.post(self.staff_url, {**self.body, 'request_id': 'next'}, format='json').status_code, 400)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 1500)
        self.assertEqual(Transaction.objects.filter(wallet=self.wallet, tx_type='PAY').count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(resource='addon-refund-proposal').count(), 1)

    def test_rejected_terms_can_be_replaced_and_case_scope_is_enforced(self):
        response = self.staff.post(self.staff_url, self.body, format='json')
        url = self.user_url + f"{response.data['data']['id']}/"
        result = self.fixture.provider_client.post(url, {'accepted': False}, format='json')
        self.assertEqual(result.data['data']['status'], 'REJECTED')
        replacement = self.staff.post(self.staff_url, {**self.body, 'request_id': 'replacement', 'amount_raw': 400}, format='json')
        self.assertEqual(replacement.status_code, 200, replacement.data)
        self.case.affected_provider_account_ids = [self.fixture.other.pk]
        self.case.save(update_fields=['affected_provider_account_ids'])
        url = self.user_url + f"{replacement.data['data']['id']}/"
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 400)

    def test_transferred_provider_can_agree_addon_refund_before_handover(self):
        old = self.fixture.segment
        replacement = OrderParticipationSegment.objects.create(attribution=old.attribution,
            provider_account=self.fixture.other, slot=old.slot, original_share=old.original_share,
            accepted_at=timezone.now())
        OrderOriginalShareTransfer.objects.create(previous_segment=old, next_segment=replacement,
            original_share_before=old.original_share, retained_share=0, transferred_share=old.original_share,
            operator=self.fixture.boss, agreement='协商转单', created_at=timezone.now())
        response = self.staff.post(self.staff_url, self.body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        url = self.user_url + f"{response.data['data']['id']}/"
        self.assertEqual(self.fixture.client.post(url, {'accepted': True}, format='json').status_code, 200)
        self.assertEqual(self.fixture.provider_client.post(url, {'accepted': True}, format='json').data['data']['status'],
                         'ACCEPTED')


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonRefundStoresTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_keep_refund_proposals_in_their_own_store(self):
        records = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                fixture = test_addon_intents.AddonIntentTests(methodName='test_boss_intent_records_sources_snapshot_and_idempotency_without_payment')
                intent_id, _ = prepare(fixture, host='xa.esports.club' if index else None)
                payment = addon_payment.pay(intent_id, actor=fixture.boss)
                AfterSale.objects.create(order=fixture.order, account=fixture.boss, reason='加购退款')
                staff = staff_client(['order:refund'])
                url = f'/api/admin/orders/{fixture.order.pk}/addons/payments/{payment.pk}/refund-proposals/'
                response = staff.post(url, {'request_id': 'same', 'amount_raw': 500 + 100 * index,
                                             'explanation': '加购项目协商退款'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                records.append((fixture, payment.pk, response.data['data']))
        self.assertEqual(records[0][1], records[1][1])
        self.assertEqual(records[0][2]['id'], records[1][2]['id'])
        for index, (fixture, payment_id, expected) in enumerate(records):
            url = f'/api/orders/orders/{fixture.order.pk}/addons/payments/{payment_id}/refund-proposals/'
            response = fixture.client.get(url)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['data'][0]['amount_raw'], 500 + 100 * index)
