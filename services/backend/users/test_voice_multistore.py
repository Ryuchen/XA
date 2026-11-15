from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest import skipUnless

from django.conf import settings
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.test_voice_recordings import audio_file


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class VoiceRecordingStoreIsolationTests(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_same_number_recordings_and_signed_urls_stay_in_their_store(self):
        folder = TemporaryDirectory(prefix='xa-voice-stores-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        entries = []
        client = APIClient()
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                user = make_provider()
                account = get_or_create_account_for_legacy_user(user)
                token, _ = issue_account_tokens(account)
                account_id = account.pk
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            response = client.post('/api/users/recordings/', {'audio': audio_file(), 'title': store.slug, 'client_request_id': 'same-voice-request'},
                                   format='multipart', HTTP_HOST=host)
            self.assertEqual(response.status_code, 200, response.data)
            entries.append((host, token, account_id, response.data['data']))
        self.assertEqual(entries[0][2], entries[1][2])
        self.assertEqual(entries[0][3]['id'], entries[1][3]['id'])
        from pathlib import Path
        from users.models import VoiceRecording
        with store_context(resolve_store()):
            Path(VoiceRecording.objects.get(pk=entries[0][3]['id']).audio.path).unlink()
        for index, (host, token, _, row) in enumerate(entries):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            listing = client.get('/api/users/recordings/', HTTP_HOST=host)
            self.assertEqual([item['title'] for item in listing.data['data']['list']], [row['title']])
            self.assertEqual(listing.data['data']['list'][0]['available'], index == 1)
            resolved = client.get('/api/users/recordings/resolve/', {'client_request_id': 'same-voice-request'}, HTTP_HOST=host)
            self.assertEqual((resolved.data['data']['status'], resolved.data['data']['recording']['title']), ('FOUND', row['title']))
            foreign_host = entries[1-index][0]
            self.assertNotEqual(client.get('/api/users/recordings/', HTTP_HOST=foreign_host).status_code, 200)
            url = urlsplit(row['audio_url'])
            foreign = client.get(url.path + ('?' + url.query if url.query else ''), HTTP_HOST=foreign_host)
            self.assertNotEqual(foreign.status_code, 200)
            foreign_range = client.get(url.path + ('?' + url.query if url.query else ''), HTTP_HOST=foreign_host, HTTP_RANGE='bytes=0-1')
            self.assertNotIn(foreign_range.status_code, (200, 206))
