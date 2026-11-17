"""An order number received in chat opens the actual order search."""
import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from PIL import Image

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.grab_config import save_grab_window
from engagement.models import PointFundingLot
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet
from stores.context import resolve_store, store_context


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True)
class AdminChatOrderLookupHTTPTest(LiveServerTestCase):
    def test_message_opens_exact_order_search(self):
        media = tempfile.TemporaryDirectory(prefix='xa-chat-order-test-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        self.enterContext(store_context(resolve_store()))
        boss, staff = make_user(), make_console_user(['chat:view', 'chat:reply', 'order:view'])
        boss_account, staff_account = (get_or_create_account_for_legacy_user(user) for user in (boss, staff))
        for user, account in ((boss, boss_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        PointFundingLot.objects.create(account=boss_account, source_key='chat-order-lookup-prepaid', remaining=50000)
        service = make_service(price=10000)
        save_grab_window(30, 0, lambda *args: None)
        boss_token = issue_account_tokens(boss_account)[0]
        staff_token = issue_account_tokens(staff_account)[0]
        client = APIClient(HTTP_HOST='sl.esports.club')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        created = client.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'chat-order-lookup'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_no = created['data']['order_no']
        session = client.get('/api/chat/session/').data
        self.assertEqual(session['code'], 0, session)
        admin = APIClient(HTTP_HOST='admin.esports.club'); admin.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        self.assertEqual(admin.post(f"/api/admin/chat-sessions/{session['data']['session']['id']}/claim/", {'expected_version': 0}, format='json').data['code'], 0)
        message = client.post('/api/chat/send/', {'content': f'我想咨询订单编号 {order_no} 的服务情况。'}, format='json').data
        self.assertEqual(message['code'], 0, message)
        image_data = io.BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image_data, format='PNG')
        image = SimpleUploadedFile('order.png', image_data.getvalue(), content_type='image/png')
        image_message = client.post('/api/chat/send/', {'content': f'咨询订单 {order_no}', 'image': image}, format='multipart').data
        self.assertEqual(image_message['code'], 0, image_message)
        self.assertEqual(image_message['data']['content_type'], 'IMAGE')
        session_id = session['data']['session']['id']
        listed = admin.get(f'/api/admin/chat-sessions/{session_id}/messages/').data
        self.assertEqual(listed['code'], 0, listed)
        rows = listed['data']['list']
        self.assertTrue(any(row['content_type'] == 'TEXT' and order_no in row['content'] for row in rows))
        image_row = next(row for row in rows if row['content_type'] == 'IMAGE' and order_no in row['content'])
        self.assertTrue(image_row['image_url'])
        orders = admin.get('/api/admin/orders/', {'order_no_exact': order_no}).data
        self.assertEqual(orders['code'], 0, orders)
        self.assertEqual(len(orders['data']['list']), 1)
        self.assertEqual(orders['data']['list'][0]['order_no'], order_no)
