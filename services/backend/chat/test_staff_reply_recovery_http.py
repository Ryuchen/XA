"""Staff recovery against real isolated HTTP, including sender/session scope."""
import json
import base64
import io
import subprocess
import tempfile
from pathlib import Path

from django.test import LiveServerTestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from chat.models import ChatMessage
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffReplyRecoveryHTTPTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.TemporaryDirectory(prefix='xa-staff-chat-media-')
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable(); self.addCleanup(override.disable)

    def test_two_staff_sessions_response_loss_and_recovery(self):
        bosses = [ClubAccount.objects.create_account(username=f'staff-recovery-boss-{i}', account_type='BOSS') for i in (1, 2)]
        staff = []
        for _ in range(2):
            user = make_console_user(['chat:view', 'chat:reply'])
            account = get_or_create_account_for_legacy_user(user)
            AdminMembership.objects.filter(user=user).update(account=account)
            staff.append(account)
        viewer_user = make_console_user(['chat:view'])
        viewer = get_or_create_account_for_legacy_user(viewer_user)
        AdminMembership.objects.filter(user=viewer_user).update(account=viewer)
        sessions = []
        for boss in bosses:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
            sessions.append(client.get('/api/chat/session/').data['data']['session']['id'])
        repo = Path(__file__).resolve().parents[3]
        image = io.BytesIO(); Image.new('RGB', (3, 3), 'green').save(image, format='PNG')
        result = subprocess.run(['node', str(repo / 'scripts/admin-chat-reply-recovery-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'sessions': sessions,
                              'staffIds': [a.pk for a in staff], 'tokens': [issue_account_tokens(a)[0] for a in staff],
                              'image': base64.b64encode(image.getvalue()).decode('ascii')}),
            cwd=repo, text=True, encoding='utf-8', capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ChatMessage.objects.filter(session_id=sessions[0], is_from_support=True, content='隔离真实回复').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=sessions[0], is_from_support=True, content_type='IMAGE').count(), 1)
        self.assertEqual(ChatMessage.objects.filter(session_id=sessions[1], is_from_support=True).count(), 0)
        self.assertIn('PASS staff reply recovery', result.stdout)
        first = APIClient(); first.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff[0])[0])
        initial = ChatMessage.objects.get(session_id=sessions[0], is_from_support=True, content='隔离真实回复')
        text_conflict = first.post(f'/api/admin/chat-sessions/{sessions[0]}/reply/',
            {'content': '不同内容', 'client_message_id': initial.client_message_id}, format='json')
        self.assertEqual(text_conflict.status_code, 409)
        viewer_client = APIClient(); viewer_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(viewer)[0])
        forbidden = viewer_client.get(f'/api/admin/chat-sessions/{sessions[0]}/message-status/', {'client_message_id': initial.client_message_id})
        self.assertEqual(forbidden.status_code, 403)

        def png(color):
            output = io.BytesIO(); Image.new('RGB', (3, 3), color).save(output, format='PNG')
            return SimpleUploadedFile('evidence.png', output.getvalue(), content_type='image/png')
        second = APIClient(); second.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff[1])[0])
        url = f'/api/admin/chat-sessions/{sessions[0]}/reply/'
        with tempfile.TemporaryDirectory(prefix='xa-chat-staff-image-') as media, override_settings(MEDIA_ROOT=media):
            payload = {'image': png('red'), 'client_message_id': 'staff-image-id-123'}
            created = first.post(url, payload, format='multipart')
            self.assertEqual(created.data['code'], 0, created.data)
            replay = first.post(url, {'image': png('red'), 'client_message_id': 'staff-image-id-123'}, format='multipart')
            self.assertEqual(replay.data['data']['id'], created.data['data']['id'])
            conflict = first.post(url, {'image': png('blue'), 'client_message_id': 'staff-image-id-123'}, format='multipart')
            self.assertEqual(conflict.status_code, 409)
            status = second.get(f'/api/admin/chat-sessions/{sessions[0]}/message-status/', {'client_message_id': 'staff-image-id-123'})
            self.assertEqual(status.data['data']['status'], 'UNKNOWN')
            other = second.post(url, {'image': png('blue'), 'client_message_id': 'staff-image-id-123'}, format='multipart')
            self.assertEqual(other.data['code'], 0, other.data)
            self.assertNotEqual(created.data['data']['id'], other.data['data']['id'])
