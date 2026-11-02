"""A returning checkout reads current service configuration before purchase."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import GameCategory, Gameplay, Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import ProviderQualification


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class NativeCheckoutServiceRefreshTest(LiveServerTestCase):
    def test_double_checkout_confirms_selected_people_before_real_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account); make_wallet(boss, balance=100000)
        service = make_service(name='隔离双陪结算', price=10000, billing_provider_count=2)
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS') for name in ('隔离陪玩甲', '隔离陪玩乙')]
        accounts = [get_or_create_account_for_legacy_user(provider) for provider in providers]
        for provider, provider_account in zip(providers, accounts):
            link_legacy_relations(provider, provider_account)
            provider.escort_profile.account = provider_account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
        token, refresh = issue_account_tokens(account)
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        available = client.get('/api/users/escorts/', {'service_item': service.pk}).data
        self.assertEqual(available['code'], 0, available)
        names = [next(row['nickname'] for row in available['data'] if row['id'] == item.pk) for item in accounts]
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-confirm-team-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk,
                'providers': [item.pk for item in accounts], 'names': names,
                'session': {'token': token, 'refresh': refresh, 'user': {'id': str(account.pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)), {item.pk for item in accounts})

    def _difficulty_change_requires_reconfirmation(self, race, change_grade=False):
        cache.clear()
        self.addCleanup(cache.clear)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        game = GameCategory.objects.create(name='隔离难度变更游戏')
        gameplay = Gameplay.objects.create(name='隔离难度变更玩法', code='checkout-difficulty-change')
        service = make_service(price=10000, game_category=game, gameplay=gameplay,
            provider_kind='GUARD', provider_grade='MASTER', platform='PC', security_level='CONFIDENTIAL')
        token, refresh = issue_account_tokens(account)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-requirement-change-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'adminToken': admin_token, 'race': race,
                **({'patch': {'provider_grade': 'LEGEND'}, 'key': 'provider_grade', 'before': 'MASTER', 'after': 'LEGEND'} if change_grade else {}),
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        security = 'CONFIDENTIAL' if change_grade else 'TOP_SECRET'
        grade = 'LEGEND' if change_grade else 'MASTER'
        self.assertEqual((order.amount, order.gameplay_snapshot['security_level']), (10000, security))
        self.assertEqual((order.provider_requirement_snapshot['security_level'], order.provider_requirement_snapshot['grade']), (security, grade))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 40000)
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        profile = provider.escort_profile
        profile.account = provider_account
        profile.save(update_fields=['account'])
        profile.service_items.add(service)
        profile.game_categories.add(game)
        ProviderQualification.objects.create(profile=profile, game_category=game,
            provider_kind='GUARD', platform='PC', security_level=security, grade=grade)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        # Exercise the real pass delay; only the test clock is advanced.
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(seconds=profile.order_visibility_delay_seconds + 1)):
            response = client.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

    def test_foreground_difficulty_change_requires_reconfirmation(self):
        self._difficulty_change_requires_reconfirmation(False)

    def test_difficulty_change_during_confirmation_rejected_before_debit(self):
        self._difficulty_change_requires_reconfirmation(True)

    def test_grade_change_requires_reconfirmation(self):
        self._difficulty_change_requires_reconfirmation(False, change_grade=True)

    def test_package_change_during_confirmation_is_rejected_before_debit(self):
        cache.clear()
        self.addCleanup(cache.clear)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss = make_user()
        staff = make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(account)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-package-change-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'adminToken': admin_token, 'race': True,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(Order.objects.filter(customer_account=account).count(), 1)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.required_provider_count), (10000, 2))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 40000)

    def test_foreground_package_change_at_same_total_requires_review(self):
        cache.clear()
        self.addCleanup(cache.clear)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss = make_user()
        staff = make_console_user(['service:view', 'service:edit'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        token, refresh = issue_account_tokens(account)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-package-change-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'adminToken': admin_token,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.required_provider_count), (10000, 2))

    def test_admin_changes_price_and_package_while_checkout_is_backgrounded(self):
        cache.clear()
        self.addCleanup(cache.clear)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native page controller')
        boss, staff = make_user(), make_console_user(['service:view', 'service:edit', 'order:view', 'order:traffic'])
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        admin = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = admin
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        token, refresh = issue_account_tokens(account)
        admin_token, _ = issue_account_tokens(admin)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-checkout-service-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'serviceId': service.pk, 'providerId': provider_account.pk, 'adminToken': admin_token,
                'session': {'token': token, 'refresh': refresh,
                            'user': {'id': str(account.pk), 'role': 'customer', 'nickname': '测试老板'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=account)
        self.assertEqual((order.amount, order.game_rounds, order.game_region, order.game_nickname),
                         (24000, 1, '本地大区', '本地昵称'))
        self.assertEqual(order.required_provider_count, 2)
