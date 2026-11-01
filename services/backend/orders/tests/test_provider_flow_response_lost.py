"""Real paid orders remain recoverable when the client loses an action response."""
import base64
import json
import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings, tag
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ProviderFlowResponseLostTest(LiveServerTestCase):
    def test_team_withdrawal_response_loss_is_readable_to_both_members(self):
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
        for _ in range(3):
            provider = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'lost-team-withdraw'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        response = client.post(f'/api/orders/orders/{order.pk}/grab/', {
            'team_size': 2, 'partner_id': accounts[1].pk,
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        invitation = OrderApplication.objects.get(order=order, applicant=accounts[0], partner=accounts[1])
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_TEAM_DETAIL_WITHDRAW_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'partnerToken': issue_account_tokens(accounts[1])[0],
            'orderId': order.pk, 'applicationId': invitation.pk,
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/application-detail.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'WITHDRAWN')
        path = f'/api/orders/orders/{order.pk}/applications/{invitation.pk}/mine/'
        for account in accounts[:2]:
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            detail = client.get(path)
            self.assertEqual(detail.data['data']['closed_reason'], '报名已撤回。')
            self.assertFalse(detail.data['data']['can_confirm'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        self.assertEqual(client.get(path).status_code, 404)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        self.assertFalse(client.get(f'/api/orders/orders/{order.pk}/').data['data']['applications'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        self.assertEqual(client.post(f'/api/orders/orders/{order.pk}/grab/', {
            'team_size': 2, 'partner_id': accounts[1].pk,
        }, format='json').data['code'], 0)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, 'WITHDRAWN')
        current = OrderApplication.objects.get(order=order, applicant=accounts[0], status='PENDING')
        self.assertNotEqual(current.pk, invitation.pk)
        self.assertFalse(current.partner_confirmed)

    def test_team_application_and_confirmation_response_loss(self):
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
        for name in ('隔离申请人', '隔离队友'):
            provider = make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                                     pass_expires_at=timezone.now() + timedelta(days=1))
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'lost-team-application'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_TEAM_APPLICATION_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'providerToken': issue_account_tokens(accounts[0])[0],
            'partnerToken': issue_account_tokens(accounts[1])[0], 'bossToken': issue_account_tokens(boss_account)[0],
            'partnerName': '隔离队友', 'orderId': order.pk,
        }, ensure_ascii=False)}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertEqual(order.status, 'GRABBED')
        application = OrderApplication.objects.get(order=order, applicant=accounts[0], partner=accounts[1])
        self.assertTrue(application.partner_confirmed)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 50000 - order.amount)
        # A second real team application exercises the invitation-detail entry point.
        response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'lost-team-detail'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        other = Order.objects.get(pk=response.data['data']['id'])
        Order.objects.filter(pk=other.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        response = client.post(f'/api/orders/orders/{other.pk}/grab/', {
            'team_size': 2, 'partner_id': accounts[1].pk,
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        invitation = OrderApplication.objects.get(order=other, applicant=accounts[0], partner=accounts[1])
        env = {**os.environ, 'XA_TEAM_DETAIL_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'partnerToken': issue_account_tokens(accounts[1])[0],
            'orderId': other.pk, 'applicationId': invitation.pk,
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/application-detail.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        invitation.refresh_from_db()
        self.assertTrue(invitation.partner_confirmed)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        detail = client.get(f'/api/orders/orders/{other.pk}/')
        self.assertTrue(detail.data['data']['applications'][0]['ready'])
        selected = client.post(f'/api/orders/orders/{other.pk}/applications/{invitation.pk}/select/', {}, format='json')
        self.assertEqual(selected.data['code'], 0, selected.data)
        self.assertEqual(selected.data['data']['status'], 'GRABBED')

    @tag('filedb')
    def test_team_partial_evidence_response_loss(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2)
        accounts = []
        for _ in range(2):
            provider = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(provider)
            link_legacy_relations(provider, account)
            provider.escort_profile.account = account
            provider.escort_profile.save(update_fields=['account'])
            provider.escort_profile.service_items.add(service)
            accounts.append(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        response = client.post('/api/orders/create/', {'service_id': service.pk,
            'provider_ids': [account.pk for account in accounts], 'client_request_id': 'team-response-loss'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['id'])
        self.assertEqual((order.status, order.escort_mode), ('GRABBED', 'DOUBLE'))
        image = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image, format='PNG')
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_PROVIDER_TEAM_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'providerToken': issue_account_tokens(accounts[0])[0],
            'teammateToken': issue_account_tokens(accounts[1])[0], 'orderId': order.pk,
            'orderNo': order.order_no, 'image': base64.b64encode(image.getvalue()).decode(),
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        reports = list(ProviderReport.objects.filter(order=order))
        self.assertEqual(len(reports), 2)
        self.assertTrue(all(report.entry_image and report.completion_image for report in reports))
        self.assertIsNone(order.settled_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Transaction.objects.get(order=order, tx_type='PAY').amount, -order.amount)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 50000 - order.amount)

    def test_application_start_and_completion_response_loss(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required for the provider page')
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        provider = make_provider(deposit_mode='EARNINGS')
        provider_account = get_or_create_account_for_legacy_user(provider)
        link_legacy_relations(provider, provider_account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=1)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        def create(mode):
            response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': mode, 'client_request_id': 'lost-flow-' + mode}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            order = Order.objects.get(pk=response.data['data']['id'])
            Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
            return order
        application = create('BOSS_SELECT')
        service_order = create('AUTO_GRAB')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        response = client.post(f'/api/orders/orders/{service_order.pk}/grab/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        image = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image, format='PNG')
        repo = Path(settings.BASE_DIR).parent.parent
        env = {**os.environ, 'XA_PROVIDER_FLOW_LOST_HTTP': json.dumps({
            'base': self.live_server_url, 'providerToken': issue_account_tokens(provider_account)[0],
            'serviceNo': service_order.order_no, 'image': base64.b64encode(image.getvalue()).decode(),
        })}
        result = subprocess.run([node, '--test', str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                                env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        service_order.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(application.status, 'PENDING')
        self.assertEqual(OrderApplication.objects.filter(order=application, applicant=provider_account, status='PENDING').count(), 1)
        self.assertEqual(service_order.status, 'COMPLETED')
        report = ProviderReport.objects.get(order=service_order, provider_account=provider_account)
        self.assertTrue(report.entry_image and report.completion_image)
        self.assertIsNone(service_order.settled_at)
        self.assertEqual(Transaction.objects.filter(order__in=[application, service_order], tx_type='PAY').count(), 2)
        self.assertEqual(Wallet.objects.get(pk=wallet.pk).balance, 30000)
