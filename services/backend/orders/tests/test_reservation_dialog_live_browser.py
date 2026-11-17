"""An open provider reservation dialog follows another participant's action."""
import json
import os
from datetime import timedelta
from unittest import skipUnless
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import SystemConfig


@skipUnless(os.environ.get('XA_RESERVATION_DIALOG_BROWSER') == '1', 'Requires isolated ASGI and H5 build')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationDialogLiveBrowserTest(APITransactionTestCase):
    def test_boss_cancellation_closes_open_provider_rejection(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('An isolated file-backed database is required')
        boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in zip((boss, provider), accounts): link_legacy_relations(user, account)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        SystemConfig.objects.update_or_create(key='reservation_deadlines', defaults={'value': json.dumps(
            {'confirmation_minutes': 120, 'payment_minutes': 120, 'version': 1,
             'slot_minutes': 15, 'min_duration_minutes': 15, 'max_duration_minutes': 720, 'max_advance_days': 30})})
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        rid = response.data['data']['id']
        other_start = start + timedelta(hours=2)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': other_start.isoformat(), 'end_time': (other_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        other_id = response.data['data']['id']
        third_start = start + timedelta(hours=4)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': third_start.isoformat(), 'end_time': (third_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        third_id = response.data['data']['id']
        provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        self.assertEqual(provider_client.post(f'/api/orders/reservations/{third_id}/confirm/', {}, format='json').data['code'], 0)
        fourth_start = start + timedelta(hours=6)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': fourth_start.isoformat(), 'end_time': (fourth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        fourth_id = response.data['data']['id']
        self.assertEqual(provider_client.post(f'/api/orders/reservations/{fourth_id}/confirm/', {}, format='json').data['code'], 0)
        fifth_start = start + timedelta(hours=8)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': fifth_start.isoformat(), 'end_time': (fifth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        fifth_id = response.data['data']['id']
        sixth_start = start + timedelta(hours=10)
        response = client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk, 'service_id': service.pk,
            'start_time': sixth_start.isoformat(), 'end_time': (sixth_start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        sixth_id = response.data['data']['id']
        self.assertEqual(client.post(f'/api/orders/reservations/{other_id}/cancel/',
            {'reason': '另一预约计划有变'}, format='json').data['code'], 0)
        self.assertEqual(client.post(f'/api/orders/reservations/{rid}/cancel/',
            {'reason': '老板计划有变'}, format='json').data['code'], 0)
        self.assertEqual(provider_client.post(f'/api/orders/reservations/{sixth_id}/reject/',
            {'reason': '填写中的原因'}, format='json').data['code'], 0)
        detail = client.get(f'/api/orders/reservations/{sixth_id}/').data['data']
        self.assertEqual((detail['status'], detail['reject_reason']), ('REJECTED', '填写中的原因'))
        for reservation_id in (fifth_id, fourth_id, third_id):
            cancelled = client.post(f'/api/orders/reservations/{reservation_id}/cancel/',
                {'reason': '老板改变安排'}, format='json')
            self.assertEqual(cancelled.data['code'], 0, cancelled.data)
            self.assertEqual(cancelled.data['data']['status'], 'CANCELLED', cancelled.data)
