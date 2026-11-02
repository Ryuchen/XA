"""Opt-in: render the built provider H5 against isolated, real Django HTTP."""
import json
import os
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests.factories import make_provider, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_PROVIDER_MOBILE_375') == '1', 'Requires the isolated H5 build and browser')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CORS_ALLOW_ALL_ORIGINS=True, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderTeamMobile375Test(LiveServerTestCase):
    host = '127.0.0.1'
    port = 19166

    def test_confirm_and_withdraw_are_visible_to_boss(self):
        boss = make_user()
        applicant = make_provider(display_name='隔离申请人', deposit_mode='EARNINGS', pass_tier='BLACK',
                                  pass_expires_at=timezone.now() + timedelta(days=1))
        partner = make_provider(display_name='隔离队友', deposit_mode='EARNINGS', pass_tier='BLACK',
                                pass_expires_at=timezone.now() + timedelta(days=1))
        accounts = []
        for user in (boss, applicant, partner):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            if user != boss:
                user.escort_profile.account = account
                user.escort_profile.save(update_fields=['account'])
            accounts.append(account)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = accounts[0]
        wallet.save(update_fields=['account'])
        service = make_service(name='隔离双陪服务', price=10000, billing_provider_count=2)
        for user in (applicant, partner):
            user.escort_profile.service_items.add(service)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient()
        invitations = []
        for number in (1, 2):
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            response = client.post('/api/orders/create/', {'service_id': service.pk, 'dispatch_route': 'PUBLIC',
                'dispatch_mode': 'BOSS_SELECT', 'client_request_id': f'mobile-team-375-{number}'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            order = Order.objects.get(pk=response.data['data']['id'])
            Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            response = client.post(f'/api/orders/orders/{order.pk}/grab/',
                                   {'team_size': 2, 'partner_id': accounts[2].pk}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            invitation = OrderApplication.objects.get(order=order, applicant=accounts[1], partner=accounts[2])
            invitations.append({'orderId': order.pk, 'applicationId': invitation.pk})
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/provider-team-mobile-375-live.cjs')],
            input=json.dumps({'partnerToken': tokens[2], 'bossToken': tokens[0], 'partnerId': str(accounts[2].pk),
                              'invitations': invitations}), text=True, encoding='utf-8', capture_output=True,
            timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        first = OrderApplication.objects.get(pk=invitations[0]['applicationId'])
        second = OrderApplication.objects.get(pk=invitations[1]['applicationId'])
        self.assertTrue(first.partner_confirmed)
        self.assertEqual(second.status, 'WITHDRAWN')
