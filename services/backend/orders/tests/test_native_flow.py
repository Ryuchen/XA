"""Business API contracts exercised by the native boss mini-program."""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import GameCategory, Order
from orders.tests.factories import make_user, make_service, make_order, make_provider, make_service_category
from support.models import SupportContactCard
from users.models import CustomerGameProfile
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class NativeBossFlowTest(APITestCase):
    def setUp(self):
        self.user = make_user()
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.wallet = get_wallet(account=self.account, user=self.user)
        self.wallet.balance = 10000
        self.wallet.save(update_fields=['balance'])
        self.service = make_service(price=1250)
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch', 'notify_order_update'):
            patcher = patch(f'orders.views.{name}')
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, **extra):
        return self.client.post('/api/orders/create/', {
            'service_id': self.service.id, 'game_rounds': 2,
            'game_region': '微信区', 'game_nickname': '测试老板',
            'client_request_id': 'native-purchase-001', **extra,
        }, format='json')

    def test_quote_purchase_detail_cancel_wallet_form_one_consistent_flow(self):
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.id, 'game_rounds': 2}, format='json').data['data']
        self.assertEqual(quote['amount'], 2500)
        created = self.create(expected_amount=quote['amount'])
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        detail = self.client.get(f'/api/orders/orders/{order_id}/').data['data']
        self.assertEqual(detail['amount'], quote['amount'])
        self.assertIn('CANCEL', detail['allowed_actions'])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 7500)
        duplicate = self.create(expected_amount=quote['amount'])
        self.assertEqual(duplicate.data['code'], 409)
        self.assertEqual(Order.objects.filter(customer_account=self.account).count(), 1)
        cancelled = self.client.post(f'/api/orders/orders/{order_id}/cancel/', {'reason': '变更计划'}, format='json')
        self.assertEqual(cancelled.data['code'], 0, cancelled.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 10000)
        detail = self.client.get(f'/api/orders/orders/{order_id}/').data['data']
        self.assertEqual(detail['status'], 'CANCELLED')
        self.assertNotIn('CANCEL', detail['allowed_actions'])
        ledger = self.client.get('/api/wallet/transactions/').data['data']
        self.assertEqual(ledger['balance'], 10000)
        self.assertEqual(sum(row['amount'] for row in ledger['transactions']), 0)

    def test_changed_quote_cannot_charge_a_different_amount(self):
        response = self.create(expected_amount=2000)
        self.assertEqual(response.data.get('errorCode'), 'PRICE_CHANGED', response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 10000)
        self.assertFalse(Order.objects.filter(customer_account=self.account).exists())
        self.assertEqual(self.create(expected_amount=2500).data['code'], 0)

    def test_changed_gift_price_does_not_charge(self):
        provider = make_provider()
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        order = make_order(self.user, self.service, provider, status=Order.Status.COMPLETED,
                           customer_account=self.account, provider_account=provider_account,
                           payment_status=Order.PaymentStatus.PAID)
        gift = make_service(price=500, service_category=make_service_category(is_gift=True))
        response = self.client.post(f'/api/orders/orders/{order.id}/tip/', {
            'gift_service_id': gift.pk, 'expected_amount': 400,
            'client_request_id': 'native-gift-001',
        }, format='json')
        self.assertEqual(response.data.get('errorCode'), 'PRICE_CHANGED', response.data)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 10000)

    def test_guest_contact_contains_only_active_business_contact(self):
        SupportContactCard.objects.create(name='停用', is_active=False)
        SupportContactCard.objects.create(name='公开客服', wechat_id='official-help', is_active=True)
        self.client.credentials()
        response = self.client.get('/api/support/public-contact/?order_id=123')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(response.data['data']['name'], '公开客服')
        self.assertNotIn('customer_reference', response.data['data'])
        self.assertNotIn('order_no', response.data['data'])
        self.assertEqual(self.client.get('/api/support/contact-card/?order_id=123').status_code, 401)

    def test_invalid_profile_name_does_not_partially_save_game_profile(self):
        category = GameCategory.objects.create(name='测试游戏')
        profile = CustomerGameProfile.objects.create(account=self.account, user=self.user,
            game_category=category, nickname='原昵称', region='原大区')
        response = self.client.patch('/api/users/me/', {'nickname': 'x' * 51, 'game_profiles': [
            {'game_category': category.id, 'nickname': '不应保存', 'region': '新大区'},
        ]}, format='json')
        self.assertEqual(response.data['code'], 400)
        profile.refresh_from_db()
        self.assertEqual(profile.nickname, '原昵称')

    def test_chat_send_and_read_uses_current_business_account(self):
        response = self.client.post('/api/chat/send/', {'content': '咨询我的订单'}, format='json')
        self.assertEqual(response.data['code'], 0)
        self.assertEqual(self.client.get('/api/chat/session/').data['data']['messages'][0]['content'], '咨询我的订单')
        self.assertEqual(self.client.post('/api/chat/read/', {}, format='json').data['code'], 0)

    def test_chat_rejects_structured_or_oversize_content(self):
        for content in ({'invalid': 1}, ['invalid'], 'x' * 2001):
            with self.subTest(content_type=type(content).__name__):
                self.assertEqual(self.client.post('/api/chat/send/', {'content': content}, format='json').data['code'], 400)

    def test_chat_rejects_fake_image(self):
        file = SimpleUploadedFile('not-an-image.png', b'<script>bad</script>', content_type='image/png')
        response = self.client.post('/api/chat/send/', {'image': file}, format='multipart')
        self.assertEqual(response.data['code'], 400)
