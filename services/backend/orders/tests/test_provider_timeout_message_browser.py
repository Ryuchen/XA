"""A real timeout notice opens the closed team application in provider H5."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tasks import auto_cancel_pending_order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from site_messages.models import Message


@skipUnless(os.environ.get('XA_PROVIDER_TIMEOUT_H5_DIR'), 'Requires isolated H5 build and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderTimeoutMessageBrowserTest(LiveServerTestCase):
    def test_timeout_notice_opens_closed_application_for_both_providers(self):
        users = [make_user(), make_provider(display_name='隔离报名人'), make_provider(display_name='隔离队友')]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(name='隔离双陪套餐', price=10000, billing_provider_count=2)
        for user, account in zip(users[1:], accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.pass_tier = 'BLACK'
            user.escort_profile.pass_expires_at = timezone.now() + timedelta(days=1)
            user.escort_profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
            user.escort_profile.service_items.add(service)
        make_wallet(users[0], balance=50000)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        result = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
            'dispatch_mode': 'BOSS_SELECT', 'client_request_id': 'timeout-h5-team-isolated'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order_id = result.data['data']['id']
        Order.objects.filter(pk=order_id).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        result = client.post(f'/api/orders/orders/{order_id}/grab/', {'team_size': 2, 'partner_id': accounts[2].pk}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        application_id = OrderApplication.objects.get(order_id=order_id, applicant=accounts[1]).pk
        Order.objects.filter(pk=order_id).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order.run(order_id), 'cancelled')
        notices = [Message.objects.get(recipient_account=account, related_order_id=order_id, title='报名订单已取消').pk
                   for account in accounts[1:]]
        repo = Path(__file__).resolve().parents[4]
        result = subprocess.run(['node', str(repo / 'scripts/provider-timeout-message-375-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'root': os.environ['XA_PROVIDER_TIMEOUT_H5_DIR'],
                              'tokens': tokens[1:], 'ids': [str(account.pk) for account in accounts[1:]],
                              'notices': notices, 'orderId': order_id, 'applicationId': application_id}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
