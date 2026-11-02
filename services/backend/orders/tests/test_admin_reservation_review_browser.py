"""Render the persisted reservation review against actual isolated HTTP data."""
import json
import os
import shutil
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
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_RESERVATION_BROWSER') == '1', 'Requires isolated browser build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminReservationReviewBrowserTest(LiveServerTestCase):
    def test_conversion_confirmation_at_375px(self):
        from orders.models import Order
        from wallet.models import Wallet
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
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
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = boss_client.post('/api/orders/reservations/', {'provider_account_id': accounts[1].pk,
            'service_id': service.pk, 'start_time': start.isoformat(),
            'end_time': (start + timedelta(hours=1)).isoformat()}, format='json').data
        self.assertEqual(created['code'], 0, created)
        booking = created['data']
        self.assertEqual(provider_client.post(f"/api/orders/reservations/{booking['id']}/confirm/", {}, format='json').data['code'], 0)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-reservation-review-375.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(accounts[2])[0],
                              'convert': True, 'reservationNo': booking['reservation_no'],
                              'serviceName': service.name, 'bossName': booking['customer']['name'],
                              'providerName': booking['provider']['name']}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order = Order.objects.get(customer_account=accounts[0])
        self.assertEqual((order.amount, order.provider_account_id, order.status), (10000, accounts[1].pk, 'GRABBED'))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 40000)
        for client in (boss_client, provider_client):
            reservation = client.get(f"/api/orders/reservations/{booking['id']}/").data['data']
            linked_order = client.get(f'/api/orders/orders/{order.pk}/').data['data']
            self.assertEqual((reservation['status'], reservation['order']), ('CONVERTED', order.pk))
            self.assertEqual((linked_order['id'], linked_order['status'], linked_order['amount']), (order.pk, 'GRABBED', 10000))

    def test_workbench_queue_orders_soonest_first_at_375px(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['dashboard:view', 'order:view', 'order:dispatch'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in ((boss, boss_account), (provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000)
        boss_client = APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        provider_client = APIClient()
        provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = {}
        for days in (2, 0, 1, 3):
            slot = start + timedelta(days=days)
            result = boss_client.post('/api/orders/reservations/', {'provider_account_id': provider_account.pk,
                'service_id': service.pk, 'start_time': slot.isoformat(),
                'end_time': (slot + timedelta(hours=1)).isoformat()}, format='json').data
            self.assertEqual(result['code'], 0, result)
            created[days] = result['data']
        self.assertEqual(provider_client.post(f"/api/orders/reservations/{created[3]['id']}/confirm/", {}, format='json').data['code'], 0)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-reservation-review-375.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': issue_account_tokens(staff_account)[0],
                'queue': True, 'pendingNos': [created[i]['reservation_no'] for i in (0, 1, 2)],
                'confirmedNo': created[3]['reservation_no']}), text=True, encoding='utf-8',
            capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reload_review_at_375px(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        boss_account, provider_account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        for user, account in ((boss, boss_account), (provider, provider_account), (staff, staff_account)):
            link_legacy_relations(user, account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000)
        token = issue_account_tokens(staff_account)[0]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        created = client.post('/api/orders/reservations/', {'customer_account_id': boss_account.pk,
            'provider_account_id': provider_account.pk, 'service_id': service.pk,
            'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        summary = f'老板 #{boss_account.pk} · 陪玩 #{provider_account.pk} · 服务 #{service.pk} · 测试时间（北京时间）'
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-reservation-review-375.cjs')],
            input=json.dumps({'api': self.live_server_url, 'token': token, 'staffId': staff_account.pk,
                'reservationNo': created.data['data']['reservation_no'], 'summary': summary}),
            text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
