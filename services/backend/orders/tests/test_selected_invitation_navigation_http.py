"""Exercise the selected invitation page using a real order and HTTP API."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import ClubOperationConfig, PointFundingLot
from orders.models import Order, OrderApplication, OrderStatusLog
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.test_voice_recordings import audio_file
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, CORS_ALLOW_ALL_ORIGINS=True)
class SelectedInvitationNavigationTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19166 if os.environ.get('XA_CANCELLED_TEAM_BROWSER') == '1' else 0

    def setUp(self):
        self.node = shutil.which('node')
        if not self.node:
            self.skipTest('Node is required for the provider page')
        media = TemporaryDirectory(prefix='xa-selection-navigation-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.client = APIClient()

    def checkout(self, request_id, count=1):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        ClubOperationConfig.objects.get_or_create(pk=1)
        PointFundingLot.objects.create(account=boss_account, source_key=request_id,
            kind=PointFundingLot.Kind.PREPAID, remaining=wallet.balance)
        service = make_service(price=10000, billing_provider_count=count, fulfillment_provider_count=count)
        boss_token = issue_account_tokens(boss_account)[0]
        self.auth(boss_token)
        result = self.client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': request_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(order.required_provider_count, count)
        self.assertEqual(order.amount, 10000 * count)
        return order, boss_token

    def provider(self, service):
        provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                 pass_expires_at=timezone.now() + timedelta(days=1))
        account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, account)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        return account, issue_account_tokens(account)[0]

    def auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def upload_recording(self, token):
        self.auth(token)
        result = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '报名录音',
            'client_request_id': uuid4().hex}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data['data']['id']

    def verify_http(self, key, **context):
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, key: json.dumps({'base': self.live_server_url, **context})}
        result = subprocess.run([self.node, '--test', str(repo / 'scripts/fronted-application-contracts.test.mjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_cancelled_team_application_remains_readable_for_both_members(self):
        order, boss_token = self.checkout('cancelled-team-navigation', count=2)
        applicant, applicant_token = self.provider(order.service)
        partner, partner_token = self.provider(order.service)
        _, outsider_token = self.provider(order.service)
        recording_id = self.upload_recording(applicant_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
            {'team_size': 2, 'partner_id': partner.pk, 'recording_id': recording_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        application = OrderApplication.objects.get(order=order, applicant=applicant)
        self.verify_http('XA_TEAM_CANCELLED_HTTP', bossToken=boss_token, tokens=[applicant_token, partner_token],
                         outsiderToken=outsider_token, orderId=order.pk, applicationId=application.pk)
        self.assertEqual(list(order.transactions.order_by('pk').values_list('tx_type', 'amount')),
                         [(Transaction.TxType.PAY, -20000), (Transaction.TxType.REFUND, 20000)])

    def test_previous_selection_page_shows_historical_rejected_order_back_in_pool(self):
        order, boss_token = self.checkout('rejected-selection-navigation')
        applicant, provider_token = self.provider(order.service)
        recording_id = self.upload_recording(provider_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/', {'recording_id': recording_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        application = OrderApplication.objects.get(order=order, applicant=applicant)
        self.auth(boss_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.auth(provider_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason': '时间冲突'}, format='json')
        self.assertEqual(result.data.get('errorCode'), 'ORDER_SELF_REJECTION_DISABLED', result.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), (Order.Status.GRABBED, applicant.pk))

        # Revisit a saved return-to-pool record from before self-rejection was disabled.
        now = timezone.now()
        order.providers.update(released_at=now, settlement_base=0, provider_income=0, commission_fixed=0)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PENDING, provider=None, provider_account=None,
            provider_name_snapshot='', grabbed_at=None, reject_count=1, auto_cancel_at=now + timedelta(minutes=30))
        OrderStatusLog.objects.create(order=order, action=OrderStatusLog.Action.REJECT, operator_account=applicant,
            from_status=Order.Status.GRABBED, to_status=Order.Status.PENDING, reason='时间冲突')
        self.verify_http('XA_REJECTED_SELECTION_HTTP', providerToken=provider_token, orderId=order.pk,
                         applicationId=application.pk)
        self.assertEqual(list(order.transactions.values_list('tx_type', 'amount')), [(Transaction.TxType.PAY, -10000)])

    def test_selected_team_member_can_open_the_order(self):
        order, boss_token = self.checkout('selected-invitation-navigation', count=2)
        applicant, applicant_token = self.provider(order.service)
        partner, partner_token = self.provider(order.service)
        recording_id = self.upload_recording(applicant_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/grab/',
            {'team_size': 2, 'partner_id': partner.pk, 'recording_id': recording_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        invitation = OrderApplication.objects.get(order=order, applicant=applicant, status='PENDING')
        partner_recording_id = self.upload_recording(partner_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/applications/{invitation.pk}/action/',
            {'action': 'CONFIRM_TEAM', 'recording_id': partner_recording_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.auth(boss_token)
        result = self.client.post(f'/api/orders/orders/{order.pk}/applications/{invitation.pk}/select/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.verify_http('XA_TEAM_SELECTED_HTTP', partnerToken=partner_token, orderId=order.pk,
                         applicationId=invitation.pk)
