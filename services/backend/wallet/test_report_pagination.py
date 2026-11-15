"""Long provider report histories remain navigable and owner-scoped."""
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_order, make_provider, make_user
from wallet.models import ProviderReport
from orders.models import OrderProvider
from django.utils import timezone


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReportPaginationTest(TransactionTestCase):
    def test_missing_migrated_points_receipt_table_blocks_unsafe_report_choices(self):
        from unittest.mock import patch
        from django.db import DatabaseError
        from django.db.migrations.recorder import MigrationRecorder
        from orders.models import OrderPointsRedemption
        from orders.points_service_payment import needs_integrated_processing, display_has_points
        from stores.db import connection
        provider, boss = make_provider(), make_user()
        account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, account)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        order = make_order(boss, provider=provider, provider_account=account, status='GRABBED')
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        MigrationRecorder.Migration.objects.get_or_create(app='orders', name='0117_orderpointsredemption')
        tables = [name for name in connection.introspection.table_names()
                  if name != OrderPointsRedemption._meta.db_table]
        with patch.object(connection.introspection, 'table_names', return_value=tables):
            with self.assertRaises(DatabaseError):
                needs_integrated_processing(order)
            self.assertTrue(display_has_points(order))
            response = client.get('/api/wallet/report-order-choices/')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['data']['list'], [])

    def test_eligible_order_choices_are_paginated_searchable_and_create_real_report(self):
        provider, other, boss = make_provider(), make_provider(), make_user()
        account, other_account = [get_or_create_account_for_legacy_user(user) for user in (provider, other)]
        for user, owner in ((provider, account), (other, other_account)):
            link_legacy_relations(user, owner)
            user.escort_profile.account = owner; user.escort_profile.save(update_fields=['account'])
        orders = [make_order(boss, provider=provider, provider_account=account, status='GRABBED') for _ in range(25)]
        existing = orders[0]
        ProviderReport.objects.create(provider=provider, provider_account=account, order=existing, game_name='已报单', amount=100)
        other_order = make_order(boss, provider=other, provider_account=other_account, status='GRABBED')
        released = make_order(boss, provider=provider, provider_account=account, status='IN_SERVICE')
        OrderProvider.objects.create(order=released, provider=provider, provider_account=account, released_at=timezone.now(), settlement_base=0)
        retained = make_order(boss, provider=other, provider_account=other_account, status='IN_SERVICE')
        OrderProvider.objects.create(order=retained, provider=provider, provider_account=account, released_at=timezone.now(), settlement_base=100)
        gift = make_order(boss, provider=provider, provider_account=account, status='COMPLETED')
        type(gift).objects.filter(pk=gift.pk).update(is_gift_snapshot=True)
        partial = make_order(boss, provider=provider, provider_account=account, status='COMPLETED', payment_status='PARTIALLY_REFUNDED')
        unpaid = make_order(boss, provider=provider, provider_account=account, status='GRABBED', payment_status='UNPAID')
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def choices(query=''):
            response = client.get('/api/wallet/report-order-choices/?' + query)
            self.assertEqual(response.status_code, 200); self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        first, second = choices('page=1&page_size=20'), choices('page=2&page_size=20')
        self.assertEqual((first['total'], len(first['list']), len(second['list'])), (25, 20, 5))
        ids = {row['id'] for row in first['list'] + second['list']}
        self.assertEqual(len(ids), 25)
        self.assertTrue({existing.pk, other_order.pk, released.pk, gift.pk, partial.pk, unpaid.pk}.isdisjoint(ids))
        self.assertIn(retained.pk, ids)
        found = choices('q=' + orders[1].order_no)
        self.assertEqual([row['id'] for row in found['list']], [orders[1].pk])
        from identifiers.services import ensure_entity_number
        public_no = ensure_entity_number(orders[1])
        self.assertEqual(found['list'][0]['entity_no'], public_no)
        self.assertEqual([row['id'] for row in choices('q=' + public_no)['list']], [orders[1].pk])
        response = client.post('/api/wallet/reports/', {'order_id': orders[1].pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['order_entity_no'], public_no)
        self.assertTrue(ProviderReport.objects.filter(provider_account=account, order=orders[1]).exists())
        self.assertEqual(choices('q=' + orders[1].order_no)['total'], 0)

    def test_pages_focus_and_existing_order_ids_preserve_legacy_contract(self):
        provider, other, boss = make_provider(), make_provider(), make_user()
        account, other_account = [get_or_create_account_for_legacy_user(user) for user in (provider, other)]
        link_legacy_relations(provider, account); link_legacy_relations(other, other_account)
        for user, owner in ((provider,account),(other,other_account)):
            user.escort_profile.account=owner; user.escort_profile.save(update_fields=['account'])
        old_order = make_order(boss, provider=provider, provider_account=account, status='COMPLETED')
        old = ProviderReport.objects.create(provider=provider, provider_account=account, order=old_order,
            game_name='历史关联订单', amount=100, status='DRAFT')
        for index in range(25):
            ProviderReport.objects.create(provider=provider, provider_account=account, game_name=f'历史记录{index}', amount=100, status='DRAFT')
        other_order = make_order(boss, provider=other, provider_account=other_account, status='COMPLETED')
        ProviderReport.objects.create(provider=other, provider_account=other_account, order=other_order,
            game_name='其他陪玩', amount=100, status='DRAFT')
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        def data(path):
            response=client.get(path); self.assertEqual(response.status_code,200,response.data)
            self.assertEqual(response.data['code'],0,response.data); return response.data['data']
        legacy = data('/api/wallet/reports/')
        self.assertIsInstance(legacy,list); self.assertEqual(len(legacy),26)
        first = data('/api/wallet/reports/?page=1&page_size=20')
        second = data('/api/wallet/reports/?page=2&page_size=20')
        self.assertEqual((first['total'],len(first['list']),len(second['list'])),(26,20,6))
        self.assertEqual(len({row['id'] for row in first['list']+second['list']}),26)
        self.assertEqual(second['list'][-1]['id'],old.pk)
        focus = data(f'/api/wallet/reports/?page=1&page_size=20&order_id={old_order.pk}')
        self.assertEqual([row['id'] for row in focus['list']],[old.pk])
        self.assertEqual(data(f'/api/wallet/reports/?page=1&order_id={other_order.pk}')['total'],0)
        self.assertEqual(data('/api/wallet/reports/?only_order_ids=1'),[old_order.pk])
