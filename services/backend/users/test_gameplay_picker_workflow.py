"""Boss selection agrees with actual qualification and order creation."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership

from orders.models import GameCategory, Gameplay, Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from users.models import ProviderQualification


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True)
class GameplayPickerWorkflowTest(APITestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_unclassified_legacy_service_uses_same_eligibility_across_entry_and_order(self):
        profile = self.provider.escort_profile
        profile.recommendation_slot = 1
        profile.save(update_fields=['recommendation_slot'])
        profile.service_items.clear()
        self.assertIsNone(self.service.game_category_id)
        self.assertEqual(profile.order_acceptance_error(self.service), '')
        self.ok(self.recharge())
        self.login(self.boss)
        provider_id = self.accounts[self.provider.pk].pk
        detail = self.ok(self.client.get(f'/api/users/escorts/{provider_id}/', {'service_item': self.service.pk}))
        self.assertTrue(detail['can_order'], detail)
        catalog = self.ok(self.client.get(f'/api/users/escorts/{provider_id}/', {'eligible_catalog': '1'}))
        self.assertIn(self.service.pk, catalog['eligible_service_item_ids'])
        listed = self.ok(self.client.get('/api/users/escorts/', {'service_item': self.service.pk, 'picker': 1}))
        self.assertIn(provider_id, [row['id'] for row in listed])
        shortcuts = self.ok(self.client.get('/api/users/escorts/', {'service_item': self.service.pk, 'shortcuts': 1}))
        self.assertIn(provider_id, [row['id'] for row in shortcuts['recommended']])
        payload = {'service_id': self.service.pk, 'provider_id': provider_id,
                   'support_contact_id': self.card.pk}
        quote = self.ok(self.client.post('/api/orders/quote/', payload, format='json'))
        created = self.ok(self.client.post('/api/orders/create/', {
            **payload, 'expected_amount': quote['amount'], 'client_request_id': 'legacy-unclassified-selection'}, format='json'))
        self.assertEqual(Order.objects.get(pk=created['id']).provider_account_id, provider_id)

    def test_classifying_legacy_service_requires_the_game_skill(self):
        self.service.game_category = GameCategory.objects.create(name='隔离技能资格游戏')
        self.service.save(update_fields=['game_category'])
        self.provider.escort_profile.service_items.clear()
        self.login(self.boss)
        provider_id = self.accounts[self.provider.pk].pk
        detail = self.ok(self.client.get(f'/api/users/escorts/{provider_id}/', {'service_item': self.service.pk}))
        self.assertFalse(detail['can_order'])
        self.assertIn('技能', detail['order_unavailable_reason'])
        listed = self.ok(self.client.get('/api/users/escorts/', {'service_item': self.service.pk, 'picker': 1}))
        self.assertNotIn(provider_id, [row['id'] for row in listed])
        quote = self.client.post('/api/orders/quote/', {'service_id': self.service.pk, 'provider_id': provider_id}, format='json')
        self.assertNotEqual(quote.data['code'], 0)

    def test_higher_gameplay_skill_is_selectable_and_can_be_ordered(self):
        game = GameCategory.objects.create(name='隔离玩法资格测试')
        gameplay = Gameplay.objects.create(name='隔离护航玩法', code='picker-gameplay-test')
        self.service.game_category = game
        self.service.gameplay = gameplay
        self.service.catalog_family = ''
        self.service.provider_kind = 'GUARD'
        self.service.provider_grade = 'MASTER'
        self.service.platform = 'PC'
        self.service.security_level = 'CONFIDENTIAL'
        self.service.save()
        higher = make_service(game_category=game, gameplay=gameplay, catalog_family='',
            provider_kind='GUARD', provider_grade='LEGEND', platform='PC', security_level='CONFIDENTIAL')
        profile = self.provider.escort_profile
        profile.service_items.set([higher])
        profile.game_categories.set([game])
        ProviderQualification.objects.create(profile=profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level='CONFIDENTIAL', grade='LEGEND')
        self.assertEqual(profile.order_acceptance_error(self.service), '')
        self.ok(self.recharge())
        self.login(self.boss)
        params = {'service_item': self.service.pk}
        listed = self.ok(self.client.get('/api/users/escorts/', params))
        self.assertIn(self.accounts[self.provider.pk].pk, [row['id'] for row in listed])
        detail = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/', params))
        self.assertTrue(detail['can_order'], detail)
        self.assertIn(self.service.pk, detail['eligible_service_item_ids'])
        self.service.catalog_family = higher.catalog_family = '隔离玩法系列'
        self.service.save(update_fields=['catalog_family'])
        higher.save(update_fields=['catalog_family'])
        wrong_scope = make_service(game_category=game, gameplay=gameplay, catalog_family='隔离玩法系列',
            provider_kind='GUARD', provider_grade='MASTER', platform='MOBILE', security_level='TOP_SECRET')
        detail = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/', params))
        self.assertEqual(set(detail['eligible_service_item_ids']), {self.service.pk, higher.pk})
        self.assertNotIn(wrong_scope.pk, detail['eligible_service_item_ids'])
        catalog = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/', {'eligible_catalog': '1'}))
        self.assertIn(self.service.pk, catalog['eligible_service_item_ids'])
        self.assertIn(higher.pk, catalog['eligible_service_item_ids'])
        self.assertNotIn(wrong_scope.pk, catalog['eligible_service_item_ids'])
        higher.is_active = False
        higher.save(update_fields=['is_active'])
        catalog = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/', {'eligible_catalog': '1'}))
        self.assertIn(self.service.pk, catalog['eligible_service_item_ids'])
        self.assertNotIn(higher.pk, catalog['eligible_service_item_ids'])
        higher.is_active = True
        higher.save(update_fields=['is_active'])
        profile.status = profile.Status.OFFLINE
        profile.save(update_fields=['status'])
        catalog = self.ok(self.client.get(f'/api/users/escorts/{self.accounts[self.provider.pk].pk}/', {'eligible_catalog': '1'}))
        self.assertFalse(catalog['can_order'])
        self.assertEqual(catalog['eligible_service_item_ids'], [])
        profile.status = profile.Status.AVAILABLE
        profile.save(update_fields=['status'])
        payload = {'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
                   'support_contact_id': self.card.pk}
        quote = self.ok(self.client.post('/api/orders/quote/', payload, format='json'))
        created = self.ok(self.client.post('/api/orders/create/', {
            **payload, 'expected_amount': quote['amount'], 'client_request_id': 'gameplay-picker-order'}, format='json'))
        self.assertEqual(Order.objects.get(pk=created['id']).provider_account_id, self.accounts[self.provider.pk].pk)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True)
class GameplayPickerNativeHTTPTest(LiveServerTestCase):
    def test_native_unclassified_service_picker_to_real_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the native host adapter')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider()
        boss_account, provider_account = (get_or_create_account_for_legacy_user(user) for user in (boss, provider))
        profile = provider.escort_profile
        profile.account = provider_account
        profile.recommendation_slot = 1
        profile.save(update_fields=['account', 'recommendation_slot'])
        service = make_service(price=10000)
        self.assertIsNone(service.game_category_id)
        self.assertFalse(profile.service_items.exists())
        staff = make_console_user(['wallet:recharge'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        credited = client.post('/api/admin/wallets/recharge/', {
            'account_id': boss_account.pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'native-legacy-picker', 'proof_image': flow.screenshot(),
            'client_request_id': 'native-legacy-picker-recharge'}, format='multipart')
        self.assertEqual(credited.data['code'], 0, credited.data)
        token, refresh = issue_account_tokens(boss_account)
        payload = {'base': self.live_server_url, 'service': service.pk, 'provider': provider_account.pk,
                   'legacy': True,
                   'session': {'token': token, 'refresh': refresh,
                               'user': {'id': str(boss_account.pk), 'role': 'customer', 'nickname': '测试老板'}}}
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-gameplay-picker-live.cjs')],
            input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        order = Order.objects.get(pk=json.loads(result.stdout)['order'])
        self.assertEqual((order.customer_account_id, order.provider_account_id, order.amount),
                         (boss_account.pk, provider_account.pk, 10000))

    def test_native_service_and_checkout_use_real_eligibility_and_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the native host adapter')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider()
        boss_account, provider_account = (get_or_create_account_for_legacy_user(user) for user in (boss, provider))
        profile = provider.escort_profile
        profile.account = provider_account
        profile.save(update_fields=['account'])
        game = GameCategory.objects.create(name='原生选人测试')
        gameplay = Gameplay.objects.create(name='原生玩法测试', code='native-picker-gameplay')
        common = {'game_category': game, 'gameplay': gameplay, 'catalog_family': '原生资格系列',
                  'provider_kind': 'GUARD', 'platform': 'PC', 'security_level': 'CONFIDENTIAL'}
        service = make_service(price=10000, provider_grade='MASTER', **common)
        higher = make_service(price=15000, provider_grade='LEGEND', **common)
        wrong_scope = make_service(price=8000, game_category=game, gameplay=gameplay,
            catalog_family='原生资格系列', provider_kind='GUARD', provider_grade='MASTER',
            platform='MOBILE', security_level='TOP_SECRET')
        profile.service_items.set([higher]); profile.game_categories.set([game])
        ProviderQualification.objects.create(profile=profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level='CONFIDENTIAL', grade='LEGEND')
        staff = make_console_user(['wallet:recharge'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        credited = client.post('/api/admin/wallets/recharge/', {
            'account_id': boss_account.pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'native-gameplay-picker', 'proof_image': flow.screenshot(),
            'client_request_id': 'native-gameplay-picker-recharge'}, format='multipart')
        self.assertEqual(credited.data['code'], 0, credited.data)
        token, refresh = issue_account_tokens(boss_account)
        payload = {'base': self.live_server_url, 'service': service.pk, 'provider': provider_account.pk,
                   'unsupported': wrong_scope.pk,
                   'session': {'token': token, 'refresh': refresh,
                               'user': {'id': str(boss_account.pk), 'role': 'customer', 'nickname': '测试老板'}}}
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-gameplay-picker-live.cjs')],
            input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=60, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        order = Order.objects.get(pk=json.loads(result.stdout)['order'])
        self.assertEqual((order.customer_account_id, order.provider_account_id, order.amount),
                         (boss_account.pk, provider_account.pk, 10000))
