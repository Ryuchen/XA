"""Real admin UI and account inboxes for unmapped current identities."""
import json
import os
from pathlib import Path
import subprocess

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_user
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class MessageAccountOnlyLiveTest(LiveServerTestCase):
    def test_admin_sends_to_current_boss_and_provider(self):
        root = os.environ.get('XA_ADMIN_MESSAGE_ROOT')
        provider_root = os.environ.get('XA_PROVIDER_MESSAGE_ROOT')
        if not root or not provider_root:
            self.skipTest('requires current isolated admin and provider builds')
        staff = make_console_user(['message:view', 'message:push'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss = ClubAccount.objects.create_account(username='isolated-current-boss', nickname='隔离同名', account_type='BOSS')
        provider = ClubAccount.objects.create_account(username='isolated-current-provider', nickname='隔离同名', account_type='PROVIDER')
        other = make_user(nickname='隔离同名')
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {'root':root, 'providerRoot':provider_root, 'providerId':provider.pk, 'origin':self.live_server_url, 'bossNo':boss.account_no,
            'providerNo':provider.account_no, 'staffToken':issue_account_tokens(staff_account)[0],
            'bossToken':issue_account_tokens(boss)[0], 'providerToken':issue_account_tokens(provider)[0],
            'otherToken':issue_account_tokens(other_account)[0]}
        run = subprocess.run(['node', str(repo / 'scripts/admin-message-account-only-live.cjs')], cwd=repo,
            input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        rows = Message.objects.filter(title='隔离业务账号通知')
        self.assertEqual(rows.count(), 2)
        self.assertEqual(set(rows.values_list('recipient_account_id', flat=True)), {boss.pk, provider.pk})
        self.assertEqual(rows.filter(recipient__isnull=True).count(), 2)
        boss_notice = rows.get(recipient_account=boss)
        token, refresh = issue_account_tokens(boss)
        native = subprocess.run(['node', str(repo / 'scripts/native-reservation-notice-live.cjs')], cwd=repo,
            input=json.dumps({'base':self.live_server_url, 'token':token, 'refresh':refresh,
                'account':boss.pk, 'message':boss_notice.pk, 'expectedTitle':'隔离业务账号通知',
                'expectedPreview':'隔离测试摘要', 'expectedTarget':''}),
            text=True, encoding='utf-8', capture_output=True, timeout=30)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        boss_notice.refresh_from_db()
        self.assertTrue(boss_notice.is_read)
