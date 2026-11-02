"""Dashboard shortcut opens the real staff dispatch queue on a small screen."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@skipUnless(os.environ.get('XA_STAFF_SHORTCUT_BROWSER') == '1', 'Requires isolated browser and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class DashboardStaffShortcutBrowserTest(LiveServerTestCase):
    def test_workbench_opens_actionable_staff_queue(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        if not os.environ.get('XA_ASGI_NETWORK_TEST_DB') or connection.settings_dict['NAME'] != os.environ['XA_ASGI_NETWORK_TEST_DB']:
            self.skipTest('Requires an isolated file-backed database for concurrent browser requests')
        media = tempfile.TemporaryDirectory(prefix='xa-staff-queue-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider(display_name='待派验收陪玩')
        staff = make_console_user(['dashboard:view', 'order:view', 'order:dispatch', 'wallet:recharge'])
        boss_account, staff_account, provider_account = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, provider)]
        for user, account in ((boss, boss_account), (staff, staff_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000)
        provider.escort_profile.service_items.add(service)
        category = ServiceCategory.objects.create(name='待派验收礼物', is_gift=True)
        gift = make_service(name='隔离礼物', price=2100, service_category=category)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        recharge = client.post('/api/admin/wallets/recharge/', {'account_id': boss_account.pk, 'amount': 50000,
            'gift_amount': 0, 'trade_no': 'staff-queue-recharge', 'proof_image': screenshot(),
            'client_request_id': 'staff-queue-recharge'}, format='multipart').data
        self.assertEqual(recharge['code'], 0, recharge)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        orders = {}
        for route in ('STAFF', 'PUBLIC'):
            response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': route,
                'client_request_id': 'dashboard-shortcut-' + route}, format='json').data
            self.assertEqual(response['code'], 0, response)
            orders[route] = response['data']['order_no']
        sent = client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'provider_id': provider_account.pk,
            'quantity': 1, 'expected_amount': 2100, 'client_request_id': 'staff-queue-gift'}, format='json').data
        self.assertEqual(sent['code'], 0, sent)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-dashboard-staff-queue-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(staff_account)[0],
                              'bossToken': issue_account_tokens(boss_account)[0],
                              'providerToken': issue_account_tokens(provider_account)[0],
                              'providerName': '待派验收陪玩', 'staffNo': orders['STAFF'], 'publicNo': orders['PUBLIC']}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(order_no=orders['STAFF'])
        provider_browser = subprocess.run([node, str(repo / 'scripts/provider-aftersale-message-375-live.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(provider_account)[0],
                              'providerId': provider_account.pk, 'orderNo': order.order_no,
                              'quickTeam': True, 'names': ['待派验收陪玩']}),
            text=True, encoding='utf-8', capture_output=True, timeout=60, cwd=repo,
            env={**os.environ, 'XA_PROVIDER_AFTERSALE_BUILD': os.environ.get('XA_STAFF_PROVIDER_ROOT', str(repo / '.tools/provider-gift-origin-20261102'))})
        self.assertEqual(provider_browser.returncode, 0, provider_browser.stdout + provider_browser.stderr)
        if os.environ.get('XA_NATIVE_STAFF_QUEUE') == '1':
            native = subprocess.run([node, str(repo / '.tools/native-recharge-checkout-375-20261102/native-direct-reject-view-375.cjs')],
                input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(boss_account)[0],
                                  'id': boss_account.pk, 'orderId': order.pk, 'status': 'GRABBED',
                                  'providerName': '待派验收陪玩'}),
                text=True, encoding='utf-8', capture_output=True, timeout=45, cwd=repo)
            self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
