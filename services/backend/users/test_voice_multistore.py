from datetime import timedelta
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest import skipUnless

from django.conf import settings
from django.db.models import F
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from engagement.models import ClubOperationConfig, PointFundingLot
from orders.models import OrderApplication
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.test_voice_recordings import audio_file


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class VoiceRecordingStoreIsolationTests(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-voice-stores-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))

    def test_same_number_recordings_and_signed_urls_stay_in_their_store(self):
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

    def signed_client(self, store, host, account):
        with store_context(store):
            token, _ = issue_account_tokens(account)
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token)

    def place_audition(self, store, host, sample):
        with store_context(store):
            user = make_user()
            buyer = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, buyer)
            wallet = make_wallet(user, balance=50000)
            wallet.account = buyer
            wallet.save(update_fields=['account'])
            ClubOperationConfig.objects.get_or_create(pk=1)
            PointFundingLot.objects.create(account=buyer, source_key='isolated-audition-media',
                kind=PointFundingLot.Kind.PREPAID, remaining=wallet.balance)
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            reader = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, reader)
            provider.escort_profile.account = reader
            provider.escort_profile.save(update_fields=['account'])
            service = make_service(price=10000)
            provider.escort_profile.service_items.add(service)

        buyer_api = self.signed_client(store, host, buyer)
        provider_api = self.signed_client(store, host, reader)
        uploaded = self.ok(provider_api.post('/api/users/recordings/', {'audio': audio_file(sample=sample),
            'title': store.name, 'client_request_id': 'same-isolated-audio'}, format='multipart'))
        order = self.ok(buyer_api.post('/api/orders/create/', {'service_id': service.pk,
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'same-isolated-audition'}, format='json'))
        order_url = f"/api/orders/orders/{order['id']}/"
        self.ok(provider_api.post(order_url + 'grab/', {'recording_id': uploaded['id']}, format='json'))
        details = self.ok(buyer_api.get(order_url))
        application = details['applications'][0]
        with store_context(store):
            self.assertEqual(OrderApplication.objects.get(pk=application['id']).order_id, order['id'])
        return {'store': store, 'host': host, 'buyer': buyer, 'provider': reader, 'api': buyer_api,
                'order_url': order_url,
                'ids': (buyer.pk, reader.pk, uploaded['id'], order['id'], application['id']),
                'audio': audio_file(sample=sample).read(),
                'buyer_audio': urlsplit(application['members'][0]['recording']['audio_url']).path,
                'provider_audio': urlsplit(uploaded['audio_url']).path}

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def assert_audio_range(self, host, path, audio):
        response = APIClient(HTTP_HOST=host).get(path, HTTP_RANGE='bytes=44-51')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response['Content-Range'], f'bytes 44-51/{len(audio)}')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(b''.join(response.streaming_content), audio[44:52])

    def renew(self, client, path):
        ticket = path.rstrip('/').rsplit('/', 1)[-1]
        return client.post('/api/media/renew/', {'ticket': ticket}, format='json')

    def test_same_number_auditions_isolate_buyer_playback_renewal_and_reader_cycles(self):
        first = self.place_audition(resolve_store(), 'sl.esports.club', sample=101)
        second = self.place_audition(xinan_store(), 'xa.esports.club', sample=202)
        self.assertEqual(first['ids'], second['ids'])
        self.assertNotEqual(first['audio'], second['audio'])
        for local, other in ((first, second), (second, first)):
            with self.subTest(store=local['store'].slug):
                self.assert_audio_range(local['host'], local['buyer_audio'], local['audio'])
                renewed = self.ok(self.renew(local['api'], local['buyer_audio']))
                self.assert_audio_range(local['host'], urlsplit(renewed['url']).path, local['audio'])
                wrong_store = APIClient(HTTP_HOST=other['host'])
                self.assertEqual(wrong_store.get(local['buyer_audio'], HTTP_RANGE='bytes=44-51').status_code, 404)
                self.assertEqual(self.renew(other['api'], local['buyer_audio']).status_code, 404)

        for role in ('buyer', 'provider'):
            reader = first[role]
            previous = first[f'{role}_audio']
            with store_context(first['store']):
                ClubAccount.objects.filter(pk=reader.pk).update(license_membership_active=False,
                    business_cycle=F('business_cycle') + 1)
            anonymous_audio = APIClient(HTTP_HOST=first['host'])
            self.assertEqual(anonymous_audio.get(previous, HTTP_RANGE='bytes=44-51').status_code, 404)
            with store_context(first['store']):
                ClubAccount.objects.filter(pk=reader.pk).update(license_membership_active=True)
                reader.refresh_from_db()
            client = self.signed_client(first['store'], first['host'], reader)
            self.assertEqual(self.renew(client, previous).status_code, 404)
            self.assertEqual(anonymous_audio.get(previous).status_code, 404)
            if role == 'buyer':
                detail = self.ok(client.get(first['order_url']))
                fresh = detail['applications'][0]['members'][0]['recording']['audio_url']
            else:
                fresh = self.ok(client.get('/api/users/recordings/'))['list'][0]['audio_url']
            self.assert_audio_range(first['host'], urlsplit(fresh).path, first['audio'])
        self.assert_audio_range(second['host'], second['buyer_audio'], second['audio'])
