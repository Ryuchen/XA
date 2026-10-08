"""Self-service profile writes, media and boss-visible identity stay consistent."""
from io import BytesIO
from tempfile import TemporaryDirectory

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import GameCategory
from orders.tests.factories import make_provider, make_service, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class ProviderProfileTest(APITestCase):
    url = '/api/users/escorts/me/'

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-provider-media-')
        self.addCleanup(folder.cleanup)
        config = override_settings(MEDIA_ROOT=folder.name)
        config.enable()
        self.addCleanup(config.disable)
        self.user = make_provider(display_name='原昵称', price_per_hour=205)
        self.account = get_or_create_account_for_legacy_user(self.user)
        self.profile = self.user.escort_profile
        self.profile.account = self.account
        self.profile.save(update_fields=['account'])
        self.game = GameCategory.objects.create(name='测试游戏')
        self.service = make_service(game_category=self.game, price=9900)
        self.profile.service_items.set([self.service])
        self.profile.game_categories.set([self.game])
        self.authenticate(self.account)

    def authenticate(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def image(self):
        data = BytesIO()
        Image.new('RGB', (12, 12), '#ecc1cb').save(data, format='PNG')
        return SimpleUploadedFile('profile.png', data.getvalue(), content_type='image/png')

    def voice(self):
        import wave
        data = BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b'\0\0' * 800)
        return SimpleUploadedFile('voice.wav', data.getvalue(), content_type='audio/wav')

    def test_invalid_services_do_not_partially_save_profile_or_erase_skills(self):
        inactive = make_service(is_active=False)
        for ids in ([self.service.pk, 'oops'], [999999], [inactive.pk], [True], [1.2], [2 ** 80], None):
            with self.subTest(ids=ids):
                res = self.client.patch(self.url, {'display_name': '不应保存', 'service_items': ids}, format='json')
                self.assertEqual(res.data['code'], 400, res.data)
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.display_name, '原昵称')
                self.assertEqual(list(self.profile.service_items.all()), [self.service])
                self.assertEqual(list(self.profile.game_categories.all()), [self.game])

    def test_valid_skill_update_and_explicit_clear_sync_categories(self):
        game = GameCategory.objects.create(name='另一游戏')
        service = make_service(game_category=game)
        for ids, expected in (([service.pk, service.pk], [game]), ([], [])):
            res = self.client.patch(self.url, {'service_items': ids, 'city': '杭州'}, format='json')
            self.assertEqual(res.data['code'], 0, res.data)
            self.assertEqual(list(self.profile.game_categories.all()), expected)
        self.assertFalse(self.profile.service_items.exists())

    def test_malformed_text_gender_or_remove_flag_returns_validation_error(self):
        for data in ({'city': []}, {'bio': 5}, {'display_name': None}, {'gender': []}, {'remove_voice_card': []}):
            with self.subTest(data=data):
                res = self.client.patch(self.url, data, format='json')
                self.assertEqual(res.data['code'], 400, res.data)

    def test_invalid_assets_do_not_save_other_fields(self):
        cases = [('avatar', 'bad.png', b'not an image'), ('cheat_proof', 'bad.png', b'not an image'),
                 ('voice_card', 'voice.html', b'<html>bad</html>'), ('voice_card', 'empty.mp3', b''),
                 ('voice_card', 'large.wav', b'0' * (10 * 1024 * 1024 + 1))]
        for field, name, content in cases:
            with self.subTest(field=field, name=name):
                res = self.client.post(self.url, {field: SimpleUploadedFile(name, content), 'city': '不应保存'}, format='multipart')
                self.assertEqual(res.data['code'], 400, res.data)
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.city, '')

    def test_voice_avatar_and_reference_hourly_price_reach_boss_and_removal_is_owned(self):
        other = make_provider(voice_card='escorts/voice_cards/other.wav')
        res = self.client.post(self.url, {'voice_card': self.voice(), 'avatar': self.image(), 'account_id': other.pk}, format='multipart')
        self.assertEqual(res.data['code'], 0, res.data)
        voice_url = res.data['data']['voice_card_url']
        avatar_url = res.data['data']['avatar_url']
        self.assertTrue(voice_url.endswith('.wav'))
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.account.avatar_url, avatar_url)
        self.assertEqual(self.user.avatar_url, avatar_url)
        self.authenticate(get_or_create_account_for_legacy_user(make_user()))
        rows = self.client.get('/api/users/escorts/', {'service_item': self.service.pk}).data['data']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['voice_card_url'], voice_url)
        self.assertEqual(rows[0]['avatar'], avatar_url)
        self.assertEqual(rows[0]['pricePerHour'], 205)  # independent of the 9900 package quote
        self.assertTrue(rows[0]['hasReferenceHourlyPrice'])
        for hourly_price, configured in ((0, True), (None, False)):
            self.profile.price_per_hour = hourly_price
            self.profile.save(update_fields=['price_per_hour'])
            row = self.client.get('/api/users/escorts/', {'service_item': self.service.pk}).data['data'][0]
            self.assertEqual(row['pricePerHour'], 0)
            self.assertEqual(row['hasReferenceHourlyPrice'], configured)
        self.assertEqual(self.client.patch(self.url, {'remove_voice_card': True}, format='json').data['code'], 403)
        self.authenticate(self.account)
        res = self.client.patch(self.url, {'remove_voice_card': True}, format='json')
        self.assertEqual(res.data['data']['voice_card_url'], '')
        other.escort_profile.refresh_from_db()
        self.assertEqual(other.escort_profile.voice_card.name, 'escorts/voice_cards/other.wav')

    def test_upload_and_remove_voice_together_is_rejected(self):
        res = self.client.post(self.url, {'voice_card': self.voice(), 'remove_voice_card': 'true'}, format='multipart')
        self.assertEqual(res.data['code'], 400)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.voice_card)
