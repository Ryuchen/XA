"""Customer service sees real boss check-ins without leaving the progress page."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinRecord
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_ADMIN_CHECKIN_ROOT'), 'Requires isolated admin build and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class AdminCheckinProgressLiveTest(LiveServerTestCase):
    def test_staff_page_updates_after_boss_checkin(self):
        media = TemporaryDirectory(prefix='xa-admin-checkin-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider, staff = make_user(nickname='实时签到老板'), make_provider(), make_console_user(['wallet:recharge','checkin:view','checkin:edit'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        clients = [APIClient() for _ in accounts]
        for client, account in zip(clients, accounts): client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data); return response.data['data']
        ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id':accounts[0].pk, 'amount':20000,
            'gift_amount':0, 'trade_no':'admin-checkin-recharge', 'client_request_id':'admin-checkin-recharge',
            'proof_image':screenshot()}, format='multipart'))
        gift = make_service(name='隔离客服签到消费', price=18800, commission_rate=20, service_category=make_service_category(is_gift=True))
        ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':18800, 'client_request_id':'admin-checkin-gift'}, format='json'))
        self.assertTrue(ok(clients[0].get('/api/users/checkin/'))['today_eligible'])
        with isolated_redis_asgi() as (base, ws_base):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-checkin-progress-live.cjs')], cwd=root,
                input=json.dumps({'base':base, 'wsBase':ws_base, 'root':os.environ['XA_ADMIN_CHECKIN_ROOT'],
                    'bossToken':issue_account_tokens(accounts[0])[0], 'staffToken':issue_account_tokens(accounts[2])[0],
                    'username':accounts[0].username}), text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = CheckinRecord.objects.filter(account=accounts[0])
        self.assertEqual(records.count(), 2 if timezone.localdate().day > 1 else 1)
        self.assertEqual(records.filter(is_makeup=True).count(), 1 if timezone.localdate().day > 1 else 0)
