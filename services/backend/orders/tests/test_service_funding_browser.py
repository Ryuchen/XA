"""Real boss checkout connected to the isolated HTTP API and temporary database."""
import os
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import OrderFundingAllocation, PointFundingLot
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_SERVICE_FUNDING_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   BOSS_FUNDING_SELECTION_ENABLED=True)
class ServiceFundingBrowserTest(LiveServerTestCase):
    def test_boss_selects_sources_and_real_order_preserves_them(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 1700; wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1000), ('GIFT', 400), ('COMPENSATION', 300)]:
            PointFundingLot.objects.create(account=account, source_key=f'browser-funding-{kind}',
                                           kind=kind, remaining=amount)
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离余额来源服务', price=1000)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        created = boss.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': recipient.pk,
            'gift_balance_amount': 200, 'compensation_balance_amount': 100,
            'game_region': '隔离大区', 'game_nickname': '隔离老板',
            'client_request_id': 'service-funding-browser'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(dict(OrderFundingAllocation.objects.filter(order=order).values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})

    def test_boss_reservation_selection_and_conversion(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 1700; wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1000), ('GIFT', 400), ('COMPENSATION', 300)]:
            PointFundingLot.objects.create(account=account, source_key=f'browser-reservation-{kind}',
                                           kind=kind, remaining=amount)
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离预约余额来源', price=1000)
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(recipient)[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss.post('/api/orders/reservations/', {'service_id': service.pk,
            'provider_account_id': recipient.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        reservation_id = created.data['data']['id']
        confirmed = player.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        quote = boss.get(f'/api/orders/reservations/{reservation_id}/quote/',
                         {'gift_balance_amount': 200, 'compensation_balance_amount': 100})
        self.assertEqual(quote.data['code'], 0, quote.data)
        converted = boss.post(f'/api/orders/reservations/{reservation_id}/convert/',
            {'gift_balance_amount': 200, 'compensation_balance_amount': 100,
             'expected_amount': quote.data['data']['amount'], 'expected_service': quote.data['data']['service'],
             'client_request_id': 'reservation-funding-browser'}, format='json')
        self.assertEqual(converted.data['code'], 0, converted.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(dict(OrderFundingAllocation.objects.filter(order=order).values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})

    def test_staff_reservation_records_the_boss_confirmation(self):
        user = make_user(); account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 1700; wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1000), ('GIFT', 400), ('COMPENSATION', 300)]:
            PointFundingLot.objects.create(account=account, source_key=f'admin-reservation-{kind}',
                                           kind=kind, remaining=amount)
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='客服预约余额来源', price=1000)
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        boss = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        player = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(recipient)[0])
        admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss.post('/api/orders/reservations/', {'service_id': service.pk,
            'provider_account_id': recipient.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        reservation_id = created.data['data']['id']
        self.assertEqual(player.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        quote = admin.get(f'/api/orders/reservations/{reservation_id}/quote/',
                          {'gift_balance_amount': 200, 'compensation_balance_amount': 100})
        self.assertEqual(quote.data['code'], 0, quote.data)
        converted = admin.post(f'/api/orders/reservations/{reservation_id}/convert/',
            {'gift_balance_amount': 200, 'compensation_balance_amount': 100,
             'expected_amount': quote.data['data']['amount'], 'expected_service': quote.data['data']['service'],
             'boss_confirmation': '老板确认赠送20、赔付10，其余本金',
             'client_request_id': 'admin-reservation-funding-browser'}, format='json')
        self.assertEqual(converted.data['code'], 0, converted.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(order.funding_confirmation['actor_account_id'], staff_account.pk)
        self.assertIn('老板确认', order.funding_confirmation['confirmation'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})

    def test_staff_dispatch_page_records_boss_confirmation(self):
        user = make_user(nickname='隔离代单老板'); account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 1700; wallet.save(update_fields=['balance'])
        for kind, amount in [('PREPAID', 1000), ('GIFT', 400), ('COMPENSATION', 300)]:
            PointFundingLot.objects.create(account=account, source_key=f'admin-dispatch-{kind}',
                                           kind=kind, remaining=amount)
        service = make_service(name='隔离代单余额服务', price=1000)
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        params = {'customer_id': account.pk, 'service_id': service.pk, 'game_rounds': 1,
                  'gift_balance_amount': 200, 'compensation_balance_amount': 100}
        quote = admin.get('/api/admin/orders/dispatch-quote/', params)
        self.assertEqual(quote.data['code'], 0, quote.data)
        dispatched = admin.post('/api/admin/orders/dispatch/', {**params,
            'expected_amount': quote.data['data']['amount'],
            'boss_confirmation_method': '隔离代单确认', 'boss_confirmation': '老板确认赠送20、赔付10，其余本金',
            'client_request_id': 'admin-dispatch-funding-browser'}, format='json')
        self.assertEqual(dispatched.data['code'], 0, dispatched.data)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(order.funding_confirmation['actor_account_id'], staff_account.pk)
        self.assertIn('老板确认', order.funding_confirmation['confirmation'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})