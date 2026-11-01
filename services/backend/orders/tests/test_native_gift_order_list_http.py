import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_order, make_provider, make_service, make_user
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, WECHAT_MOCK_LOGIN=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NativeGiftOrderListHTTPTest(LiveServerTestCase):
    def test_fresh_device_filters_real_gift_orders_and_keeps_status_filter(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider, other = make_user(), make_provider(display_name='收礼陪玩'), make_user()
        account = get_or_create_account_for_legacy_user(boss)
        recipient = get_or_create_account_for_legacy_user(provider)
        other_account = get_or_create_account_for_legacy_user(other)
        provider.escort_profile.account = recipient
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=boss)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        get_wallet(account=recipient, user=provider)
        category = ServiceCategory.objects.create(name='礼物', is_gift=True)
        gift = make_service(name='隔离礼物', price=2100, service_category=category)
        normal = make_order(boss, provider=provider, customer_account=account,
                            status='COMPLETED', payment_status='PAID')
        token, refresh = issue_account_tokens(account)
        other_token, other_refresh = issue_account_tokens(other_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        sent = client.post('/api/orders/gifts/send/', {
            'items': [{'gift_service_id': gift.pk, 'quantity': 1}],
            'provider_id': recipient.pk, 'expected_amount': 2100,
            'naming_title': '', 'client_request_id': 'gift-list-fresh-device-123',
        }, format='json')
        self.assertEqual(sent.data['code'], 0, sent.data)
        gift_order_id = sent.data['data']['orders'][0]['id']
        self.assertEqual(Order.objects.get(pk=gift_order_id).status, Order.Status.COMPLETED)
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {'base': self.live_server_url, 'giftId': gift_order_id, 'normalId': normal.pk,
                   'session': {'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}},
                   'otherSession': {'token': other_token, 'refresh': other_refresh, 'user': {'id': str(other_account.pk), 'role': 'customer'}}}
        result = subprocess.run([node, str(repo / 'scripts/native-gift-order-list-live.cjs')],
                                input=json.dumps(payload), text=True, capture_output=True,
                                encoding='utf-8', timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 7900)
        self.assertEqual(Order.objects.filter(customer_account=account, is_gift_snapshot=True).count(), 1)
