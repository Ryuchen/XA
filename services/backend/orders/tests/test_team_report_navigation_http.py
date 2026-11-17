"""Real two-provider report-to-order handoff against isolated HTTP."""
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False, CORS_ALLOW_ALL_ORIGINS=True,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TeamReportNavigationHTTPTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19167
    def setUp(self):
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    finish = flow.ReleaseFlowTest.finish

    def test_evidence_links_focus_correct_team_order_and_reach_review(self):
        self.ok(self.recharge())
        self.login(self.boss)
        old = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'team-navigation-old',
        }, format='json'))
        self.finish(Order.objects.get(pk=old['id']))
        second = make_provider(display_name='搭档')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge(trade_no='receipt-2', request_id='recharge-2'))
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.login(self.boss)
        team = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-navigation-target',
        }, format='json'))
        self.login(self.provider)
        initial = self.ok(self.client.post('/api/wallet/reports/', {'order_id': team['id']}, format='json'))
        self.assertIn('ENTRY', initial['evidence_upload_block_reasons'])
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f"/api/orders/orders/{team['id']}/start/",
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f"/api/orders/orders/{team['id']}/complete/",
                                     {'completion_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{team['id']}/"))['status'], 'COMPLETED')
        self.assertEqual(Order.objects.get(pk=team['id']).status, 'COMPLETED')
        for provider in (self.provider, second):
            self.login(provider)
            report = ProviderReport.objects.get(order_id=team['id'], provider=provider)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.assertEqual(self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))['status'], 'PENDING')
        self.login(self.admin)
        for report in ProviderReport.objects.filter(order_id=team['id']):
            self.assertEqual(self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))['status'], 'APPROVED')
        order = Order.objects.get(pk=team['id'])
        self.assertIsNotNone(order.consumption_ready_at)
        self.assertIsNone(order.settled_at)
