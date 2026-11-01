"""Existing paid orders remain service orders after a real catalog reimport."""
import io
import json
from datetime import timedelta
from unittest.mock import patch
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.management.commands.seed_club_catalog import CATALOG
from orders.models import Order
from orders.tests.factories import make_service, make_service_category
from orders.tests import test_release_flow as release_flow


@override_settings(DEBUG=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class OrderCatalogSnapshotFlowTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def test_reimport_does_not_remove_paid_order_from_pool_or_block_fulfillment(self):
        book = json.loads(CATALOG.read_text(encoding='utf-8'))
        self.service.catalog_key = next(row['catalog_key'] for row in book['items'] if row['is_gift'])
        self.service.save(update_fields=['catalog_key'])
        # This provider is qualified for the specific item before import; keep
        # the existing skill binding when the catalog refreshes its category.
        self.provider.escort_profile.service_items.add(self.service)
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'support_contact_id': self.card.pk, 'dispatch_mode': 'AUTO_GRAB',
            'client_request_id': 'snapshot-pool-order'}, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.assertFalse(order.is_gift_snapshot)
        call_command('seed_club_catalog', apply=True, stdout=io.StringIO())
        self.service.refresh_from_db()
        self.assertTrue(self.service.service_category.is_gift)
        self.login(self.provider)
        from users.qualifications import qualified_order_q, authorized_skill_q
        profile = self.provider.escort_profile
        self.assertFalse(profile.order_acceptance_error())
        self.assertTrue(Order.objects.filter(pk=order.pk).filter(qualified_order_q(profile)).exists(), 'order qualification')
        self.assertTrue(Order.objects.filter(pk=order.pk).filter(authorized_skill_q(profile)).exists(), 'skill binding')
        # Preserve the real pass-tier delay while advancing the test clock.
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(seconds=profile.order_visibility_delay_seconds + 1)):
            pool = self.ok(self.client.get('/api/orders/orders/', {'status': 'pending'}))
            row = next(item for item in pool if item['id'] == order.pk)
            self.assertIn('GRAB', row['allowed_actions'])
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': release_flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': release_flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIn('EVALUATE', detail['allowed_actions'])
        self.assertIn('TIP', detail['allowed_actions'])
        self.ok(self.client.post('/api/orders/evaluate/', {'order_id': order.pk, 'score': 5}, format='json'))
        gift = make_service(name='隔离礼物', price=2100, service_category=make_service_category(is_gift=True))
        result = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/tip/', {
            'gift_service_id': gift.pk, 'quantity': 1,
            'provider_id': self.accounts[self.provider.pk].pk, 'expected_amount': 2100,
            'client_request_id': 'snapshot-source-gift',
        }, format='json'))
        self.assertEqual(Order.objects.get(pk=result['id']).source_order_id, order.pk)
