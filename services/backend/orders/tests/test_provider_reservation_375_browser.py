"""Reservation confirmation and conversion through the actual H5 at 375px."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_PROVIDER_RESERVATION_BROWSER') == '1', 'Requires isolated browser build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderReservationBrowserTest(LiveServerTestCase):
    def test_confirmation_to_order_at_375px(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss_account, provider_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        boss_token = issue_account_tokens(boss_account)[0]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = client.post('/api/orders/reservations/', {'provider_account_id': provider_account.pk,
            'service_id': service.pk, 'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        reservation_id = response.data['data']['id']
        boss.nickname = '历史预约老板'; boss.save(update_fields=['nickname'])
        boss_account.nickname = '现行预约老板'; boss_account.save(update_fields=['nickname'])
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-reservation-375-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token,
                'bossId': str(boss_account.pk), 'staffToken': issue_account_tokens(staff_account)[0],
                'providerToken': issue_account_tokens(provider_account)[0], 'providerId': str(provider_account.pk),
                'reservationId': reservation_id, 'bossName': boss_account.nickname}), text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reservation = Reservation.objects.get(pk=reservation_id)
        self.assertEqual((reservation.status, reservation.order.provider_account_id), ('CONVERTED', provider_account.pk))
        self.assertEqual((reservation.order.status, reservation.order.amount), ('GRABBED', 10000))
        wallet = Wallet.objects.get(user=boss)
        self.assertEqual(wallet.balance, 40000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, order=reservation.order, tx_type='PAY').count(), 1)
