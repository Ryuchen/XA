"""Race the same synthetic order-linked message in a registered test database."""
import os
from pathlib import Path
import secrets
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import override_settings
from rest_framework.test import APIClient
from chat.models import ChatMessage, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceItem
from stores.context import resolve_store, store_context
from stores.db import transaction

STORES = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104', 'sl.esports.club'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104', 'xa.esports.club')}
if len(sys.argv) != 2 or sys.argv[1] not in STORES:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, expected, host = STORES[slug]
config = settings.DATABASES[alias]
if (config['NAME'], config['HOST'], str(config['PORT'])) != (expected, '127.0.0.1', '13306'):
    raise SystemExit('Database boundary mismatch')
with connections[alias].cursor() as cursor:
    cursor.execute('SELECT DATABASE()')
    if cursor.fetchone()[0] != expected:
        raise SystemExit('Unexpected actual database')
store = resolve_store(slug)
if store.database_alias != alias:
    raise SystemExit('Unexpected store binding')
nonce = uuid.uuid4().hex
username = 'qa-order-card-race-' + nonce
with override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    try:
        with store_context(store), transaction.atomic():
            user = get_user_model().objects.create_user(username=username, password=secrets.token_urlsafe(24), role='CUSTOMER')
            account = get_or_create_account_for_legacy_user(user)
            account.is_phone_verified = True
            account.save(update_fields=['is_phone_verified'])
            service = ServiceItem.objects.create(name='隔离关联并发-' + nonce, price=10000)
            order = Order.objects.create(customer=user, customer_account=account, service=service, amount=10000,
                                         status=Order.Status.CANCELLED, payment_status=Order.PaymentStatus.UNPAID)
            session = ChatSession.objects.create(user=user, account=account)
            token = issue_account_tokens(account, auth_method='password_sms')[0]
            order_id, session_id, service_id = order.pk, session.pk, service.pk
        barrier, mutex, results = threading.Barrier(2), threading.Lock(), []
        payload = {'content': '隔离并发订单咨询', 'order_ref': str(order_id), 'client_message_id': 'qa-order-race-' + nonce}
        def send():
            try:
                client = APIClient(HTTP_HOST=host)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                result = client.post('/api/chat/send/', payload, format='json')
                value = (result.status_code, result.data.get('data', {}).get('id'))
            except Exception as error:
                value = (type(error).__name__, None)
            finally:
                connections[alias].close()
            with mutex:
                results.append(value)
        workers = [threading.Thread(target=send) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Message race did not terminate')
        with store_context(store):
            rows = ChatMessage.objects.filter(session_id=session_id, related_order_id=order_id)
            if len(results) != 2 or any(status != 200 for status, _ in results) or results[0][1] != results[1][1] or rows.count() != 1:
                raise RuntimeError(f'Order-card replay race failed: {results}')
        print(f'{slug}: MySQL concurrent order-card replay passed; one message')
    finally:
        with store_context(store):
            ClubAccount.objects.filter(username=username).update(is_active=False, can_login=False)
            get_user_model().objects.filter(username=username).update(is_active=False)
            if 'service_id' in locals():
                ServiceItem.objects.filter(pk=service_id, name='隔离关联并发-' + nonce).update(is_active=False)
