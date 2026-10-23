from io import BytesIO
from random import Random
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from chat.models import ChatMessage, ChatSession
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from common.media import build_media_url, build_media_url_from_path
from common.private_media import MAX_AGE, SALT
from orders.tests.factories import make_console_user, make_provider, make_user
from wallet.models import ProviderReport, ProviderReportImage, RechargeRecord


def image_file(name='evidence.png'):
    data = BytesIO()
    Image.new('RGB', (8, 8), '#e7bbc8').save(data, 'PNG')
    return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')


def account_for(user):
    account = get_or_create_account_for_legacy_user(user)
    if hasattr(user, 'admin_membership'):
        membership = user.admin_membership
        membership.account = account
        membership.save(update_fields=['account'])
    return account


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class PrivateMediaTest(APITestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-private-media-test-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        self.root = Path(folder.name)
        self.provider = make_provider()
        self.account = account_for(self.provider)
        self.other = account_for(make_provider())
        self.boss = make_user()
        self.boss_account = account_for(self.boss)
        self.report = ProviderReport.objects.create(
            provider=self.provider, provider_account=self.account, game_name='权限测试',
            amount=1000, entry_image=image_file(), completion_image=image_file(),
            proof_image=image_file(), status='PENDING',
        )
        self.result = ProviderReportImage.objects.create(report=self.report, image=image_file())

    def url(self, field=None, account=None):
        return build_media_url(SimpleNamespace(account=account or self.account), field or self.report.entry_image)

    def fetch(self, url):
        response = self.client.get(urlsplit(url).path)
        # Django's test-client stream wrapper closes the response once, with
        # request_finished's DB cleanup detached. Closing it a second time here
        # would close MySQL inside TestCase's wrapping transaction.
        if response.streaming:
            response.body = b''.join(response.streaming_content)
        return response

    def test_report_api_returns_working_capabilities_without_raw_file_paths(self):
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = self.client.get('/api/wallet/reports/')
        self.assertEqual(response.data['code'], 0)
        record = response.data['data'][0]
        self.assertNotIn('proof_image', record)
        self.client.credentials()
        for key in ('proof_image_url', 'entry_image_url', 'completion_image_url'):
            url = record[key]
            self.assertIn('/api/media/private/', url)
            result = self.fetch(url)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result['Content-Type'], 'image/png')
            self.assertIn('no-store', result['Cache-Control'])
            self.assertEqual(result['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.fetch(record['result_images'][0]['url']).status_code, 200)

    def test_anonymous_other_provider_and_boss_cannot_mint_report_grants(self):
        for request in (None, SimpleNamespace(account=self.other), SimpleNamespace(account=self.boss_account)):
            self.assertEqual(build_media_url(request, self.report.entry_image), '')

    def test_staff_must_have_report_view_and_cannot_view_drafts(self):
        wrong = account_for(make_console_user(['order:view']))
        reader = account_for(make_console_user(['report:view']))
        self.assertEqual(self.url(account=wrong), '')
        self.assertEqual(self.fetch(self.url(account=reader)).status_code, 200)
        self.report.status = 'DRAFT'
        self.report.save(update_fields=['status'])
        self.assertEqual(self.url(account=reader), '')
        self.assertEqual(self.fetch(self.url()).status_code, 200)

    def test_expired_and_tampered_capabilities_fail_and_refetch_renews(self):
        with patch('django.core.signing.time.time', return_value=1000):
            old = self.url()
        with patch('django.core.signing.time.time', return_value=1000 + MAX_AGE + 1):
            self.assertEqual(self.fetch(old).status_code, 404)
            self.assertEqual(self.fetch(self.url()).status_code, 200)
        self.assertEqual(self.fetch(self.url().replace('/private/', '/private/x')).status_code, 404)

    def test_disabling_account_or_login_invalidates_existing_grant(self):
        for flag in ('is_active', 'can_login'):
            url = self.url()
            setattr(self.account, flag, False)
            self.account.save(update_fields=[flag])
            self.assertEqual(self.fetch(url).status_code, 404)
            setattr(self.account, flag, True)
            self.account.save(update_fields=[flag])

    def test_staff_permission_revocation_invalidates_existing_grant(self):
        user = make_console_user(['report:view'])
        reader = account_for(user)
        url = self.url(account=reader)
        role = user.admin_membership.roles.get()
        role.permissions = ['order:view']
        role.save(update_fields=['permissions'])
        self.assertEqual(self.fetch(url).status_code, 404)

    def test_staff_membership_revocation_invalidates_existing_grant(self):
        user = make_console_user(['report:view'])
        url = self.url(account=account_for(user))
        membership = user.admin_membership
        membership.is_active = False
        membership.save(update_fields=['is_active'])
        self.assertEqual(self.fetch(url).status_code, 404)

    def test_replaced_deleted_or_reassigned_record_revokes_earlier_grant(self):
        old = self.url()
        self.report.entry_image.save('replacement.png', image_file())
        self.assertEqual(self.fetch(old).status_code, 404)
        old = self.url()
        self.report.provider_account = self.other
        self.report.save(update_fields=['provider_account'])
        self.assertEqual(self.fetch(old).status_code, 404)
        url = self.url(account=self.other)
        self.report.delete()
        self.assertEqual(self.fetch(url).status_code, 404)

    def test_result_image_deletion_revokes_grant(self):
        url = self.url(self.result.image)
        self.result.delete()
        self.assertEqual(self.fetch(url).status_code, 404)

    def test_private_images_ignore_public_media_domain(self):
        with override_settings(MEDIA_BASE_URL='https://assets.example.test'):
            url = self.url()
            self.assertTrue(url.startswith('/api/media/private/'))
            self.assertEqual(self.fetch(url).status_code, 200)

    def test_recharge_receipt_is_limited_to_recharge_read_permission(self):
        record = RechargeRecord.objects.create(user=self.boss, account=self.boss_account, amount=1000, proof_image=image_file())
        for reader in (self.boss_account, self.account, account_for(make_console_user(['wallet:view']))):
            self.assertEqual(self.url(record.proof_image, reader), '')
        reader = account_for(make_console_user(['recharge:view']))
        self.assertEqual(self.fetch(self.url(record.proof_image, reader)).status_code, 200)

    def test_verification_image_is_private_to_provider_and_escort_reader(self):
        profile = self.provider.escort_profile
        profile.account = self.account
        profile.cheat_proof = image_file()
        profile.save()
        self.assertEqual(self.fetch(self.url(profile.cheat_proof)).status_code, 200)
        self.assertEqual(self.url(profile.cheat_proof, self.other), '')
        self.assertEqual(self.url(profile.cheat_proof, self.boss_account), '')
        reader = account_for(make_console_user(['escort:view']))
        self.assertEqual(self.fetch(self.url(profile.cheat_proof, reader)).status_code, 200)

    def test_chat_image_follows_session_owner_not_sender(self):
        staff = account_for(make_console_user(['chat:view']))
        session = ChatSession.objects.create(user=self.boss, account=self.boss_account)
        msg = ChatMessage.objects.create(session=session, image=image_file(), content_type='IMAGE', is_from_support=True)
        for reader in (self.boss_account, staff):
            self.assertEqual(self.fetch(self.url(msg.image, reader)).status_code, 200)
        for reader in (self.account, account_for(make_console_user(['order:view']))):
            self.assertEqual(self.url(msg.image, reader), '')

    def test_direct_media_urls_and_encoded_private_paths_are_blocked(self):
        for prefix in ('reports', 'reports/entry', 'reports/results', 'reports/completion', 'recharges', 'chat', 'escorts/cheat_proofs', 'unknown'):
            path = self.root / prefix / 'test.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image_file().read())
            self.assertEqual(self.fetch('/media/' + prefix + '/test.png').status_code, 404)
            self.assertEqual(build_media_url_from_path(None, '/media/' + prefix + '/test.png'), '')
        for path in ('/media/%72eports/entry/test.png', '/media/banners/../reports/entry/test.png', '/media/banners/%2e%2e/reports/entry/test.png'):
            self.assertEqual(self.fetch(path).status_code, 404)

    def test_public_assets_keep_working(self):
        path = self.root / 'banners' / 'cover.png'
        path.parent.mkdir()
        path.write_bytes(image_file().read())
        self.assertEqual(self.fetch('/media/banners/cover.png').status_code, 200)
        with override_settings(MEDIA_BASE_URL='https://assets.example.test'):
            self.assertEqual(build_media_url_from_path(None, '/media/banners/cover.png'), 'https://assets.example.test/media/banners/cover.png')

    def test_oversized_private_image_is_bounded_without_changing_the_evidence(self):
        source = BytesIO()
        Image.frombytes('RGB', (900, 900), Random(3).randbytes(900 * 900 * 3)).save(source, 'JPEG', quality=95)
        raw = source.getvalue()
        self.assertGreater(len(raw), 200_000)
        self.report.entry_image.save('large.jpg', SimpleUploadedFile('large.jpg', raw, content_type='image/jpeg'))
        response = self.fetch(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.body), 200_000)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(Path(self.report.entry_image.path).read_bytes(), raw)
        self.assertEqual(self.url(account=self.other), '')

    def test_oversized_public_avatar_uses_a_small_display_copy(self):
        source = BytesIO()
        Image.frombytes('RGB', (900, 900), Random(4).randbytes(900 * 900 * 3)).save(source, 'JPEG', quality=95)
        self.account.avatar.save('large.jpg', SimpleUploadedFile('large.jpg', source.getvalue(), content_type='image/jpeg'))
        url = build_media_url(None, self.account.avatar)
        self.assertIn('/thumbnails/v4/', url)
        response = self.fetch(url)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.body), 200_000)
        self.assertEqual(Path(self.account.avatar.path).read_bytes(), source.getvalue())

    def test_invalid_file_content_and_missing_file_do_not_stream(self):
        url = self.url()
        Path(self.report.entry_image.path).write_text('<svg onload="alert(1)"/>')
        self.assertEqual(self.fetch(url).status_code, 404)
        Path(self.report.entry_image.path).unlink()
        self.assertEqual(self.fetch(url).status_code, 404)

    def test_signed_ticket_cannot_select_unregistered_model_or_field(self):
        for label, field in (('club_accounts.clubaccount', 'password'), ('wallet.providerreport', 'description')):
            ticket = signing.dumps({'model': label, 'field': field, 'pk': self.report.pk, 'account': self.account.pk}, salt=SALT)
            self.assertEqual(self.fetch(f'/api/media/private/{ticket}/').status_code, 404)

    def test_signed_ticket_cannot_escape_storage_root(self):
        self.report.entry_image = '../outside.png'
        self.report.save(update_fields=['entry_image'])
        self.assertEqual(self.fetch(self.url()).status_code, 404)

    def authenticate(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ticket(self, url):
        return url.rstrip('/').rsplit('/', 1)[-1]

    def test_expired_image_can_be_renewed_by_same_authenticated_reader(self):
        with patch('django.core.signing.time.time', return_value=1000):
            old = self.url()
        self.authenticate(self.account)
        result = self.client.post('/api/media/renew/', {'ticket': self.ticket(old)}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertNotEqual(result.data['data']['url'], old)
        self.assertEqual(self.fetch(result.data['data']['url']).status_code, 200)

    def test_anonymous_cannot_renew_a_capability(self):
        response = self.client.post('/api/media/renew/', {'ticket': self.ticket(self.url())}, format='json')
        self.assertIn(response.status_code, (401, 403))

    def test_other_account_cannot_extend_a_copied_capability(self):
        old = self.url()
        self.authenticate(self.other)
        self.assertEqual(self.client.post('/api/media/renew/', {'ticket': self.ticket(old)}, format='json').status_code, 404)

    def test_changed_file_cannot_be_renewed_from_old_reference(self):
        old = self.url()
        self.report.entry_image.save('replacement.png', image_file())
        self.authenticate(self.account)
        self.assertEqual(self.client.post('/api/media/renew/', {'ticket': self.ticket(old)}, format='json').status_code, 404)

    def test_revoked_staff_cannot_renew_grant(self):
        staff = make_console_user(['report:view'])
        reader = account_for(staff)
        old = self.url(account=reader)
        self.authenticate(reader)
        staff.admin_membership.roles.clear()
        self.assertEqual(self.client.post('/api/media/renew/', {'ticket': self.ticket(old)}, format='json').status_code, 404)

    def test_public_prefixes_match_both_gateway_allowlists(self):
        from common.private_media import PUBLIC_PREFIXES
        root = next((parent for parent in Path(__file__).resolve().parents
                     if (parent / 'deploy/Caddyfile').is_file()), None)
        if root is None:
            self.skipTest('Gateway files are validated on the source checkout')
        for name in ('Caddyfile', 'Caddyfile.local-validation'):
            line = next(line for line in (root / 'deploy' / name).read_text().splitlines() if '@public_media path' in line)
            self.assertEqual(set(line.split()[2:]), {f'/media/{prefix}*' for prefix in PUBLIC_PREFIXES})
