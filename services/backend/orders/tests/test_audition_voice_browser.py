"""Opt-in browser test: real isolated backend, SQLite and temporary media."""
import base64
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import VoiceRecording
from orders.applications_models import OrderApplicationVoice
from users.test_voice_recordings import audio_file
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_AUDITION_VOICE_BROWSER') == '1', 'Requires isolated ASGI and frontend')
class AuditionVoiceBrowserTest(LiveServerTestCase):
    def test_provider_uploads_recording_and_boss_listens_and_selects(self):
        self.run_scenario(double=False)

    def test_both_providers_upload_recordings_and_boss_selects_team(self):
        self.run_scenario(double=True)

    def run_scenario(self, *, double):
        media = self.enterContext(TemporaryDirectory(prefix='xa-asgi-media-'))
        self.enterContext(override_settings(MEDIA_ROOT=media))
        self.enterContext(patch.dict(os.environ, {'XA_ASGI_NETWORK_TEST_MEDIA_ROOT': media}))
        boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
        make_wallet(boss, balance=100000)
        player = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True,
                               pass_tier='BLACK', pass_expires_at=timezone.now()+timedelta(days=1))
        provider_account = get_or_create_account_for_legacy_user(player)
        provider_account.openid = 'isolated-browser-audition'; provider_account.is_openid_bound = True
        provider_account.save(update_fields=['openid', 'is_openid_bound'])
        player.escort_profile.account = provider_account; player.escort_profile.save(update_fields=['account'])
        second_account = None
        if double:
            second = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True,
                                   pass_tier='BLACK', pass_expires_at=timezone.now()+timedelta(days=1))
            second_account = get_or_create_account_for_legacy_user(second)
            second_account.openid = 'isolated-browser-partner'; second_account.is_openid_bound = True
            second_account.save(update_fields=['openid', 'is_openid_bound'])
            second.escort_profile.account = second_account; second.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2 if double else 1)
        client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0], HTTP_HOST='sl.esports.club')
        with patch('orders.views._schedule_auto_cancel'):
            response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_mode': 'BOSS_SELECT',
                'client_request_id': 'audition-voice-browser'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = response.data['data']
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/audition-voice-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'order': order['id'], 'number': order['order_no'],
                    'audio': base64.b64encode(audio_file().read()).decode('ascii'),
                    'provider': {'id': str(provider_account.pk), 'token': issue_account_tokens(provider_account)[0]},
                    'boss': {'id': str(boss_account.pk), 'token': issue_account_tokens(boss_account)[0]},
                    'partner': ({'id': str(second_account.pk), 'token': issue_account_tokens(second_account)[0]}
                                if second_account else None), 'lose_first_upload': not double}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(VoiceRecording.objects.filter(owner=provider_account).count(), 1)
        if second_account:
            self.assertEqual(VoiceRecording.objects.filter(owner=second_account).count(), 1)
            self.assertEqual(OrderApplicationVoice.objects.filter(application__order_id=order['id']).count(), 2)
