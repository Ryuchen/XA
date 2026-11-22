from datetime import timedelta
import base64
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from chat.models import ChatMessage
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.consumers import current_chat_payload
from orders.tests.factories import make_console_user, make_order, make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatOrderCardTest(APITestCase):
    def client_for(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def staff(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        client = self.client_for(account)
        client.card_test_account = account
        return client

    def setUp(self):
        self.boss = make_user()
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.other_boss = make_user()
        self.other_account = get_or_create_account_for_legacy_user(self.other_boss)
        self.provider = make_provider()
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        self.provider.escort_profile.account = self.provider_account
        self.provider.escort_profile.save(update_fields=['account'])
        self.order = make_order(self.boss, customer_account=self.boss_account,
            provider=self.provider, provider_account=self.provider_account, status=Order.Status.IN_SERVICE)
        self.boss_client = self.client_for(self.boss_account)
        self.provider_client = self.client_for(self.provider_account)

    def test_card_is_linked_idempotent_and_redacted_without_order_permission(self):
        payload = {'content': '请核实', 'client_message_id': 'order-card-12345', 'order_ref': str(self.order.pk)}
        first = self.boss_client.post('/api/chat/send/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        card = first.data['data']['order_card']
        self.assertEqual((card['id'], card['order_no'], card['status_display']),
                         (self.order.pk, self.order.order_no, self.order.get_status_display()))
        stored = ChatMessage.objects.get(pk=first.data['data']['id'])
        self.assertEqual(stored.related_order_id, self.order.pk)
        replay = self.boss_client.post('/api/chat/send/', payload, format='json')
        self.assertEqual(replay.data['data']['id'], stored.pk)
        second_order = make_order(self.boss, customer_account=self.boss_account)
        changed = self.boss_client.post('/api/chat/send/', {**payload, 'order_ref': str(second_order.pk)}, format='json')
        self.assertEqual(changed.status_code, 409)
        self.order.status = Order.Status.COMPLETED
        self.order.save(update_fields=['status'])
        updated_card = self.boss_client.get('/api/chat/session/').data['data']['messages'][0]['order_card']
        self.assertEqual(updated_card['status'], Order.Status.COMPLETED)
        self.assertEqual(updated_card['status_display'], self.order.get_status_display())
        self.assertEqual(updated_card['snapshot']['status_display'], '服务中')
        session_id = stored.session_id
        staff = self.staff(['chat:view', 'chat:reply'])
        self.assertEqual(staff.post(f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json').status_code, 200)
        card = staff.get(f'/api/admin/chat-sessions/{session_id}/messages/').data['data']['list'][0]['order_card']
        self.assertEqual(card, {'available': False})
        supervisor = self.staff(['chat:view', 'chat:supervise', 'order:view'])
        card = supervisor.get(f'/api/admin/chat-sessions/{session_id}/messages/').data['data']['list'][0]['order_card']
        self.assertEqual((card['id'], card['order_no']), (self.order.pk, self.order.order_no))
        self.assertEqual(card['status'], Order.Status.COMPLETED)
        self.assertEqual(card['snapshot']['status_display'], '服务中')
        event = {'session_id': session_id, 'message': {'id': stored.pk}}
        self.assertEqual(current_chat_payload.func(staff.card_test_account, event, message=True)['message']['order_card'], {'available': False})
        live_card = current_chat_payload.func(supervisor.card_test_account, event, message=True)['message']['order_card']
        self.assertEqual((live_card['id'], live_card['status']), (self.order.pk, Order.Status.COMPLETED))

    def test_cannot_forge_other_boss_or_provider_order(self):
        other = self.client_for(self.other_account)
        response = other.post('/api/chat/send/', {'content': '请求关联', 'order_ref': self.order.order_no}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ChatMessage.objects.filter(sender_account=self.other_account).exists())
        response = self.provider_client.post('/api/chat/send/', {'content': '本人订单', 'order_ref': self.order.order_no}, format='json')
        self.assertEqual(response.data['data']['order_card']['id'], self.order.pk)
        outsider = make_provider()
        outsider_account = get_or_create_account_for_legacy_user(outsider)
        outsider.escort_profile.account = outsider_account
        outsider.escort_profile.save(update_fields=['account'])
        response = self.client_for(outsider_account).post('/api/chat/send/', {'content': '请求关联', 'order_ref': self.order.order_no}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_conflicting_legacy_owner_never_overrides_business_account(self):
        self.order.customer_account = self.other_account
        self.order.save(update_fields=['customer_account'])
        response = self.boss_client.post('/api/chat/send/', {'content': '旧身份不应覆盖', 'order_ref': str(self.order.pk)}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_image_card_replay_keeps_order_reference(self):
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUbulhYGBgYgADAA1VASfFeWKjAAAAAElFTkSuQmCC')
        def payload():
            return {'content': '图片核对', 'order_ref': str(self.order.pk), 'client_message_id': 'order-image-123',
                    'image': SimpleUploadedFile('test.png', image, content_type='image/png')}
        with TemporaryDirectory(prefix='xa-chat-order-card-') as root, override_settings(MEDIA_ROOT=root):
            first = self.boss_client.post('/api/chat/send/', payload(), format='multipart')
            self.assertEqual(first.status_code, 200, first.data)
            replay = self.boss_client.post('/api/chat/send/', payload(), format='multipart')
            self.assertEqual(first.data['data']['id'], replay.data['data']['id'])
            self.assertEqual(replay.data['data']['order_card']['id'], self.order.pk)

    def test_double_and_transferred_provider_has_own_historical_link(self):
        second = make_provider()
        account = get_or_create_account_for_legacy_user(second)
        OrderProvider.objects.create(order=self.order, provider=second, provider_account=account, released_at=timezone.now())
        response = self.client_for(account).post('/api/chat/send/', {'content': '本人转出部分', 'order_ref': self.order.order_no}, format='json')
        self.assertEqual(response.data['data']['order_card']['id'], self.order.pk)

    def test_pool_visibility_rechecked_and_cannot_open_private_detail(self):
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.status = 'AVAILABLE'
        profile.wechat_avatar_legacy_exempt = True
        profile.save(update_fields=['deposit_mode', 'status', 'wechat_avatar_legacy_exempt'])
        self.provider_account.is_openid_bound = True
        self.provider_account.openid = 'test-wechat-card'
        self.provider_account.save(update_fields=['is_openid_bound', 'openid'])
        automatic = make_order(self.other_boss, customer_account=self.other_account)
        Order.objects.filter(pk=automatic.pk).update(created_at=timezone.now()-timedelta(hours=2))
        invalid_ref = self.provider_client.post('/api/chat/send/', {
            'content': '咨询自动抢单', 'order_ref': automatic.order_no, 'client_message_id': 'automatic-without-round',
        }, format='json')
        self.assertEqual(invalid_ref.status_code, 404)
        self.assertFalse(ChatMessage.objects.filter(related_order=automatic).exists())
        pool = make_order(self.other_boss, customer_account=self.other_account, requires_boss_selection=True)
        Order.objects.filter(pk=pool.pk).update(created_at=timezone.now()-timedelta(hours=2))
        listing = self.provider_client.get('/api/orders/orders/?status=pending')
        self.assertIn(pool.pk, [row['id'] for row in listing.data['data']])
        payload = {'content': '咨询待接订单', 'order_ref': pool.order_no, 'client_message_id': 'pool-order-retry'}
        response = self.provider_client.post('/api/chat/send/', payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        original_id = response.data['data']['id']
        self.assertTrue(response.data['data']['order_card']['can_open'])
        self.assertEqual(self.provider_client.get(f'/api/orders/orders/{pool.pk}/').status_code, 200)
        pool.status = Order.Status.CANCELLED
        pool.save(update_fields=['status'])
        message = self.provider_client.get('/api/chat/session/').data['data']['messages'][-1]
        self.assertEqual(message['order_card'], {'available': False})
        self.assertEqual(self.provider_client.get(f'/api/orders/orders/{pool.pk}/').status_code, 404)
        replay = self.provider_client.post('/api/chat/send/', payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['data']['id'], original_id)
        self.assertEqual(replay.data['data']['order_card'], {'available': False})
        status = self.provider_client.get('/api/chat/message-status/', {'client_message_id': 'pool-order-retry'})
        self.assertEqual(status.data['data']['status'], 'FOUND')
        self.assertEqual(status.data['data']['message']['order_card'], {'available': False})
        self.assertEqual(ChatMessage.objects.filter(sender_account=self.provider_account, client_message_id='pool-order-retry').count(), 1)
        numeric = self.provider_client.post('/api/chat/send/', {**payload, 'order_ref': str(pool.pk)}, format='json')
        self.assertEqual((numeric.status_code, numeric.data['data']['id']), (200, original_id))
        changed_content = self.provider_client.post('/api/chat/send/', {**payload, 'content': '不同内容'}, format='json')
        self.assertEqual(changed_content.status_code, 409)
        no_order = self.provider_client.post('/api/chat/send/', {**payload, 'order_ref': ''}, format='json')
        self.assertEqual(no_order.status_code, 409)
        self.assertEqual(ChatMessage.objects.get(pk=original_id).session.unread_support, 1)
        wrong = self.provider_client.post('/api/chat/send/', {**payload, 'order_ref': str(self.order.pk)}, format='json')
        self.assertEqual(wrong.status_code, 409)
        new = self.provider_client.post('/api/chat/send/', {**payload, 'client_message_id': 'pool-order-new'}, format='json')
        self.assertEqual(new.status_code, 404)

    def test_image_retry_after_pool_visibility_loss_is_deduplicated(self):
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'; profile.status = 'AVAILABLE'; profile.wechat_avatar_legacy_exempt = True
        profile.save(update_fields=['deposit_mode', 'status', 'wechat_avatar_legacy_exempt'])
        self.provider_account.openid = 'test-wechat-image-card'; self.provider_account.is_openid_bound = True
        self.provider_account.save(update_fields=['openid', 'is_openid_bound'])
        pool = make_order(self.other_boss, customer_account=self.other_account, requires_boss_selection=True)
        Order.objects.filter(pk=pool.pk).update(created_at=timezone.now()-timedelta(hours=2))
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUbulhYGBgYgADAA1VASfFeWKjAAAAAElFTkSuQmCC')
        def payload():
            return {'content': '图片咨询', 'order_ref': pool.order_no, 'client_message_id': 'pool-image-retry',
                    'image': SimpleUploadedFile('test.png', image, content_type='image/png')}
        with TemporaryDirectory(prefix='xa-chat-order-image-') as root, override_settings(MEDIA_ROOT=root):
            first = self.provider_client.post('/api/chat/send/', payload(), format='multipart')
            self.assertEqual(first.status_code, 200, first.data)
            pool.status = Order.Status.CANCELLED; pool.save(update_fields=['status'])
            replay = self.provider_client.post('/api/chat/send/', payload(), format='multipart')
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(replay.data['data']['id'], first.data['data']['id'])
            self.assertEqual(replay.data['data']['order_card'], {'available': False})
            self.assertEqual(ChatMessage.objects.filter(sender_account=self.provider_account).count(), 1)
