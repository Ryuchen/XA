"""Full attendance remains visible to the boss and the customer-service team."""
import calendar
from datetime import datetime, time
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.models import LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinMonthProgress, CheckinRecord
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class AccountOnlyFullAttendanceTest(LiveServerTestCase):
    def test_last_day_awards_message_and_staff_sees_progress(self):
        media = TemporaryDirectory(prefix='xa-full-checkin-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, staff = make_user(), make_provider(), make_console_user(['wallet:recharge', 'checkin:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts): client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk, 'amount':20000,
            'gift_amount':0, 'trade_no':'full-checkin-recharge', 'client_request_id':'full-checkin-recharge',
            'proof_image':screenshot()}, format='multipart'))
        gift = make_service(name='隔离全勤消费', price=18800, commission_rate=20, service_category=make_service_category(is_gift=True))
        order = ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':18800, 'client_request_id':'full-checkin-gift'}, format='json'))
        # Existing historical check-ins are fixture state; only the clock is advanced.
        now = timezone.localdate()
        last = now.replace(day=calendar.monthrange(now.year, now.month)[1])
        Order.objects.filter(pk=order['id']).update(created_at=timezone.make_aware(datetime.combine(last, time(12))))
        LegacyAccountMap.objects.filter(account=accounts[0]).delete()
        CheckinMonthProgress.objects.create(account=accounts[0], year=last.year, month=last.month)
        CheckinRecord.objects.bulk_create([CheckinRecord(account=accounts[0], checkin_date=last.replace(day=day),
            seq_in_month=day, reward_amount=0) for day in range(1, last.day)])
        with patch('users.views.timezone.localdate', return_value=last):
            result = ok(clients[0].post('/api/users/checkin/', {}, format='json'))
            self.assertTrue(result['full_attendance_awarded'])
            self.assertEqual(result['checked_count'], last.day)
            self.assertEqual(ok(clients[0].get('/api/users/checkin/'))['checked_count'], last.day)
            self.assertEqual(clients[0].post('/api/users/checkin/', {}, format='json').data['code'], 400)
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 1200 + result['reward_amount'])
        notice = next(row for row in ok(clients[0].get('/api/messages/'))['messages'] if row['title'] == '月度全勤奖励已获得')
        self.assertEqual(notice['action_url'], '/pages/checkin/index')
        self.assertFalse(any(row['title'] == '月度全勤奖励已获得' for row in ok(clients[1].get('/api/messages/'))['messages']))
        listing = ok(clients[2].get(f'/api/admin/checkin-progress/?year={last.year}&month={last.month}'))
        rows = listing if isinstance(listing, list) else listing.get('results', listing.get('list', []))
        row = next(item for item in rows if item.get('username') == accounts[0].username)
        self.assertEqual((row['checked_count'], row['full_attendance_awarded']), (last.day, True))
        token, refresh = issue_account_tokens(accounts[0])
        root = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run(['node', str(root / 'scripts/native-reservation-notice-live.cjs')], cwd=root,
            input=json.dumps({'base':self.live_server_url, 'token':token, 'refresh':refresh, 'account':accounts[0].pk,
                'message':notice['id'], 'expectedTitle':'月度全勤奖励已获得', 'expectedTarget':'/pages/checkin/index'}),
            text=True, encoding='utf-8', capture_output=True, timeout=30)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
