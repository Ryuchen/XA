"""An order number received in chat opens the actual order search."""
import json
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from PIL import Image

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminChatOrderLookupHTTPTest(LiveServerTestCase):
    def test_message_opens_exact_order_search(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        media = tempfile.TemporaryDirectory(prefix='xa-chat-order-test-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff = make_user(), make_console_user(['chat:view', 'chat:reply', 'order:view'])
        boss_account, staff_account = (get_or_create_account_for_legacy_user(user) for user in (boss, staff))
        for user, account in ((boss, boss_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        boss_token = issue_account_tokens(boss_account)[0]
        staff_token = issue_account_tokens(staff_account)[0]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = client.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'chat-order-lookup'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_no = created['data']['order_no']
        session = client.get('/api/chat/session/').data
        self.assertEqual(session['code'], 0, session)
        message = client.post('/api/chat/send/', {'content': f'我想咨询订单编号 {order_no} 的服务情况。'}, format='json').data
        self.assertEqual(message['code'], 0, message)
        image_data = io.BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image_data, format='PNG')
        image = SimpleUploadedFile('order.png', image_data.getvalue(), content_type='image/png')
        image_message = client.post('/api/chat/send/', {'content': f'咨询订单 {order_no}', 'image': image}, format='multipart').data
        self.assertEqual(image_message['code'], 0, image_message)
        self.assertEqual(image_message['data']['content_type'], 'IMAGE')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-chat-order-lookup-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'staffToken': staff_token,
                'sessionId': session['data']['session']['id'], 'orderNo': order_no}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        if os.environ.get('XA_ADMIN_CHAT_375') == '1':
            result = subprocess.run([node, str(repo / 'scripts/admin-chat-order-mobile-live.cjs')],
                input=json.dumps({'api': self.live_server_url, 'token': staff_token,
                    'bossName': boss_account.nickname or boss_account.username, 'orderNo': order_no}),
                text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
