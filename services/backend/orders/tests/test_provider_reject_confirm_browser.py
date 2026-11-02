"""Confirm an actual staff order rejection in the provider's mobile browser."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from users.models import EscortProfile
from wallet.models import SystemConfig, Transaction, Wallet


@skipUnless(os.environ.get('XA_PROVIDER_REJECT_BROWSER') == '1', 'Requires isolated browser build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderRejectConfirmBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        media = TemporaryDirectory(prefix='xa-reject-browser-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))

    def test_preset_reason_confirms_before_staff_queue_return(self):
        self.run_rejection(False)

    def test_public_direct_grab_survives_rejection(self):
        self.run_rejection(True)

    def test_public_boss_selection_survives_rejection(self):
        self.run_rejection(True, selection_mode=True)

    def run_rejection(self, public_route, selection_mode=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        replacement = make_provider(display_name='预约替补验收')
        staff = make_console_user(['order:view', 'order:dispatch', 'wallet:recharge'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in ((boss, boss_account), (provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        replacement_account = get_or_create_account_for_legacy_user(replacement)
        link_legacy_relations(replacement, replacement_account)
        EscortProfile.objects.filter(user=replacement).update(account=replacement_account)
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})
        service = make_service(price=10000)
        boss_token, staff_token = [issue_account_tokens(account)[0] for account in (boss_account, staff_account)]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        recharge = client.post('/api/admin/wallets/recharge/', {'account_id': boss_account.pk, 'amount': 50000,
            'gift_amount': 0, 'trade_no': 'reject-browser-recharge', 'proof_image': screenshot(),
            'client_request_id': 'reject-browser-recharge'}, format='multipart').data
        self.assertEqual(recharge['code'], 0, recharge)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC' if public_route else 'STAFF',
            **({'dispatch_mode': 'BOSS_SELECT' if selection_mode else 'AUTO_GRAB'} if public_route else {}),
            'client_request_id': 'reject-confirm-browser-qa'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        order_id, order_no = result['data']['id'], result['data']['order_no']
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + (issue_account_tokens(provider_account)[0] if public_route else staff_token))
        assigned = client.post(f'/api/orders/orders/{order_id}/grab/' if public_route else f'/api/admin/orders/{order_id}/assign/',
            {} if public_route else {'provider_id': provider_account.pk}, format='json').data
        self.assertEqual(assigned['code'], 0, assigned)
        if selection_mode:
            application_id = assigned['data']['my_applications'][0]['id']
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
            selected = client.post(f'/api/orders/orders/{order_id}/applications/{application_id}/select/', {}, format='json').data
            self.assertEqual(selected['code'], 0, selected)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-reject-confirm-375.cjs')],
            input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token, 'staffToken': staff_token,
                'providerToken': issue_account_tokens(provider_account)[0], 'providerId': str(provider_account.pk),
                'secondToken': issue_account_tokens(replacement_account)[0], 'secondId': str(replacement_account.pk),
                'secondName': '预约替补验收',
                'publicRoute': public_route, 'selectionMode': selection_mode, 'orderId': order_id, 'orderNo': order_no,
                'bossId': boss_account.pk, 'nativeCheck': os.environ.get('XA_NATIVE_REJECT_375') == '1'}), text=True, encoding='utf-8', capture_output=True, timeout=150, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.dispatch_route), ('GRABBED', 'PUBLIC' if public_route else 'STAFF'))
        self.assertEqual(order.provider_account_id, replacement_account.pk)
        self.assertEqual(order.provider_name_snapshot, '预约替补验收')
        self.assertEqual(order.providers.get(provider_account=replacement_account, released_at__isnull=True).provider_name_snapshot, '预约替补验收')
        self.assertEqual(Wallet.objects.get(user=boss).balance, 40000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
