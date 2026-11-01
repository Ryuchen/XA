"""The real provider TSX controllers use the actual API for invitation actions."""
import base64
import json
import subprocess
from datetime import timedelta
from pathlib import Path

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderApplication
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_provider, make_service
from site_messages.models import Message


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class InvitationFrontendHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        release_flow.ReleaseFlowTest.setUp(self)
        self.client = APIClient()

    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def test_actual_message_and_application_pages_through_real_http(self):
        partner = make_provider(display_name='验收队友')
        second = make_provider(display_name='另一发起人')
        for user in (self.provider, partner, second):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'client_request_id': 'invitation-native-http', 'support_contact_id': self.card.pk,
        }, format='json'))
        applications = []
        for applicant in (self.provider, second):
            self.login(applicant)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/grab/", {
                'team_size': 2, 'partner_id': self.accounts[partner.pk].pk,
            }, format='json'))
            applications.append(OrderApplication.objects.get(order_id=order['id'], applicant=self.accounts[applicant.pk]))
        notices = list(Message.objects.filter(recipient_account=self.accounts[partner.pk], related_order_id=order['id']).order_by('id'))
        self.assertEqual(len(notices), 2)
        # An older installed client can still have the earlier order-only link.
        notices[0].action_url = '/pages/orders/index?tab=pending'
        notices[0].save(update_fields=['action_url'])
        single = make_service(price=200, commission_rate=20)
        inputs = {
            'base': self.live_server_url, 'order': order['id'], 'firstApplication': applications[0].pk,
            'secondApplication': applications[1].pk, 'oldMessage': notices[0].pk, 'newMessage': notices[1].pk,
            'partnerToken': issue_account_tokens(self.accounts[partner.pk])[0],
            'bossToken': issue_account_tokens(self.accounts[self.boss.pk])[0],
            'firstApplicant': self.provider.escort_profile.display_name, 'singleService': single.pk, 'support': self.card.pk,
            'image': base64.b64encode(release_flow.screenshot().read()).decode('ascii'),
        }
        script = Path(__file__).resolve().parents[4] / 'scripts' / 'provider-invitation-live.cjs'
        completed = subprocess.run(['node', str(script)], input=json.dumps(inputs), text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout.strip())
        self.assertEqual((result['withdrawn'], result['confirmed'], result['selected']),
                         (applications[0].pk, applications[1].pk, order['id']))
