"""Render the persisted reservation review against actual isolated HTTP data."""
import os
from datetime import timedelta
from unittest import skipUnless
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_RESERVATION_BROWSER') == '1', 'Requires isolated browser build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminReservationReviewBrowserTest(LiveServerTestCase):
    def test_conversion_confirmation_at_ipad_landscape(self):
        from orders.models import Order
        from wallet.models import Wallet
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in zip((boss, provider, staff), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[2]; staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = accounts[1]; provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000); wallet.account = accounts[0]; wallet.save(update_fields=['account'])
        service = make_service(name='隔离预约套餐', price=10000)
        boss_client, provider_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[1])[0])
        staff_client = APIClient()
        staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss_client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
            'service_id': service.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat()}, format='json').data
        self.assertEqual(created['code'], 0, created)
        booking = created['data']
        self.assertEqual(provider_client.post(f"/api/orders/reservations/{booking['id']}/confirm/", {}, format='json').data['code'], 0)
        quote = staff_client.get(f"/api/orders/reservations/{booking['id']}/quote/").data
        self.assertEqual(quote['code'], 0, quote)
        self.assertEqual(quote['data']['amount'], 10000)
        converted = staff_client.post(f"/api/orders/reservations/{booking['id']}/convert/",
            {'client_request_id': 'reservation-review-convert', 'expected_amount': quote['data']['amount'],
             'expected_service': quote['data']['service']}, format='json').data
        self.assertEqual(converted['code'], 0, converted)
        order = Order.objects.get(customer_account=accounts[0])
        self.assertEqual((order.amount, order.provider_account_id, order.status), (10000, accounts[1].pk, 'GRABBED'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 40000)
        for client in (boss_client, provider_client):
            reservation = client.get(f"/api/orders/reservations/{booking['id']}/").data['data']
            linked_order = client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertEqual((reservation['status'], reservation['order']), ('CONVERTED', order.pk))
            self.assertEqual((linked_order['id'], linked_order['status'], linked_order['amount']), (order.pk, 'GRABBED', 10000))