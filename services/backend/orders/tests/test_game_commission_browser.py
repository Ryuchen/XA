"""Real admin page and isolated ASGI-backed game commission drafts."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import GameCategory, GameCommissionProfile
from orders.tests.factories import make_console_user, make_service
from wallet.test_deposit_realtime_workflow import single_asgi


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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/game-commission-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'staff': {'id': account.pk, 'token': issue_account_tokens(account)[0],
                    'permissions': permissions}, 'reader': {'id': reader_account.pk, 'token': issue_account_tokens(reader_account)[0],
                    'permissions': ['dictionary:view']}, 'game_id': game.pk}), text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        saved = GameCommissionProfile.objects.get(game_category=game)
        self.assertEqual((saved.game, saved.rules, saved.version), ('VALORANT', {'companion': 0, 'grind': 35}, 2))
        other = GameCommissionProfile.objects.get(game_category=delta)
        self.assertEqual(other.game, 'DELTA')
        self.assertEqual(other.rules['scopes'], [{'provider_kind': 'GUARD', 'platform': 'PC',
            'security_level': 'TOP_SECRET', 'grade': 'MASTER', 'gameplay_id': None, 'rate': 22}])
        self.assertEqual(AdminAuditLog.objects.filter(resource='game-commission-draft', operator_account=account).count(), 3)
