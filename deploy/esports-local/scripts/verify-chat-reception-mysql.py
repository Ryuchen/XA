"""Exercise simultaneous claims on synthetic accounts in one isolated store."""
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
from chat.models import ChatAssignmentEvent, ChatSession
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from stores.context import resolve_store, store_context
from stores.db import transaction

STORES = {'shenglang': ('default', 'xa_test_fronted_acceptance_20261104'),
          'xinan': ('store_b', 'xa_test_store_preview_b_20261104')}
if len(sys.argv) != 2 or sys.argv[1] not in STORES:
    raise SystemExit('Expected store: shenglang or xinan')
slug = sys.argv[1]
alias, expected = STORES[slug]
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
names = [f'qa-chat-claim-{nonce}-{index}' for index in range(2)]
boss_name = 'qa-chat-claim-boss-' + nonce
tokens = []
with override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}):
    try:
        with store_context(store), transaction.atomic():
            role = AdminRole.objects.create(name='隔离客服测试-' + nonce, code='qa-chat-' + nonce,
                                            permissions=['chat:view', 'chat:reply'])
            for name in names:
                user = get_user_model().objects.create_user(username=name, password=secrets.token_urlsafe(24), role='OPERATOR')
                account = get_or_create_account_for_legacy_user(user)
                account.is_phone_verified = True
                account.save(update_fields=['is_phone_verified'])
                membership = AdminMembership.objects.create(user=user, account=account)
                membership.roles.add(role)
                tokens.append(issue_account_tokens(account, auth_method='password_sms')[0])
            boss = ClubAccount.objects.create_account(username=boss_name, account_type='BOSS')
            session = ChatSession.objects.create(account=boss)
            session_id = session.pk
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        results = []
        def claim(token):
            try:
                client = APIClient(HTTP_HOST='admin.esports.club')
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                response = client.post(f'/api/admin/chat-sessions/{session_id}/claim/', {'expected_version': 0}, format='json')
                result = response.status_code
            except Exception as error:
                result = type(error).__name__
            finally:
                connections[alias].close()
            with lock:
                results.append(result)
        workers = [threading.Thread(target=claim, args=(token,)) for token in tokens]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=25)
        if any(worker.is_alive() for worker in workers):
            raise RuntimeError('Claim race did not terminate')
        with store_context(store):
            session = ChatSession.objects.get(pk=session_id)
            if sorted(results) != [200, 409] or session.claim_version != 1 or ChatAssignmentEvent.objects.filter(session=session).count() != 1:
                raise RuntimeError(f'Unexpected claim race: {results}')
        print(f'{slug}: isolated MySQL concurrent claim passed; one assignment event')
    finally:
        with store_context(store):
            ClubAccount.objects.filter(username__in=names + [boss_name]).update(is_active=False, can_login=False)
            get_user_model().objects.filter(username__in=names).update(is_active=False)
