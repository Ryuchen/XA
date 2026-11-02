"""Exercise the selected invitation page using a real order and HTTP API."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_provider, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, CORS_ALLOW_ALL_ORIGINS=True)
class SelectedInvitationNavigationTest(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19166 if os.environ.get('XA_CANCELLED_TEAM_BROWSER') == '1' else 0

    def test_cancelled_team_application_remains_readable_for_both_members(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2)
        accounts = []
        for _ in range(2):
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        outsider = make_provider()
        outsider_account = get_or_create_account_for_legacy_user(outsider)
        link_legacy_relations(outsider, outsider_account)
        tokens = [issue_account_tokens(account)[0] for account in (boss_account, *accounts)]
        outsider_token = issue_account_tokens(outsider_account)[0]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'cancelled-team-navigation'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        result = client.post(f'/api/orders/orders/{order_id}/grab/',
                             {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        application = OrderApplication.objects.get(order_id=order_id, applicant=accounts[0])
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_TEAM_CANCELLED_HTTP': json.dumps({'base': self.live_server_url,
            'bossToken': tokens[0], 'tokens': tokens[1:], 'outsiderToken': outsider_token,
            'orderId': order_id, 'applicationId': application.pk})}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/application-detail.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if os.environ.get('XA_CANCELLED_TEAM_BROWSER') == '1':
            result = subprocess.run([node, str(repo / 'scripts/provider-cancelled-team-375.cjs')],
                input=json.dumps({'base': self.live_server_url, 'tokens': tokens[1:],
                                  'ids': [str(account.pk) for account in accounts],
                                  'orderId': order_id, 'applicationId': application.pk}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_selection_page_shows_rejected_order_back_in_pool(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                                   pass_expires_at=timezone.now() + timedelta(days=1))
        boss_account, provider_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        boss_token, provider_token = [issue_account_tokens(account)[0] for account in (boss_account, provider_account)]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'rejected-selection-navigation'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        result = client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        application = OrderApplication.objects.get(order_id=order_id, applicant=provider_account)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
        result = client.post(f'/api/orders/orders/{order_id}/applications/{application.pk}/select/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
        result = client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '时间冲突'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_REJECTED_SELECTION_HTTP': json.dumps({'base': self.live_server_url,
            'providerToken': provider_token, 'orderId': order_id, 'applicationId': application.pk})}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/application-detail.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_selected_team_member_can_open_the_order(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2)
        accounts = []
        for _ in range(2):
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        tokens = [issue_account_tokens(account)[0] for account in (boss_account, *accounts)]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'selected-invitation-navigation'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        result = client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        invitation = OrderApplication.objects.get(order=order, applicant=accounts[0], status='PENDING')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        result = client.post(f'/api/orders/orders/{order.pk}/applications/{invitation.pk}/action/', {'action': 'CONFIRM_TEAM'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        result = client.post(f'/api/orders/orders/{order.pk}/applications/{invitation.pk}/select/', {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_TEAM_SELECTED_HTTP': json.dumps({'base': self.live_server_url,
            'partnerToken': tokens[2], 'orderId': order.pk, 'applicationId': invitation.pk})}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/application-detail.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
