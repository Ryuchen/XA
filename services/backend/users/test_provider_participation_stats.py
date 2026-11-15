from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderOriginalAttribution, OrderParticipationSegment, OrderProvider
from orders.tests.factories import make_order, make_provider, make_service, make_service_category, make_user


class ProviderParticipationStatsTests(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.players = [make_provider(), make_provider()]
        self.accounts = [get_or_create_account_for_legacy_user(player) for player in self.players]
        self.clients = []
        for player, account in zip(self.players, self.accounts):
            profile = player.escort_profile
            profile.account = account
            profile.save(update_fields=['account'])
            self.clients.append(APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
        self.now = timezone.now()
        self.order = make_order(self.boss, provider=self.players[0], provider_account=self.accounts[0],
            status=Order.Status.IN_SERVICE, original_amount=10000, payment_status='PAID')
        attribution = OrderOriginalAttribution.objects.create(order=self.order, original_amount=10000, status='ACTIVE')
        self.shares, self.segments = [], []
        for index, (player, account) in enumerate(zip(self.players, self.accounts), 1):
            self.shares.append(OrderProvider.objects.create(order=self.order, provider=player, provider_account=account,
                settlement_base=5000, provider_income=4000))
            self.segments.append(OrderParticipationSegment.objects.create(attribution=attribution,
                provider_account=account, slot=index, original_share=5000,
                accepted_at=self.now, started_at=self.now))

    def stats(self, index):
        response = self.clients[index].get('/api/users/provider-stats/')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_double_and_transferred_player_stats_follow_their_own_participation(self):
        second = self.stats(1)
        self.assertEqual((second['today_orders'], second['serving'], second['active_orders']), (1, 1, 1))
        self.segments[0].released_at = self.now
        self.segments[0].save(update_fields=['released_at'])
        self.shares[0].released_at = self.now
        self.shares[0].save(update_fields=['released_at'])
        self.order.provider, self.order.provider_account = self.players[1], self.accounts[1]
        self.order.save(update_fields=['provider', 'provider_account'])
        self.assertEqual(self.stats(0)['serving'], 0)
        self.assertEqual(self.stats(1)['serving'], 1)
        self.order.status, self.order.completed_at = Order.Status.COMPLETED, timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        self.assertEqual((self.stats(0)['total_completed'], self.stats(1)['total_completed']), (1, 1))

    def test_today_is_based_on_each_acceptance_and_counts_same_order_once(self):
        yesterday = self.now - timedelta(days=1)
        self.segments[0].accepted_at = yesterday
        self.segments[0].save(update_fields=['accepted_at'])
        OrderParticipationSegment.objects.create(attribution=self.segments[1].attribution,
            provider_account=self.accounts[1], slot=2, original_share=0,
            accepted_at=self.now, started_at=self.now)
        gift_service = make_service(service_category=make_service_category(is_gift=True))
        make_order(self.boss, provider=self.players[1], provider_account=self.accounts[1], service=gift_service,
            status=Order.Status.COMPLETED, payment_status='PAID', completed_at=self.now)
        self.assertEqual(self.stats(0)['today_orders'], 0)
        second = self.stats(1)
        self.assertEqual((second['today_orders'], second['total_completed']), (1, 0))


from unittest import skipUnless
from django.conf import settings
from django.test import override_settings
from club_accounts.models import ClubAccount, LegacyAccountMap
from stores.context import resolve_store, store_context
from stores.models import Store


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class ProviderParticipationStoreIsolationTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_identical_account_ids_keep_stats_in_their_own_store(self):
        original = resolve_store()
        branch = Store.objects.create(slug='xinan', name='星星', database_alias='store_b',
                                      status=Store.Status.ACTIVE, initialized_at=timezone.now())
        clients = []
        for store, host in ((original, 'sl.esports.club'), (branch, 'xa.esports.club')):
            with store_context(store):
                player = make_provider()
                account = ClubAccount.objects.create_account(pk=777, username='same-provider', account_type='PROVIDER')
                LegacyAccountMap.objects.create(account=account, legacy_user_id=player.pk, legacy_role='PROVIDER')
                player.escort_profile.account = account
                player.escort_profile.save(update_fields=['account'])
                token = issue_account_tokens(account)[0]
                clients.append(APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token))
                if store == branch:
                    boss = make_user()
                    order = make_order(boss, provider=player, provider_account=account,
                        status=Order.Status.IN_SERVICE, original_amount=10000, payment_status='PAID')
                    OrderProvider.objects.create(order=order, provider=player, provider_account=account,
                                                 settlement_base=10000, provider_income=8000)
                    attribution = OrderOriginalAttribution.objects.create(order=order, original_amount=10000, status='ACTIVE')
                    OrderParticipationSegment.objects.create(attribution=attribution, provider_account=account,
                        slot=1, original_share=10000, accepted_at=timezone.now(), started_at=timezone.now())
        first, second = [client.get('/api/users/provider-stats/') for client in clients]
        self.assertEqual((first.data['data']['today_orders'], first.data['data']['serving']), (0, 0))
        self.assertEqual((second.data['data']['today_orders'], second.data['data']['serving']), (1, 1))
        cross = clients[0].get('/api/users/provider-stats/', HTTP_HOST='xa.esports.club')
        self.assertNotEqual(cross.data.get('code'), 0)
