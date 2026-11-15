"""Real boss checkout connected to an isolated ASGI and temporary database."""
import json
import os
from pathlib import Path
import subprocess
from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import OrderFundingAllocation, PointFundingLot
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_SERVICE_FUNDING_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/service-funding-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'provider': recipient.pk, 'service': service.pk,
                                  'token': issue_account_tokens(account)[0], 'account': account.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/reservation-funding-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'reservation': reservation_id,
                                  'token': issue_account_tokens(account)[0], 'account': account.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss.post('/api/orders/reservations/', {'service_id': service.pk,
            'provider_account_id': recipient.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        reservation_id = created.data['data']['id']
        self.assertEqual(player.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-reservation-funding-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'reservation_no': created.data['data']['reservation_no'],
                    'staff': {'id': staff_account.pk, 'token': issue_account_tokens(staff_account)[0],
                              'permissions': permissions}}), text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
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
        make_service(name='隔离代单余额服务', price=1000)
        permissions = ['order:view', 'order:dispatch']
        staff = make_console_user(permissions); staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/admin-dispatch-funding-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'staff': {'id': staff_account.pk,
                    'token': issue_account_tokens(staff_account)[0], 'permissions': permissions}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=account)
        self.assertEqual(order.funding_confirmation['actor_account_id'], staff_account.pk)
        self.assertIn('老板确认', order.funding_confirmation['confirmation'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 700, 'GIFT': 200, 'COMPENSATION': 100})
