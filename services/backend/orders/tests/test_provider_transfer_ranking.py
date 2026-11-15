"""Default-config ranking from real single-player transfer and completion."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
import os
from unittest.mock import patch
from django.test import override_settings
from django.conf import settings
from django.utils import timezone
from django.db import close_old_connections, connection
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from orders.models import Order
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderTransferRankingTest(APITestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    replacement = fixture.PrestartHandoffTest.replacement
    def transfer(self, order_id, old, new, **extra):
        from orders.models import OrderWorkOriginalShare
        share = OrderWorkOriginalShare.objects.filter(stint__order_id=order_id,
            stint__account=self.accounts[old.pk], stint__released_at__isnull=True).first()
        if share and 'original_share' not in extra:
            extra['original_share'] = {'retained': 0, 'transferred': share.amount,
                                       'agreement': '客服确认原价份额'}
        return fixture.PrestartHandoffTest.transfer(self, order_id, old, new, **extra)

    def new_order(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk], 'client_request_id': 'ranking-transfer'}, format='json'))
        return Order.objects.get(pk=data['id'])

    def ranking(self):
        self.login(self.boss)
        data = self.ok(self.client.get('/api/orders/rankings/?period=month'))
        return {row['provider_account_id']: row for row in data['order_rank']}, data['order_rank_complete']

    def enter_transfer(self, order, user, request_id):
        self.login(user)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        stint = detail['my_pending_work_stint']['id']
        self.assertIn('START_PARTICIPATION', detail['allowed_actions'])
        self.assertNotIn('COMPLETE', detail['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
            'work_stint_id': stint, 'entry_image': flow.screenshot(), 'client_request_id': request_id}, format='multipart'))
        replay = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
            'work_stint_id': stint, 'entry_image': flow.screenshot(), 'client_request_id': request_id}, format='multipart'))
        self.assertTrue(replay['request_replayed'])

    def test_actual_transfer_and_completion_count_both_players(self):
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement))
        first, complete = self.ranking()
        self.assertEqual(first[self.accounts[self.provider.pk].pk]['order_count'], 1)
        self.assertTrue(complete)
        self.enter_transfer(order, replacement, 'first-entry')
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        rows, complete = self.ranking()
        self.assertEqual(rows[self.accounts[self.provider.pk].pk]['order_count'], 1)
        self.assertEqual(rows[self.accounts[replacement.pk].pk]['order_count'], 1)
        self.assertTrue(complete)
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '售后全额退款',
            'plan_token': preview['plan_token'], 'client_request_id': 'transfer-ranking-refund'}, format='json'))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'REFUNDED')
        rows, complete = self.ranking()
        self.assertEqual(rows[self.accounts[self.provider.pk].pk]['order_count'], 1)
        self.assertEqual(rows[self.accounts[replacement.pk].pk]['order_count'], 1)
        self.assertTrue(complete)

    def test_return_counts_each_actual_player_once(self):
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.enter_transfer(order, replacement, 'middle-entry')
        self.ok(self.transfer(order.pk, replacement, self.provider))
        from orders.models import OrderWorkStint
        before = list(OrderWorkStint.objects.filter(order=order).order_by('pk').values_list(
            'pk', 'account_id', 'started_at', 'released_at'))
        self.assertNotEqual(self.transfer(order.pk, self.provider, replacement).data['code'], 0)
        self.assertEqual(list(OrderWorkStint.objects.filter(order=order).order_by('pk').values_list(
            'pk', 'account_id', 'started_at', 'released_at')), before)
        self.enter_transfer(order, self.provider, 'return-entry')
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        rows, complete = self.ranking()
        self.assertEqual(rows[self.accounts[self.provider.pk].pk]['order_count'], 1)
        self.assertEqual(rows[self.accounts[replacement.pk].pk]['order_count'], 1)
        self.assertTrue(complete)

    def test_unstarted_transfer_does_not_count_and_entry_is_owned(self):
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.login(replacement)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        stint_id = detail['my_pending_work_stint']['id']
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart').data['code'], 0)
        self.login(self.provider)
        denied = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'work_stint_id': stint_id, 'entry_image': flow.screenshot()}, format='multipart')
        self.assertNotEqual(denied.data['code'], 0)
        self.ok(self.transfer(order.pk, replacement, self.provider))
        self.login(replacement)
        stale = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'work_stint_id': stint_id, 'entry_image': flow.screenshot()}, format='multipart')
        self.assertNotEqual(stale.data['code'], 0)
        self.enter_transfer(order, self.provider, 'unstarted-return')
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        rows, complete = self.ranking()
        self.assertEqual(rows[self.accounts[self.provider.pk].pk]['order_count'], 1)
        self.assertNotIn(self.accounts[replacement.pk].pk, rows)
        self.assertTrue(complete)

    def test_failure_after_work_stint_write_rolls_back_transfer(self):
        from orders.models import OrderTransferSeatEvent, OrderWorkStint
        from orders.state_machine import IllegalTransitionError
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        with patch('orders.original_attribution.service_transferred', side_effect=IllegalTransitionError('模拟后续失败')):
            result = self.transfer(order.pk, self.provider, replacement)
        self.assertNotEqual(result.data['code'], 0)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertFalse(OrderTransferSeatEvent.objects.filter(seat__order=order).exists())
        stints = list(OrderWorkStint.objects.filter(order=order))
        self.assertEqual(len(stints), 1)
        self.assertEqual(stints[0].account_id, self.accounts[self.provider.pk].pk)
        self.assertIsNotNone(stints[0].started_at)
        self.assertIsNone(stints[0].released_at)

    def test_transfer_and_completion_use_each_persons_day(self):
        replacement = self.replacement()
        now = timezone.now()
        yesterday = (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        with patch('django.utils.timezone.now', return_value=yesterday):
            order = self.new_order()
            self.login(self.provider)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': flow.screenshot()}, format='multipart'))
        with patch('django.utils.timezone.now', return_value=yesterday + timedelta(hours=1)):
            self.ok(self.transfer(order.pk, self.provider, replacement))
            self.login(self.boss)
            rows = self.ok(self.client.get('/api/orders/rankings/?period=day'))['order_rank']
            self.assertEqual([row['provider_account_id'] for row in rows], [self.accounts[self.provider.pk].pk])
        with patch('django.utils.timezone.now', return_value=now):
            self.login(self.boss)
            before = self.ok(self.client.get('/api/orders/rankings/?period=day'))
            self.assertEqual(before['order_rank'], [])
            self.assertTrue(before['order_rank_complete'])
            self.enter_transfer(order, replacement, 'day-entry')
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': flow.screenshot()}, format='multipart'))
            self.login(self.boss)
            rows = self.ok(self.client.get('/api/orders/rankings/?period=day'))['order_rank']
            self.assertEqual([row['provider_account_id'] for row in rows], [self.accounts[replacement.pk].pk])


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False, ALLOWED_HOSTS=['*'],
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderTransferRankingStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    login = ProviderTransferRankingTest.login
    ok = ProviderTransferRankingTest.ok
    recharge = ProviderTransferRankingTest.recharge
    new_order = ProviderTransferRankingTest.new_order
    replacement = ProviderTransferRankingTest.replacement
    transfer = ProviderTransferRankingTest.transfer
    ranking = ProviderTransferRankingTest.ranking
    enter_transfer = ProviderTransferRankingTest.enter_transfer

    def test_actual_transfer_and_full_refund_in_both_stores(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.PrestartHandoffTest.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                ProviderTransferRankingTest.test_actual_transfer_and_completion_count_both_players(self)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class UnknownTeamTransferRankingTest(APITestCase):
    from orders.tests.test_provider_completion_ranking import ProviderCompletionRankingTest as setup_fixture
    setUp = setup_fixture.setUp
    auth = setup_fixture.auth
    login = setup_fixture.auth
    create_specified = setup_fixture.create_specified
    ok = fixture.PrestartHandoffTest.ok
    enter_transfer = ProviderTransferRankingTest.enter_transfer

    def test_team_two_transfers_record_each_real_entry(self):
        order = self.create_specified(self.providers[:2])
        for provider in self.providers[:2]:
            self.auth(provider)
            result = self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': flow.screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '客服协调更换',
            'transfers': [{'old_provider_id': self.accounts[self.providers[0].pk].pk,
                           'new_provider_id': self.accounts[self.providers[2].pk].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.enter_transfer(order, self.providers[2], 'team-middle-entry')
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '客服协调转回',
            'transfers': [{'old_provider_id': self.accounts[self.providers[2].pk].pk,
                           'new_provider_id': self.accounts[self.providers[0].pk].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        from orders.models import OrderWorkStint
        before = OrderWorkStint.objects.filter(order=order).count()
        denied = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '超出席位上限',
            'transfers': [{'old_provider_id': self.accounts[self.providers[0].pk].pk,
                           'new_provider_id': self.accounts[self.providers[2].pk].pk}]}, format='json')
        self.assertNotEqual(denied.data['code'], 0)
        self.assertEqual(OrderWorkStint.objects.filter(order=order).count(), before)
        self.enter_transfer(order, self.providers[0], 'team-return-entry')
        for provider in (self.providers[1], self.providers[0]):
            self.auth(provider)
            result = self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': flow.screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.boss)
        result = self.client.get('/api/orders/rankings/?period=month')
        self.assertEqual(result.data['code'], 0, result.data)
        data = result.data['data']
        rows = {row['provider_account_id']: row['order_count'] for row in data['order_rank']}
        self.assertEqual([rows[self.accounts[provider.pk].pk] for provider in self.providers], [1, 1, 1])
        self.assertTrue(data['order_rank_complete'])


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class TeamTransferRankingStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    auth = UnknownTeamTransferRankingTest.auth
    login = UnknownTeamTransferRankingTest.login
    ok = UnknownTeamTransferRankingTest.ok
    create_specified = UnknownTeamTransferRankingTest.create_specified
    enter_transfer = UnknownTeamTransferRankingTest.enter_transfer

    def test_team_two_transfers_in_each_store(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                UnknownTeamTransferRankingTest.setup_fixture.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                UnknownTeamTransferRankingTest.test_team_two_transfers_record_each_real_entry(self)


@skipUnless(connection.vendor == 'mysql' and os.environ.get('DJANGO_SETTINGS_MODULE') ==
            'config.ordinary_followup_mysql_test_settings', 'requires disposable MySQL')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ProviderEntryRaceTest(APITransactionTestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = ProviderTransferRankingTest.login
    ok = ProviderTransferRankingTest.ok
    recharge = ProviderTransferRankingTest.recharge
    new_order = ProviderTransferRankingTest.new_order
    replacement = ProviderTransferRankingTest.replacement
    transfer = ProviderTransferRankingTest.transfer

    def test_competing_entry_requests_commit_one_stint(self):
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderWorkStint
        from stores.context import resolve_store, store_context
        replacement = self.replacement()
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement))
        account = self.accounts[replacement.pk]
        stint = OrderWorkStint.objects.get(order=order, account=account, released_at__isnull=True)
        token = issue_account_tokens(account)[0]
        store = resolve_store()
        barrier = Barrier(2)

        def submit(index):
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                    barrier.wait(timeout=15)
                    result = client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
                        'work_stint_id': stint.pk, 'entry_image': flow.screenshot(),
                        'client_request_id': f'entry-race-{index}'}, format='multipart')
                    return result.data['code']
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(submit, (1, 2)))
        self.assertEqual(codes, [0, 0])
        stint.refresh_from_db()
        self.assertIsNotNone(stint.started_at)
        self.assertEqual(OrderWorkStint.objects.filter(order=order, account=account).count(), 1)
