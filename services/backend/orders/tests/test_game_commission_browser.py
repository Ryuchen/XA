"""Admin game commission drafts against the real isolated HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import GameCategory, GameCommissionProfile
from orders.tests.factories import make_console_user, make_service


@skipUnless(os.environ.get('XA_GAME_COMMISSION_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class GameCommissionBrowserTest(LiveServerTestCase):
    def test_save_and_reopen_game_rule_draft(self):
        permissions = ['dictionary:view', 'dictionary:edit']
        staff = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        reader = make_console_user(['dictionary:view'])
        reader_account = get_or_create_account_for_legacy_user(reader)
        reader.admin_membership.account = reader_account; reader.admin_membership.save(update_fields=['account'])
        game = GameCategory.objects.create(name='界面隔离游戏')
        delta = GameCategory.objects.create(name='界面三角洲测试')
        make_service(name='界面陪玩服务', game_category=game, provider_kind='COMPANION')
        make_service(name='界面未映射方向', game_category=game, provider_kind='GUARD')
        make_service(name='界面三角洲护航', game_category=delta, provider_kind='GUARD',
                     platform='PC', security_level='TOP_SECRET', provider_grade='MASTER')
        make_service(name='界面特殊加购', is_addon=True)
        make_service(name='界面已停用服务', is_active=False)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        url = f'/api/admin/config/game-commissions/{game.pk}/'
        first = client.put(url, {'game': 'VALORANT', 'version': 0, 'rules': {'companion': 0, 'grind': 25}}, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        concurrent = client.put(url, {'game': 'VALORANT', 'version': 1, 'rules': {'companion': 0, 'grind': 35}}, format='json')
        self.assertEqual(concurrent.status_code, 200, concurrent.data)
        stale = client.put(url, {'game': 'VALORANT', 'version': 1, 'rules': {'companion': 0, 'grind': 40}}, format='json')
        self.assertEqual(stale.data['errorCode'], 'CONFIG_CHANGED', stale.data)
        reopened = client.get(url)
        self.assertEqual(reopened.data['data']['configuration']['rules'], {'companion': 0, 'grind': 35})
        delta_save = client.put(f'/api/admin/config/game-commissions/{delta.pk}/', {'game': 'DELTA', 'version': 0,
            'rules': {'scopes': [{'provider_kind': 'GUARD', 'platform': 'PC', 'security_level': 'TOP_SECRET',
                                  'grade': 'MASTER', 'gameplay_id': None, 'rate': 22}]}}, format='json')
        self.assertEqual(delta_save.data['code'], 0, delta_save.data)
        reader_client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reader_account)[0])
        denied = reader_client.put(url, {'game': 'VALORANT', 'version': 2, 'rules': {'companion': 0, 'grind': 35}}, format='json')
        self.assertEqual(denied.status_code, 403, denied.data)
        saved = GameCommissionProfile.objects.get(game_category=game)
        self.assertEqual((saved.game, saved.rules, saved.version), ('VALORANT', {'companion': 0, 'grind': 35}, 2))
        other = GameCommissionProfile.objects.get(game_category=delta)
        self.assertEqual(other.game, 'DELTA')
        self.assertEqual(other.rules['scopes'], [{'provider_kind': 'GUARD', 'platform': 'PC',
            'security_level': 'TOP_SECRET', 'grade': 'MASTER', 'gameplay_id': None, 'rate': 22}])
        self.assertEqual(AdminAuditLog.objects.filter(resource='game-commission-draft', operator_account=account).count(), 3)