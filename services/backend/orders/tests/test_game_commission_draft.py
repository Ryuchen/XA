"""Game-specific draft rules must not leak between games or into money flows."""
import os
from django.test import override_settings
from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient, APITestCase
from unittest import skipUnless
from unittest.mock import patch

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import GameCategory, GameCommissionProfile, Gameplay, ServiceCategory
from orders.tests.factories import make_console_user, make_service


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class GameCommissionDraftTest(APITestCase):
    def setUp(self):
        self.staff = make_console_user(['dictionary:view', 'dictionary:edit'])
        self.reader = make_console_user(['dictionary:view'])
        self.denied = make_console_user(['order:view'])
        self.clients = {}
        for user in (self.staff, self.reader, self.denied):
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.clients[user.pk] = client
        self.client = self.clients[self.staff.pk]
        self.delta = GameCategory.objects.create(name='测试三角洲')
        self.val = GameCategory.objects.create(name='无畏契约')
        self.league = GameCategory.objects.create(name='测试英雄联盟')
        self.url = lambda game: f'/api/admin/config/game-commissions/{game.pk}/'

    def save(self, category, game, rules, version=0):
        return self.client.put(self.url(category), {'version': version, 'game': game, 'rules': rules}, format='json')

    @override_settings(GAME_COMMISSION_PAYMENT_ENABLED=True)
    def test_enabled_rules_are_reported_as_effective_for_new_orders(self):
        result = self.save(self.val, 'VALORANT', {'companion': 15, 'grind': 27})
        self.assertEqual(result.data['data']['status'], 'PAYMENT_ENFORCED')
        service = make_service(game_category=self.val, provider_kind='RANK')
        self.assertEqual(self.client.get(self.url(self.val)).data['data']['status'], 'PAYMENT_ENFORCED')
        preview = self.client.get(self.url(self.val) + f'preview/{service.pk}/').data['data']
        self.assertEqual((preview['rate'], preview['status']), (27, 'PAYMENT_ENFORCED'))
        catalog = self.client.get('/api/admin/config/game-commissions/').data['data']
        self.assertTrue(catalog['payment_enforced'])
        coverage = self.client.get('/api/admin/config/game-commissions/coverage/').data['data']
        self.assertEqual(coverage['status'], 'PAYMENT_ENFORCED')
        self.assertEqual(coverage['list'][0]['code'], 'MATCHED_ACTIVE')

    def test_explicit_links_distinct_games_upward_mapping_and_missing_rate(self):
        self.assertEqual(self.save(self.val, 'VALORANT', {'companion': 0, 'grind': 27}).data['data']['status'], 'DRAFT_NOT_APPLIED')
        self.assertEqual(self.save(self.league, 'LEAGUE', {'companion': 14, 'grind': None}).status_code, 200)
        companion = make_service(game_category=self.val, provider_kind='COMPANION')
        rank = make_service(game_category=self.val, provider_kind='RANK')
        league = make_service(game_category=self.league, provider_kind='RANK')
        path = lambda category, service: self.url(category) + f'preview/{service.pk}/'
        self.assertEqual(self.client.get(path(self.val, companion)).data['data']['rate'], 0)
        self.assertEqual(self.client.get(path(self.val, rank)).data['data']['rate'], 27)
        self.assertEqual(self.client.get(path(self.league, league)).status_code, 400)
        self.assertEqual(self.client.get(path(self.league, rank)).status_code, 404)
        # Display names can change without changing the explicit game relation.
        self.val.name = '测试更名'; self.val.save(update_fields=['name'])
        self.assertEqual(self.client.get(path(self.val, rank)).data['data']['game'], 'VALORANT')
        rank.provider_kind = 'GUARD'; rank.save(update_fields=['provider_kind'])
        self.assertEqual(self.client.get(path(self.val, rank)).status_code, 400)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_delta_exact_scope_overlap_and_unconfigured(self):
        gameplay = Gameplay.objects.create(name='测试玩法', code='test-commission-gameplay')
        base = {'provider_kind': 'GUARD', 'platform': 'PC', 'security_level': 'TOP_SECRET',
                'grade': 'MASTER', 'gameplay_id': None, 'rate': 22}
        overlap = {**base, 'gameplay_id': gameplay.pk, 'rate': 15}
        self.assertEqual(self.save(self.delta, 'DELTA', {'scopes': [base, overlap]}).status_code, 400)
        self.assertEqual(self.save(self.delta, 'DELTA', {'scopes': [base]}).status_code, 200)
        service = make_service(game_category=self.delta, gameplay=gameplay, provider_kind='GUARD',
            platform='PC', security_level='TOP_SECRET', provider_grade='MASTER')
        path = self.url(self.delta) + f'preview/{service.pk}/'
        self.assertEqual(self.client.get(path).data['data']['rate'], 22)
        service.provider_grade = 'LEGEND'; service.save(update_fields=['provider_grade'])
        self.assertEqual(self.client.get(path).status_code, 400)
        invalid = {**base, 'grade': []}
        self.assertEqual(self.save(self.delta, 'DELTA', {'scopes': [invalid]}, version=1).status_code, 400)

    def test_permissions_versions_atomic_audit_and_unique_game_type(self):
        reader, denied = self.clients[self.reader.pk], self.clients[self.denied.pk]
        self.assertEqual(denied.get(self.url(self.val)).status_code, 403)
        self.assertEqual(denied.get('/api/admin/config/game-commissions/').status_code, 403)
        self.assertEqual(reader.put(self.url(self.val), {}, format='json').status_code, 403)
        original = {'companion': 15, 'grind': 25}
        saved = self.save(self.val, 'VALORANT', original)
        self.assertEqual(saved.data['data']['configuration']['version'], 1)
        stale = self.save(self.val, 'VALORANT', {'companion': 30, 'grind': 30})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.save(self.league, 'VALORANT', original).status_code, 409)
        updated = self.save(self.val, 'VALORANT', {'companion': 18, 'grind': 25}, version=1)
        self.assertEqual(updated.data['data']['configuration']['version'], 2)
        logs = list(AdminAuditLog.objects.filter(resource='game-commission-draft').order_by('pk'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].request_body['before']['rules'], original)
        self.assertEqual(logs[1].request_body['after']['rules']['companion'], 18)
        self.assertEqual(GameCommissionProfile.objects.count(), 1)
        self.assertEqual(reader.get(self.url(self.val)).data['data']['configuration']['version'], 2)
        directory = reader.get('/api/admin/config/game-commissions/').data['data']
        self.assertEqual(directory['capability'], 'GAME_COMMISSION_DRAFT_V1')
        self.assertEqual(next(row for row in directory['games'] if row['id'] == self.val.pk)['game'], 'VALORANT')

    def test_incorrect_draft_game_classification_can_be_corrected_and_audited(self):
        original = {'companion': 15, 'grind': 25}
        self.assertEqual(self.save(self.val, 'LEAGUE', original).status_code, 200)
        corrected = self.save(self.val, 'VALORANT', original, version=1)
        self.assertEqual(corrected.data['data']['configuration']['game'], 'VALORANT')
        audit = AdminAuditLog.objects.filter(resource='game-commission-draft').order_by('-pk').first()
        self.assertEqual((audit.request_body['before']['game'], audit.request_body['after']['game']),
                         ('LEAGUE', 'VALORANT'))

    def test_invalid_values_never_create_configuration(self):
        for rules in ({'companion': True, 'grind': 10}, {'companion': -1, 'grind': 10},
                      {'companion': 101, 'grind': 10}, {'companion': 20, 'grind': 10, 'grade': 'MASTER'},
                      {'companion': 20}, []):
            with self.subTest(rules=rules):
                self.assertEqual(self.save(self.val, 'VALORANT', rules).status_code, 400)
        self.assertFalse(GameCommissionProfile.objects.exists())

    def test_failed_audit_rolls_back_rule_update(self):
        initial = {'companion': 12, 'grind': 25}
        self.assertEqual(self.save(self.val, 'VALORANT', initial).status_code, 200)
        with patch('console.game_commission.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaisesRegex(RuntimeError, 'audit failed'):
                self.save(self.val, 'VALORANT', {'companion': 33, 'grind': 25}, version=1)
        saved = GameCommissionProfile.objects.get(game_category=self.val)
        self.assertEqual((saved.rules, saved.version), (initial, 1))
        self.assertEqual(AdminAuditLog.objects.filter(resource='game-commission-draft').count(), 1)

    def test_database_rejects_invalid_classification_and_zero_version(self):
        for game, version in (('UNKNOWN', 1), ('DELTA', 0)):
            with self.subTest(game=game, version=version):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    GameCommissionProfile.objects.create(game_category=self.delta, game=game,
                                                         version=version, rules={})

    def test_coverage_reports_active_services_without_claiming_business_readiness(self):
        GameCommissionProfile.objects.create(game_category=self.val, game='VALORANT',
            rules={'companion': 0, 'grind': None})
        GameCommissionProfile.objects.create(game_category=self.delta, game='DELTA', rules={})
        matched = make_service(game_category=self.val, provider_kind='COMPANION')
        missing = make_service(game_category=self.val, provider_kind='RANK')
        unsupported = make_service(game_category=self.val, provider_kind='GUARD')
        no_game = make_service(game_category=None)
        no_profile = make_service(game_category=self.league, provider_kind='GRIND')
        manual = make_service(game_category=self.val, provider_kind='COMPANION', commission_override=True)
        invalid = make_service(game_category=self.delta, provider_kind='GUARD')
        gift = ServiceCategory.objects.create(name='隔离礼物分类', is_gift=True)
        special = [make_service(service_category=gift), make_service(is_addon=True),
                   make_service(experience_kind='NEW_FIRST')]
        make_service(is_active=False)
        inactive_game = GameCategory.objects.create(name='已停用游戏', is_active=False)
        make_service(game_category=inactive_game)
        url = '/api/admin/config/game-commissions/coverage/'
        self.assertEqual(self.clients[self.denied.pk].get(url).status_code, 403)
        data = self.clients[self.reader.pk].get(url, {'page_size': 100}).data['data']
        self.assertEqual(data['status'], 'DRAFT_NOT_APPLIED')
        self.assertEqual(data['counts'], {'all': 10, 'matched': 1, 'issues': 6, 'special': 3})
        by_id = {row['id']: row for row in data['list']}
        self.assertEqual((by_id[matched.pk]['code'], by_id[matched.pk]['rate']), ('MATCHED_DRAFT', 0))
        self.assertEqual(by_id[missing.pk]['code'], 'NO_MATCH')
        self.assertEqual(by_id[unsupported.pk]['code'], 'NO_MATCH')
        self.assertEqual(by_id[no_game.pk]['code'], 'NO_GAME')
        self.assertEqual(by_id[no_profile.pk]['code'], 'NO_PROFILE')
        self.assertEqual(by_id[manual.pk]['code'], 'SERVICE_OVERRIDE')
        self.assertEqual(by_id[invalid.pk]['code'], 'INVALID_PROFILE')
        self.assertTrue(all(by_id[row.pk]['group'] == 'special' for row in special))
        filtered = self.client.get(url, {'result': 'issues', 'page_size': 2}).data['data']
        self.assertEqual((filtered['total'], len(filtered['list']), filtered['counts']['all']), (6, 2, 10))
        per_game = self.client.get(url, {'game_category_id': self.val.pk}).data['data']
        self.assertEqual(per_game['counts'], {'all': 4, 'matched': 1, 'issues': 3, 'special': 0})
        self.assertEqual(self.client.get(url, {'game_category_id': '0'}).status_code, 400)
        self.assertEqual(self.client.get(url, {'result': 'unknown'}).status_code, 400)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
class GameCommissionStoreIsolationTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '游戏抽成付款/分组派单消息确认/游戏账号字段/抽成草稿/客服代下抢单池口径属正式契约深水区（18-盘点 游戏抽成/派单/企微KOOK待联调）；最终联调启用')
    def test_same_game_and_service_ids_keep_separate_rates_and_audits(self):
        from stores.context import resolve_store, store_context
        from stores.models import Store
        first = resolve_store()
        second = Store.objects.create(slug='xinan', name='兴安测试', database_alias='store_b',
                                      status=Store.Status.ACTIVE)
        for store, rate in ((first, 11), (second, 29)):
            with store_context(store):
                staff = make_console_user(['dictionary:view', 'dictionary:edit'])
                account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = account
                staff.admin_membership.save(update_fields=['account'])
                category = GameCategory.objects.create(pk=87222, name='同编号测试游戏')
                service = make_service(pk=87223, game_category=category, provider_kind='RANK')
                client = APIClient(HTTP_X_STORE_SLUG=store.slug)
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                url = f'/api/admin/config/game-commissions/{category.pk}/'
                response = client.put(url, {'game': 'VALORANT', 'version': 0,
                    'rules': {'companion': 20, 'grind': rate}}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(client.get(url + f'preview/{service.pk}/').data['data']['rate'], rate)
                coverage = client.get('/api/admin/config/game-commissions/coverage/', {'game_category_id': category.pk}).data['data']
                self.assertEqual((coverage['counts']['matched'], coverage['list'][0]['rate']), (1, rate))
                self.assertEqual(AdminAuditLog.objects.filter(resource='game-commission-draft').count(), 1)
        with store_context(first):
            self.assertEqual(GameCommissionProfile.objects.get(game_category_id=87222).rules['grind'], 11)
        with store_context(second):
            self.assertEqual(GameCommissionProfile.objects.get(game_category_id=87222).rules['grind'], 29)
