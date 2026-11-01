import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_user, make_provider, make_service, make_order
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, WECHAT_MOCK_LOGIN=False)
class NativeGiftRecoveryHTTPTest(LiveServerTestCase):
    def test_lost_response_does_not_duplicate_gift_after_page_reload(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider(display_name='收礼陪玩')
        account, recipient = get_or_create_account_for_legacy_user(boss), get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=boss)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        get_wallet(account=recipient, user=provider)
        category = ServiceCategory.objects.create(name='礼物', is_gift=True)
        gift = make_service(name='测试礼物', price=2100, service_category=category)
        token, refresh = issue_account_tokens(account)
        other_account = get_or_create_account_for_legacy_user(make_user())
        other_token, _ = issue_account_tokens(other_account)
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {'base': self.live_server_url, 'giftId': gift.pk, 'providerId': recipient.pk, 'otherToken': other_token,
                   'session': {'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}}}
        result = subprocess.run([node, str(repo / 'scripts/native-gift-recovery-live.cjs')], input=json.dumps(payload),
                                text=True, capture_output=True, encoding='utf-8', timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        wallet.refresh_from_db()
        # Two deliberately submitted gifts, each recovered without an extra debit.
        self.assertEqual(wallet.balance, 5800)
        self.assertEqual(Order.objects.filter(customer_account=account, is_gift_snapshot=True).count(), 2)

        # Existing-order gifting uses a different original path but the same real receipt.
        source = make_order(boss, provider=provider, customer_account=account, status='COMPLETED', payment_status='PAID')
        original_path = f'/orders/orders/{source.pk}/tip/'
        original = {'items': [{'gift_service_id': gift.pk, 'quantity': 1}], 'provider_id': recipient.pk,
                    'expected_amount': 2100, 'naming_title': ''}
        request_id = 'native-source-order-123'
        self.client.defaults['HTTP_AUTHORIZATION'] = 'Bearer ' + token
        sent = self.client.post('/api' + original_path, data=json.dumps({**original, 'client_request_id': request_id}), content_type='application/json').json()
        self.assertEqual(sent['code'], 0, sent)
        credential = {'path': original_path, 'payload': original, 'client_request_id': request_id}
        lookup = self.client.post('/api/orders/gifts/receipt/', data=json.dumps(credential), content_type='application/json').json()
        self.assertEqual(lookup['data']['fundResult'], sent)
        mismatch = self.client.post('/api/orders/gifts/receipt/', data=json.dumps({**credential, 'path': '/orders/gifts/send/'}), content_type='application/json').json()
        self.assertEqual(mismatch['errorCode'], 'REQUEST_ID_CONFLICT')
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 3700)
        self.assertEqual(Order.objects.filter(customer_account=account, is_gift_snapshot=True).count(), 3)
