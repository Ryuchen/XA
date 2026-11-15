from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import wave
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.test import TransactionTestCase
from django.db import close_old_connections, connection
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider, make_user
from users.models import VoiceRecording


def audio_file():
    data = BytesIO()
    with wave.open(data, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 800)
    return SimpleUploadedFile('sample.wav', data.getvalue(), content_type='audio/wav')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class VoiceRecordingApiTests(APITestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-voice-test-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        self.provider = get_or_create_account_for_legacy_user(make_provider())
        self.other = get_or_create_account_for_legacy_user(make_provider())
        self.boss = get_or_create_account_for_legacy_user(make_user())

    def auth(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_multiple_private_recordings_can_be_removed_without_file_loss(self):
        self.auth(self.provider)
        first = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '第一段', 'client_request_id': uuid4().hex}, format='multipart')
        second = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '第二段', 'client_request_id': uuid4().hex}, format='multipart')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        item = first.data['data']
        self.assertEqual(item['duration_ms'], 100)
        self.assertEqual(len(self.client.get('/api/users/recordings/').data['data']['list']), 2)
        row = VoiceRecording.objects.get(pk=item['id'])
        self.assertTrue(row.audio.storage.exists(row.audio.name))
        direct = self.client.get('/media/' + row.audio.name)
        self.assertEqual(direct.status_code, 404)
        url = urlsplit(item['audio_url'])
        playback = self.client.get(url.path + ('?' + url.query if url.query else ''))
        self.assertEqual(playback.status_code, 200)
        self.assertEqual(playback['Content-Type'], 'audio/wav')
        self.assertTrue(b''.join(playback.streaming_content).startswith(b'RIFF'))
        self.auth(self.other)
        self.assertEqual(self.client.get('/api/users/recordings/').data['data']['list'], [])
        self.assertEqual(self.client.delete(f'/api/users/recordings/{row.pk}/').status_code, 404)
        self.auth(self.boss)
        self.assertEqual(self.client.get('/api/users/recordings/').status_code, 403)
        self.auth(self.provider)
        self.assertEqual(self.client.delete(f'/api/users/recordings/{row.pk}/').data['code'], 0)
        self.assertEqual(len(self.client.get('/api/users/recordings/').data['data']['list']), 1)
        row.refresh_from_db()
        self.assertIsNotNone(row.deleted_at)
        self.assertTrue(row.audio.storage.exists(row.audio.name))

    def test_invalid_files_are_not_stored(self):
        self.auth(self.provider)
        for upload in (SimpleUploadedFile('bad.wav', b'<script>bad</script>', content_type='audio/wav'),
                       SimpleUploadedFile('empty.mp3', b'', content_type='audio/mpeg'),
                       SimpleUploadedFile('bad.html', b'not audio', content_type='text/html')):
            response = self.client.post('/api/users/recordings/', {'audio': upload, 'client_request_id': uuid4().hex}, format='multipart')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(VoiceRecording.objects.exists())

    def test_expired_recording_link_renews_only_for_its_owner(self):
        from common.private_media import MAX_AGE, private_media_url
        self.auth(self.provider)
        result = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'client_request_id': uuid4().hex}, format='multipart').data['data']
        recording = VoiceRecording.objects.get(pk=result['id'])
        with patch('django.core.signing.time.time', return_value=1000):
            old = private_media_url(SimpleNamespace(account=self.provider), recording.audio)
        path = urlsplit(old).path
        ticket = path.rstrip('/').rsplit('/', 1)[-1]
        with patch('django.core.signing.time.time', return_value=1000 + MAX_AGE + 1):
            self.assertEqual(self.client.get(path).status_code, 404)
            self.assertEqual(self.client.get(path, HTTP_RANGE='bytes=0-1').status_code, 404)
            self.auth(self.other)
            self.assertEqual(self.client.post('/api/media/renew/', {'ticket': ticket}, format='json').status_code, 404)
            self.auth(self.provider)
            renewed = self.client.post('/api/media/renew/', {'ticket': ticket}, format='json')
            self.assertEqual(renewed.status_code, 200, renewed.data)
            audio = self.client.get(urlsplit(renewed.data['data']['url']).path)
            self.assertEqual(audio.status_code, 200)
            self.assertTrue(b''.join(audio.streaming_content).startswith(b'RIFF'))

    def test_authorized_audio_byte_ranges_and_seek_boundaries(self):
        self.auth(self.provider)
        original = audio_file().read()
        result = self.client.post('/api/users/recordings/', {'audio': audio_file(), 'client_request_id': uuid4().hex}, format='multipart').data['data']
        url = urlsplit(result['audio_url']).path
        size = len(original)
        for header, start, end in [('bytes=0-0', 0, 0), ('bytes=10-29', 10, 29),
                                   ('bytes=100-', 100, size - 1), ('bytes=-10', size - 10, size - 1),
                                   ('bytes=10-999999', 10, size - 1), ('bytes=-999999', 0, size - 1)]:
            with self.subTest(header=header):
                response = self.client.get(url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response['Content-Range'], f'bytes {start}-{end}/{size}')
                self.assertEqual(response['Content-Length'], str(end-start+1))
                self.assertEqual(response['Accept-Ranges'], 'bytes')
                self.assertEqual(response['Content-Type'], 'audio/wav')
                self.assertIn('no-store', response['Cache-Control'])
                self.assertEqual(b''.join(response.streaming_content), original[start:end+1])
                response.close()
        for header in (f'bytes={size}-', 'bytes=9-1', 'bytes=-0', 'bytes=-', 'bytes=' + '9' * 100 + '-'):
            with self.subTest(header=header):
                response = self.client.get(url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response['Content-Range'], f'bytes */{size}')
        for headers in ({'HTTP_RANGE': 'bytes=0-1,4-5'}, {'HTTP_RANGE': 'bytes=0-1', 'HTTP_IF_RANGE': 'stale'}):
            response = self.client.get(url, **headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), original)
            response.close()
        head = self.client.head(url, HTTP_RANGE='bytes=0-1')
        self.assertEqual((head.status_code, head['Content-Length']), (200, str(size)))
        self.assertEqual(head.content, b'')

    def test_upload_replay_and_lookup_are_owned_and_detect_conflicts(self):
        self.auth(self.provider)
        key = 'voice-retry-test-001'
        path = '/api/users/recordings/'
        def upload(title):
            return self.client.post(path, {'audio': audio_file(), 'title': title, 'client_request_id': key}, format='multipart')
        absent = self.client.get(path + 'resolve/', {'client_request_id': key}).data['data']
        self.assertEqual(absent['status'], 'NOT_FOUND')
        first = upload('本人录音')
        self.assertEqual(first.status_code, 200, first.data)
        replay = upload('本人录音')
        self.assertEqual(replay.data['data']['id'], first.data['data']['id'])
        self.assertEqual(VoiceRecording.objects.count(), 1)
        conflict = upload('不同名称')
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'VOICE_REQUEST_CONFLICT'))
        changed = bytearray(audio_file().read())
        changed[-1] = 1
        conflict = self.client.post(path, {'audio': SimpleUploadedFile('sample.wav', bytes(changed), content_type='audio/wav'),
            'title': '本人录音', 'client_request_id': key}, format='multipart')
        self.assertEqual((conflict.status_code, conflict.data['errorCode']), (409, 'VOICE_REQUEST_CONFLICT'))
        self.auth(self.other)
        self.assertEqual(self.client.get(path + 'resolve/', {'client_request_id': key}).data['data']['status'], 'NOT_FOUND')
        other_upload = self.client.post(path, {'audio': audio_file(), 'title': '他人录音', 'client_request_id': key}, format='multipart')
        self.assertEqual(other_upload.status_code, 200, other_upload.data)
        self.assertNotEqual(other_upload.data['data']['id'], first.data['data']['id'])
        self.auth(self.provider)
        resolved = self.client.get(path + 'resolve/', {'client_request_id': key}).data['data']
        self.assertEqual(resolved['status'], 'FOUND')
        self.assertEqual(resolved['recording']['id'], first.data['data']['id'])
        self.client.delete(path + str(first.data['data']['id']) + '/')
        self.assertEqual(self.client.get(path + 'resolve/', {'client_request_id': key}).data['data']['status'], 'REMOVED')
        self.assertEqual(upload('本人录音').data['errorCode'], 'VOICE_RECORDING_REMOVED')

    def test_bad_or_missing_request_ids_cannot_create_recordings(self):
        self.auth(self.provider)
        for key in (None, 'short', 'UPPERCASE', 'a' * 65, '../invalid'):
            payload = {'audio': audio_file()}
            if key is not None:
                payload['client_request_id'] = key
            response = self.client.post('/api/users/recordings/', payload, format='multipart')
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(VoiceRecording.objects.exists())

    def test_uncertain_database_failure_does_not_delete_written_file(self):
        from django.db import DatabaseError
        original = VoiceRecording.save
        saved_name = []
        def fail_after_save(row, *args, **kwargs):
            original(row, *args, **kwargs)
            saved_name.append(row.audio.name)
            raise DatabaseError('simulated unknown commit result')
        self.auth(self.provider)
        with patch.object(VoiceRecording, 'save', fail_after_save), self.assertRaises(DatabaseError):
            self.client.post('/api/users/recordings/', {'audio': audio_file(), 'client_request_id': uuid4().hex}, format='multipart')
        self.assertEqual(VoiceRecording.objects.count(), 0)
        self.assertTrue(VoiceRecording._meta.get_field('audio').storage.exists(saved_name[0]))


@skipUnless(connection.vendor == 'mysql', 'Requires MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class VoiceRecordingConcurrentTests(TransactionTestCase):
    def test_same_request_concurrently_creates_one_recording(self):
        folder = TemporaryDirectory(prefix='xa-voice-race-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        account = get_or_create_account_for_legacy_user(make_provider())
        token, _ = issue_account_tokens(account)
        barrier = Barrier(2)
        def upload():
            try:
                client = APIClient(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post('/api/users/recordings/', {'audio': audio_file(), 'title': '并发录音',
                    'client_request_id': 'voice-concurrent-001'}, format='multipart').data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            replies = [future.result(timeout=30) for future in (executor.submit(upload), executor.submit(upload))]
        self.assertEqual([result['code'] for result in replies], [0, 0], replies)
        self.assertEqual(replies[0]['data']['id'], replies[1]['data']['id'])
        self.assertEqual(VoiceRecording.objects.filter(owner=account).count(), 1)
