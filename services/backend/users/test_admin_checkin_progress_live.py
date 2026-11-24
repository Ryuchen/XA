"""Customer service sees real boss check-ins without leaving the progress page."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinRecord


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
        rule = clients[2].put('/api/admin/config/checkin', {'makeup_card_spend_required': 18800}, format='json')
        self.assertEqual(rule.data['code'], 0, rule.data)
        calendar = ok(clients[0].get('/api/users/checkin/'))
        self.assertEqual(calendar['makeup_cards'], 1)
        if calendar['makeup_available_days']:
            makeup = ok(clients[0].post('/api/users/checkin/', {'day': calendar['makeup_available_days'][0]}, format='json'))
            self.assertEqual(makeup['makeup_cards'], 0)
        ok(clients[0].post('/api/users/checkin/', {}, format='json'))
        records = CheckinRecord.objects.filter(account=accounts[0])
        self.assertEqual(records.count(), 2 if timezone.localdate().day > 1 else 1)
        self.assertEqual(records.filter(is_makeup=True).count(), 1 if timezone.localdate().day > 1 else 0)
