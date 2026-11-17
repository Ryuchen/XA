"""Real transfer shares remain distinguishable through the report APIs."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_provider_aftersale_status as flow
from orders.tests.factories import make_provider
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_PROVIDER_REPORT_ROOT'), 'Requires an isolated provider build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class ProviderTransferReportBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ProviderAfterSaleStatusTest.setUp(self)

    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok

    def test_real_transfer_preserves_individual_report_history(self):
        role = self.staff.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'order:dispatch'})
        role.save(update_fields=['permissions'])
        orders = []
        for percent in (0, 40):
            incoming = make_provider(display_name=f'接替陪玩{percent}')
            account = get_or_create_account_for_legacy_user(incoming)
            incoming.escort_profile.account = account
            incoming.escort_profile.save(update_fields=['account'])
            self.accounts[incoming.pk] = account
            self.tokens[incoming.pk] = issue_account_tokens(account)[0]
            self.login(self.boss)
            order = self.ok(self.client.post('/api/orders/create/', {
                'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                'client_request_id': f'transfer-report-view-{percent}',
            }, format='json'))
            self.login(self.provider)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.image()}, format='multipart'))
            self.login(self.staff)
            self.ok(self.client.post(f"/api/admin/orders/{order['id']}/transfer/", {'transfers': [{
                'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': account.pk,
                'split_type': 'PERCENT', 'split_value': percent, 'penalty_amount': 0,
            }]}, format='json'))
            self.login(self.provider)
            old = self.ok(self.client.get('/api/wallet/reports/', {'order_id': order['id'], 'page': 1}))['list'][0]
            self.assertEqual(old['can_edit'], bool(percent))
            self.login(incoming)
            new = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order['id']}, format='json'))
            self.assertNotEqual(old['id'], new['id'])
            self.assertEqual(old['expected_income'], percent * 80)
            self.assertEqual(new['expected_income'], (100 - percent) * 80)
            orders.append({'id': order['id']})
        for order in orders:
            self.assertEqual(ProviderReport.objects.filter(order_id=order['id']).count(), 2)
