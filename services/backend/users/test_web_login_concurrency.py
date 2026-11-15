import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.models import ClubAccount
from users.models import WebLoginSession
from users.web_login import sms_digest
from wallet.models import Wallet


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(WECHAT_MINIAPP_APPID='wx-concurrency-test', WECHAT_MINIAPP_SECRET='test-only', WECHAT_MOCK_LOGIN=False)
class WebLoginConcurrencyTests(APITransactionTestCase):
    def ticket(self, openid='wx-concurrent-boss'):
        secret = secrets.token_urlsafe(32)
        row = WebLoginSession.objects.create(poll_token_hash='0' * 64,
            bridge_token_hash=hashlib.sha256(secret.encode()).hexdigest(),
            wechat_openid=openid, phone='13800003333',
            expires_at=timezone.now() + timedelta(minutes=5),
            sms_expires_at=timezone.now() + timedelta(minutes=5))
        row.sms_hash = sms_digest(row, '347981')
        row.save(update_fields=['sms_hash'])
        return {'ticket': row.ticket.hex, 'bridge_token': secret, 'smsCode': '347981'}

    def run_parallel(self, payloads):
        barrier = Barrier(len(payloads))
        def submit(payload):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = APIClient().post('/api/users/web-login/confirm/', payload, format='json')
                return response.status_code, response.data['code']
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            return list(executor.map(submit, payloads))

    def test_same_confirmation_concurrently_creates_one_identity_and_one_wallet(self):
        payload = self.ticket()
        result = self.run_parallel([payload, payload])
        self.assertEqual(result, [(200, 0), (200, 0)])
        account = ClubAccount.objects.get(openid='wx-concurrent-boss')
        self.assertEqual(Wallet.objects.filter(account=account).count(), 1)
        row = WebLoginSession.objects.get(ticket=payload['ticket'])
        self.assertEqual(row.confirmed_account_id, account.pk)
        self.assertEqual(row.sms_attempts, 1)

    def test_distinct_wechat_owners_cannot_merge_through_the_same_phone(self):
        payloads = [self.ticket('wx-first-boss'), self.ticket('wx-second-boss')]
        result = self.run_parallel(payloads)
        self.assertEqual(sorted(status for status, _ in result), [200, 409])
        self.assertEqual(ClubAccount.objects.filter(phone='13800003333', account_type='BOSS').count(), 1)
        self.assertEqual(WebLoginSession.objects.filter(confirmed_account__isnull=False).count(), 1)
