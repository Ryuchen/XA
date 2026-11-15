"""Verify real order-card writes and provider pool authorization, then roll back."""
from datetime import timedelta
import os
from pathlib import Path
import secrets
import sys
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
from django.utils import timezone
from rest_framework.test import APIClient
from chat.models import ChatMessage
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceItem
from stores.context import resolve_store, store_context
from stores.db import transaction
from users.models import EscortProfile

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
with override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    with store_context(store), transaction.atomic():
        boss = get_user_model().objects.create_user(username='qa-order-card-boss-' + nonce,
            password=secrets.token_urlsafe(24), role='CUSTOMER')
        provider = get_user_model().objects.create_user(username='qa-order-card-provider-' + nonce,
            password=secrets.token_urlsafe(24), role='PROVIDER')
        boss_account = get_or_create_account_for_legacy_user(boss)
        player_account = get_or_create_account_for_legacy_user(provider)
        boss_account.is_phone_verified = True
        boss_account.save(update_fields=['is_phone_verified'])
        player_account.is_phone_verified = True
        player_account.is_openid_bound = True
        player_account.openid = 'qa-card-' + nonce
        player_account.save(update_fields=['is_phone_verified', 'is_openid_bound', 'openid'])
        EscortProfile.objects.create(user=provider, account=player_account, display_name='隔离陪玩',
            is_verified=True, status='AVAILABLE', deposit_mode='EARNINGS', wechat_avatar_legacy_exempt=True)
        service = ServiceItem.objects.create(name='隔离咨询服务-' + nonce, price=10000)
        order = Order.objects.create(customer=boss, customer_account=boss_account, service=service, amount=10000)
        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=2))
        def client(account):
            c = APIClient(HTTP_HOST=host)
            c.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account, auth_method='password_sms')[0])
            return c
        boss_client, player_client = client(boss_account), client(player_account)
        boss_payload = {'content':'隔离老板咨询', 'order_ref':str(order.pk), 'client_message_id':'qa-boss-' + nonce}
        first = boss_client.post('/api/chat/send/', boss_payload, format='json')
        replay = boss_client.post('/api/chat/send/', boss_payload, format='json')
        if first.status_code != 200 or replay.data['data']['id'] != first.data['data']['id']:
            raise RuntimeError('Boss order message not idempotent')
        player_payload = {'content':'隔离陪玩咨询', 'order_ref':order.order_no, 'client_message_id':'qa-player-' + nonce}
        player = player_client.post('/api/chat/send/', player_payload, format='json')
        if player.status_code != 200 or player.data['data']['order_card']['can_open']:
            raise RuntimeError(f'Provider pool card not authorized as expected: {player.status_code}')
        if ChatMessage.objects.filter(related_order=order).count() != 2:
            raise RuntimeError('Order message count incorrect')
        transaction.set_rollback(True)
print(f'{slug}: MySQL boss and provider order cards passed; transaction rolled back')
