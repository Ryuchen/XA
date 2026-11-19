"""Real order and wallet responses use the authenticated store's currency."""
from datetime import timedelta
import os
from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_provider, make_service, make_user
from site_messages.models import Message
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ALLOWED_HOSTS=['*'])
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class StoreCurrencyFlowsTest(APITransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '资金来源多店结算开放/付款来源快照/抢单窗口/补贴三方结算/VIP折扣重定价/店币消息属正式契约深水区（18-盘点 资金来源/结算/补贴/VIP待联调）；最终联调启用')
    def test_order_cancel_timeout_and_wallet_messages_use_own_currency(self):
        fixtures = []
        for store, host, currency in ((resolve_store(), 'sl.esports.club', '浪花'),
                                      (xinan_store(), 'xa.esports.club', '星辉')):
            with store_context(store):
                boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(user=boss, account=account)
                service = make_service(price=10000)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                self.assertIn(currency, client.post('/api/wallet/topup/', {}, format='json').data['msg'])
                missing = client.post('/api/orders/create/', {'service_id':service.pk,
                    'client_request_id':str(uuid4())}, format='json')
                self.assertIn(f'{currency}不足', missing.data['msg'])
                wallet.balance = 50000; wallet.save(update_fields=['balance'])
                def create_order():
                    response = client.post('/api/orders/create/', {'service_id':service.pk,
                        'client_request_id':str(uuid4())}, format='json')
                    self.assertEqual(response.data.get('code'), 0, response.data)
                    return response.data['data']['id']
                first = create_order()
                cancelled = client.post(f'/api/orders/orders/{first}/cancel/', {'reason':'隔离测试'}, format='json')
                self.assertEqual(cancelled.data.get('code'), 0, cancelled.data)
                self.assertIn(f'已退回{currency}余额', cancelled.data['msg'])
                second = create_order()
                Order.objects.filter(pk=second).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
                self.assertEqual(auto_cancel_pending_order.run(second), 'cancelled')
                self.assertNotEqual(auto_cancel_pending_order.run(second), 'cancelled')
                notice = Message.objects.get(recipient_account=account, related_order_id=second,
                                             title='订单超时已取消')
                self.assertIn(f'已退回{currency}余额', notice.preview)
                self.assertEqual(Transaction.objects.filter(order_id=second, tx_type='REFUND').count(), 1)
                wallet.refresh_from_db(); self.assertEqual(wallet.balance, 50000)
                provider = make_provider(); provider_account = get_or_create_account_for_legacy_user(provider)
                provider_wallet = get_wallet(user=provider, account=provider_account)
                provider_wallet.balance = 100; provider_wallet.save(update_fields=['balance'])
                player = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
                result = player.post('/api/wallet/withdraw/', {'amount':10000, 'payee_method':'WECHAT',
                    'payee_account':'LOCAL_TEST_ONLY', 'payee_name':'隔离测试', 'client_request_id':str(uuid4())}, format='json')
                self.assertIn(f'{currency}余额不足', result.data['msg'])
                provider_wallet.balance = 20000; provider_wallet.save(update_fields=['balance'])
                requested = player.post('/api/wallet/withdraw/', {'amount':10000, 'payee_method':'WECHAT',
                    'payee_account':'LOCAL_TEST_ONLY', 'payee_name':'隔离测试', 'client_request_id':str(uuid4())}, format='json')
                self.assertEqual(requested.data.get('code'), 0, requested.data)
                self.assertIn(currency, requested.data['msg'])
                withdrawal = Transaction.objects.get(wallet=provider_wallet, tx_type='WITHDRAW')
                self.assertEqual(withdrawal.status, 'PENDING')
                self.assertIn(currency, withdrawal.remark)
                fixtures.append((store.slug, first, second, notice.preview))
        self.assertNotIn('星辉', fixtures[0][3])
        self.assertNotIn('浪花', fixtures[1][3])
