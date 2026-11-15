"""Original-price ranking facts derived from actual wallet refund flows."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.grab_original_facts import original_amount_facts
from orders.models import Order, OrderRefund
from orders.negotiation_execution import apply_refund
from orders.tests import test_provider_aftersale_status as fixture
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class GrabNegotiatedRefundFactsTest(APITransactionTestCase):
    setUp = fixture.ProviderAfterSaleStatusTest.setUp
    login = fixture.ProviderAfterSaleStatusTest.login
    ok = fixture.ProviderAfterSaleStatusTest.ok

    def test_single_provider_actual_partial_refund_reduces_original_price(self):
        from orders.tests.factories import make_boss_type
        boss_account = self.accounts[self.boss.pk]
        boss_account.boss_type = make_boss_type(discount_rate=80)
        boss_account.save(update_fields=['boss_type'])
        account = self.accounts[self.provider.pk]
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': account.pk, 'client_request_id': 'rank-partial-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': fixture.image()}, format='multipart'))
        get_wallet(user=self.provider, account=account)
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund']; role.save(update_fields=['permissions'])
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [account.pk], 'reason': '联系协商',
            'client_request_id': 'rank-partial-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'rank-partial-proposal', 'expected_version': 0, 'refund_units': 5000,
            'provider_terms': [{'account_id': account.pk, 'retained_units': 1400}],
            'explanation': '退款并保留已履约收益'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        execution = apply_refund(case['id'], proposal['id'], operator=self.accounts[self.staff.pk],
                                 legacy_operator=self.staff, request_id='rank-partial-refund')
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.original_amount, order.amount, execution.refund.amount), (10000, 8000, 5000))
        before = Transaction.objects.filter(order=order).count()
        fact = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertEqual((fact.amount, fact.review_order_ids), (5000, ()))
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)
        historical = original_amount_facts([account.pk],
            as_of=execution.refund.created_at - timedelta(microseconds=1))[account.pk]
        self.assertIsNone(historical.amount)
        OrderRefund.objects.filter(pk=execution.refund_id).update(amount=4000)
        altered = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertEqual((altered.amount, altered.review_order_ids), (None, (order.pk,)))
