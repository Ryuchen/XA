"""Two fulfilled TAGs remain manageable after one expires."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberTag, RewardItem
from orders.tests.factories import make_console_user, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TagMembershipJourneyTest(LiveServerTestCase):
    host, port = '127.0.0.1', 19210

    def test_expired_selection_does_not_block_current_tag(self):
        boss, staff = make_user(), make_console_user(['engagement:points', 'engagement:fulfill'])
        account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, staff)]
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        clients = []
        for user in (account, staff_account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(user)[0])
            clients.append(client)
        boss_client, staff_client = clients
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']
        ok(staff_client.post('/api/admin/external-points/', {'account_id': account.pk, 'kind': 'KOOK_CHECKIN',
            'day': timezone.localdate().isoformat(), 'source_id': 'tag-journey-checkin'}, format='json'))
        claims = []
        for index, days in enumerate((1, 7)):
            reward = RewardItem.objects.create(code=f'tag-journey-{index}', name=f'TAG权益{index}',
                kind='TAG', audience='BOSS', point_cost=1, duration_days=days, in_shop=True, is_active=True)
            claim = ok(boss_client.post('/api/club/redeem/', {'reward_id': reward.pk,
                'client_request_id': f'tag-journey-redeem-{index}'}, format='json'))
            ok(boss_client.patch(f"/api/club/claims/{claim['id']}/", {'details': {'tag': f'标签{index}'}}, format='json'))
            ok(staff_client.post(f"/api/admin/club-claims/{claim['id']}/process/",
                {'status': 'COMPLETED', 'note': '已交付标签', 'tag_reviewed': True}, format='json'))
            claims.append(claim['id'])
        # A pre-existing custom TAG has no review evidence until staff rechecks it.
        MemberTag.objects.filter(event_key=f'claim:{claims[0]}:tag').update(reviewed_at=None,reviewed_by=None)
        before = ok(boss_client.get('/api/club/me/'))
        self.assertNotIn('标签0', [tag['title'] for tag in before['tags']])
        ok(staff_client.post(f'/api/admin/club-claims/{claims[0]}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'标签0'},format='json'))
        member = ok(boss_client.get('/api/club/me/'))
        keys = {tag['title']: tag['key'] for tag in member['tags']}
        ok(boss_client.patch('/api/club/me/', {'selected_tags': [keys['标签0'], keys['标签1']]}, format='json'))
        ok(staff_client.post(f'/api/admin/club-claims/{claims[0]}/suspend-tag/',
            {'reason':'等待复核','expected_tag':'标签0'},format='json'))
        paused = ok(boss_client.get('/api/club/me/'))
        self.assertEqual(paused['selected_tags'], [keys['标签1']])
        paused_claim = ok(boss_client.get(f'/api/club/claims/{claims[0]}/'))
        self.assertEqual(paused_claim['tag_suspension_reason'],'等待复核')
        self.assertTrue(paused_claim['tag_editable'])
        self.assertFalse(paused_claim['tag_revision_submitted'])
        tag = MemberTag.objects.get(event_key=f'claim:{claims[0]}:tag')
        expiry_before = tag.expires_at
        if os.environ.get('XA_NATIVE_TAG_REVISION') == '1':
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(repo / 'scripts/native-tag-revision-375.cjs')], cwd=repo,
                input=json.dumps({'claimId': claims[0], 'session': {'token': issue_account_tokens(account)[0],
                    'user': {'id': str(account.pk), 'role': 'customer'}}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            ok(boss_client.patch(f'/api/club/claims/{claims[0]}/', {'details':{'tag':'清海老板'}}, format='json'))
        revised_claim = ok(boss_client.get(f'/api/club/claims/{claims[0]}/'))
        self.assertEqual(revised_claim['details']['tag'],'清海老板')
        self.assertTrue(revised_claim['tag_revision_submitted'])
        tag.refresh_from_db()
        self.assertEqual((tag.title,tag.expires_at),('标签0',expiry_before))
        self.assertNotIn('清海老板',[item['title'] for item in ok(boss_client.get('/api/club/me/'))['tags']])
        with patch('django.utils.timezone.now', return_value=expiry_before+timedelta(seconds=1)):
            self.assertFalse(ok(boss_client.get(f'/api/club/claims/{claims[0]}/'))['tag_editable'])
            expired = boss_client.patch(f'/api/club/claims/{claims[0]}/', {'details':{'tag':'到期修改'}}, format='json')
            self.assertEqual(expired.status_code,409)
        ok(staff_client.post(f'/api/admin/club-claims/{claims[0]}/review-tag/',
            {'tag_reviewed':True,'expected_tag':'清海老板'},format='json'))
        tag.refresh_from_db()
        self.assertEqual((tag.title,tag.expires_at),('清海老板',expiry_before))
        reviewed_claim = ok(boss_client.get(f'/api/club/claims/{claims[0]}/'))
        self.assertFalse(reviewed_claim['tag_revision_submitted'])
        self.assertIn('清海老板',[item['title'] for item in ok(boss_client.get('/api/club/me/'))['display_tags']])
        # Advance the business clock while the real API and native UI continue to run.
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(days=2)):
            member = ok(boss_client.get('/api/club/me/'))
            self.assertEqual(member['selected_tags'], [keys['标签1']])
            self.assertEqual([tag['title'] for tag in member['display_tags']], ['标签1'])
            if os.environ.get('XA_NATIVE_TAG_MEMBERSHIP') == '1':
                repo = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(repo / 'scripts/native-tag-membership-375.cjs')], cwd=repo,
                    input=json.dumps({'claimId': claims[1], 'validKey': keys['标签1'],
                        'session': {'token': issue_account_tokens(account)[0],
                            'user': {'id': str(account.pk), 'role': 'customer'}}}),
                    text=True, encoding='utf-8', capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                ok(boss_client.patch('/api/club/me/', {'selected_tags': [keys['标签1']]}, format='json'))
