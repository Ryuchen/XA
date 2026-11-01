"""Native boss reservation controllers against an isolated real HTTP server."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeReservationHTTPTest(LiveServerTestCase):
    def test_boss_creates_provider_confirms_and_boss_converts(self):
        self.run_boss_reservation(False)

    def test_changed_price_requires_new_quote_before_conversion(self):
        self.run_boss_reservation(True)

    def run_boss_reservation(self, price_change):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native controllers')
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(boss, boss_account)
        link_legacy_relations(provider, provider_account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        wallet = make_wallet(boss, balance=50000)
        make_wallet(provider, balance=0)
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(boss_account)
        provider_token, _ = issue_account_tokens(provider_account)
        admin_token = ''
        if price_change:
            staff = make_console_user(['service:view', 'service:edit'])
            staff_account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = staff_account
            staff.admin_membership.save(update_fields=['account'])
            admin_token, _ = issue_account_tokens(staff_account)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-reservation-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'providerId': provider_account.pk,
                              'providerToken': provider_token, 'adminToken': admin_token, 'session': {'token': token, 'refresh': refresh,
                              'user': {'id': str(boss_account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        reservation = Reservation.objects.get(customer_account=boss_account, status='CONVERTED')
        self.assertEqual(reservation.status, 'CONVERTED')
        self.assertEqual((reservation.order.amount, reservation.order.provider_account_id), (12000 if price_change else 10000, provider_account.pk))
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account, status='CANCELLED').count(), 1)
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account, status='CONFIRMED').count(), 1)
        self.assertEqual(Reservation.objects.filter(customer_account=boss_account).count(), 3)
        self.assertEqual(Order.objects.filter(customer_account=boss_account).count(), 1)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 38000 if price_change else 40000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PAY').count(), 1)
