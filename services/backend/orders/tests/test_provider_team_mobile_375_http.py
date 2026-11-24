"""Opt-in: real team invitation confirm/withdraw flows against isolated Django HTTP."""
import os
from datetime import timedelta
from unittest import skipUnless

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
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        detail = client.get(f"/api/orders/orders/{invitations[0]['orderId']}/").data['data']
        self.assertFalse(detail['applications'][0]['ready'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        confirmed = client.post(f"/api/orders/orders/{invitations[0]['orderId']}/applications/{invitations[0]['applicationId']}/action/",
                                {'action': 'CONFIRM_TEAM'}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        detail = client.get(f"/api/orders/orders/{invitations[0]['orderId']}/").data['data']
        self.assertTrue(detail['applications'][0]['ready'])
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
        declined = client.post(f"/api/orders/orders/{invitations[1]['orderId']}/applications/{invitations[1]['applicationId']}/action/",
                               {'action': 'WITHDRAW'}, format='json')
        self.assertEqual(declined.data['code'], 0, declined.data)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        detail = client.get(f"/api/orders/orders/{invitations[1]['orderId']}/").data['data']
        self.assertEqual(detail['applications'], [])
        first = OrderApplication.objects.get(pk=invitations[0]['applicationId'])
        second = OrderApplication.objects.get(pk=invitations[1]['applicationId'])
        self.assertTrue(first.partner_confirmed)
        self.assertEqual(second.status, 'WITHDRAWN')
