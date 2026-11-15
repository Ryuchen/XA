"""A wallet-owning boss remains able to check in without a legacy mapping."""
from tempfile import TemporaryDirectory
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.models import LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from orders.tests.test_release_flow import screenshot
from users.models import CheckinMonthProgress, CheckinRecord
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, REQUIRE_RECHARGE_PROOF=True)
class AccountOnlyCheckinTest(TransactionTestCase):
    def test_spending_checkin_and_staff_progress_with_detached_mapping(self):
        media = TemporaryDirectory(prefix='xa-account-checkin-'); self.addCleanup(media.cleanup)
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
            'gift_amount':0, 'trade_no':'account-checkin-recharge', 'client_request_id':'account-checkin-recharge',
            'proof_image':screenshot()}, format='multipart'))
        gift = make_service(name='隔离签到消费', price=18800, commission_rate=20, service_category=make_service_category(is_gift=True))
        ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id':gift.pk, 'quantity':1,
            'provider_id':accounts[1].pk, 'expected_amount':18800, 'client_request_id':'account-checkin-gift'}, format='json'))
        # Historical account already owns the wallet and order; only the mapping is absent.
        LegacyAccountMap.objects.filter(account=accounts[0]).delete()
        self.assertTrue(ok(clients[0].get('/api/users/checkin/'))['today_eligible'])
        result = ok(clients[0].post('/api/users/checkin/', {}, format='json'))
        self.assertGreater(result['reward_amount'], 0)
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 1200 + result['reward_amount'])
        self.assertEqual(CheckinRecord.objects.filter(account=accounts[0]).count(), 1)
        self.assertEqual(CheckinMonthProgress.objects.filter(account=accounts[0]).count(), 1)
        self.assertEqual(clients[0].post('/api/users/checkin/', {}, format='json').data['code'], 400)
        listing = ok(clients[2].get('/api/admin/checkin-progress/'))
        rows = listing if isinstance(listing, list) else listing.get('results', listing.get('list', []))
        row = next(item for item in rows if item.get('username') == accounts[0].username)
        self.assertEqual(row['checked_count'], 1)
