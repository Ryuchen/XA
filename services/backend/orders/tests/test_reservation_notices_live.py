"""Reservation notices use committed state and real account-scoped HTTP APIs."""
import asyncio
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationNoticesLiveTest(LiveServerTestCase):
    @override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
    def test_staff_actions_notify_both_affected_parties(self):
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        make_wallet(boss, balance=10000)
        staff = make_console_user(['order:view', 'order:dispatch', 'order:cancel'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        service = make_service(price=1000)
        clients = {}
        for name, account in (('boss', boss_account), ('provider', provider_account), ('staff', staff_account)):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients[name] = client

        def notices(role, title, route):
            result = clients[role].get('/api/messages/').data
            self.assertEqual(result['code'], 0, result)
            return [message for message in result['data']['messages']
                    if message['title'] == title and message['action_url'] == route]

        def create(day):
            start = (timezone.now() + timedelta(days=day)).replace(hour=10, minute=0, second=0, microsecond=0)
            result = clients['staff'].post('/api/orders/reservations/', {
                'customer_account_id': boss_account.pk, 'provider_account_id': provider_account.pk,
                'service_id': service.pk, 'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=1)).isoformat(),
            }, format='json').data
            self.assertEqual(result['code'], 0, result)
            return result['data']['id']

        rid = create(1)
        boss_route = f'/pages/reservation-detail/index?id={rid}'
        provider_route = f'/pages/reservations/index?id={rid}'
        self.assertEqual(len(notices('boss', '客服已创建预约', boss_route)), 1)
        self.assertEqual(len(notices('provider', '有新的预约待确认', provider_route)), 1)
        result = clients['staff'].post(f'/api/orders/reservations/{rid}/confirm/', {}, format='json').data
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(len(notices('boss', '预约已确认', boss_route)), 1)
        self.assertEqual(len(notices('provider', '预约已确认', provider_route)), 1)
        quote = clients['staff'].get(f'/api/orders/reservations/{rid}/quote/').data
        self.assertEqual(quote['code'], 0, quote)
        layer = get_channel_layer()
        channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)(f'account_{boss_account.pk}', channel)
        converted = clients['staff'].post(f'/api/orders/reservations/{rid}/convert/', {
            'client_request_id': 'staff-booking-notice', 'expected_amount': quote['data']['amount'],
            'expected_service': quote['data']['service'],
        }, format='json').data
        self.assertEqual(converted['code'], 0, converted)
        self.assertEqual(len(notices('boss', '预约已转为订单', boss_route)), 1)
        async def message_invalidation():
            while True:
                try:
                    event = await asyncio.wait_for(layer.receive(channel), timeout=0.5)
                except asyncio.TimeoutError:
                    return False
                if event['type'] == 'site_message_update':
                    return event['data']['account_id'] == boss_account.pk
        self.assertTrue(async_to_sync(message_invalidation)(), '客服转单后老板预约页面缺少消息刷新信号')
        self.assertEqual(clients['boss'].get(f'/api/orders/reservations/{rid}/').data['data']['status'], 'CONVERTED')
        again = clients['staff'].post(f'/api/orders/reservations/{rid}/convert/', {
            'client_request_id': 'staff-booking-notice', 'expected_amount': quote['data']['amount'],
            'expected_service': quote['data']['service'],
        }, format='json').data
        self.assertNotEqual(again['code'], 0)
        self.assertEqual(len(notices('boss', '预约已转为订单', boss_route)), 1)

        second = create(2)
        result = clients['staff'].post(f'/api/orders/reservations/{second}/reject/', {'reason': '档期调整'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(len(notices('boss', '预约已拒绝', f'/pages/reservation-detail/index?id={second}')), 1)
        self.assertEqual(len(notices('provider', '预约已拒绝', f'/pages/reservations/index?id={second}')), 1)
        # Either staff or the provider can reject. Feed the real account-scoped
        # API responses into the native presenter so it cannot attribute every
        # rejection to the provider, and its clock agrees with the other ends.
        third = create(3)
        result = clients['provider'].post(f'/api/orders/reservations/{third}/reject/', {'reason': '时间不合适'}, format='json').data
        self.assertEqual(result['code'], 0, result)
        rows = []
        from django.utils.dateparse import parse_datetime
        for reservation_id in (second, third):
            response = clients['boss'].get(f'/api/orders/reservations/{reservation_id}/').data
            self.assertEqual(response['code'], 0, response)
            row = response['data']
            rows.append({'reservation': row, 'startText': timezone.localtime(parse_datetime(row['start_time'])).strftime('%Y-%m-%d %H:%M')})
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native reservation display')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-reservation-display-live.cjs')],
            input=json.dumps(rows), text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_committed_notices_and_boss_message_navigation(self):
        boss, provider = make_user(), make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        staff = make_console_user(['order:view', 'order:dispatch', 'order:cancel'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, staff_account)
        AdminMembership.objects.filter(user=staff).update(account=staff_account)
        service = make_service(price=1000)
        clients = {}
        for name, account in (('boss', boss_account), ('provider', provider_account), ('staff', staff_account)):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients[name] = client

        def create(day):
            start = (timezone.now() + timedelta(days=day)).replace(hour=10, minute=0, second=0, microsecond=0)
            result = clients['boss'].post('/api/orders/reservations/', {
                'provider_account_id': provider_account.pk, 'service_id': service.pk,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
                'game_rounds': 1,
            }, format='json').data
            self.assertEqual(result['code'], 0, result)
            return result['data']['id']

        def notices(role, reservation_id, title):
            response = clients[role].get('/api/messages/').data
            self.assertEqual(response['code'], 0, response)
            route = 'reservations' if role == 'provider' else 'reservation-detail'
            expected = f'/pages/{route}/index?id={reservation_id}'
            return [item for item in response['data']['messages']
                    if item['title'] == title and item['action_url'] == expected]

        def transition(role, rid, action, reason=''):
            return clients[role].post(f'/api/orders/reservations/{rid}/{action}/', {'reason': reason}, format='json').data

        first = create(1)
        created_notice = notices('provider', first, '有新的预约待确认')
        self.assertEqual(len(created_notice), 1)
        self.assertEqual(transition('provider', first, 'confirm')['code'], 0)
        confirmed = notices('boss', first, '预约已确认')
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(transition('provider', first, 'confirm')['code'], 409)
        self.assertEqual(len(notices('boss', first, '预约已确认')), 1)
        self.assertEqual(transition('provider', first, 'cancel', '临时有事')['code'], 0)
        cancelled = notices('boss', first, '预约已取消')
        self.assertEqual(len(cancelled), 1)
        self.assertIn('临时有事', cancelled[0]['preview'])
        self.assertFalse(notices('provider', first, '预约已取消'))

        second = create(2)
        self.assertEqual(transition('provider', second, 'reject', '时间不合适')['code'], 0)
        rejected = notices('boss', second, '预约已拒绝')
        self.assertEqual(len(rejected), 1)
        self.assertIn('时间不合适', rejected[0]['preview'])

        third = create(3)
        self.assertEqual(transition('boss', third, 'cancel', '计划有变')['code'], 0)
        self.assertEqual(len(notices('provider', third, '预约已取消')), 1)
        self.assertFalse(notices('boss', third, '预约已取消'))

        fourth = create(4)
        self.assertEqual(transition('staff', fourth, 'cancel', '客服协调')['code'], 0)
        self.assertEqual(len(notices('provider', fourth, '预约已取消')), 1)
        self.assertEqual(len(notices('boss', fourth, '预约已取消')), 1)

        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required for native message navigation')
        repo = Path(settings.BASE_DIR).parent.parent
        token, refresh = issue_account_tokens(boss_account)
        result = subprocess.run([node, str(repo / 'scripts/native-reservation-notice-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'token': token, 'refresh': refresh, 'account': boss_account.pk,
                              'message': confirmed[0]['id'], 'reservation': first}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([node, str(repo / 'scripts/provider-reservation-notice-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(provider_account)[0],
                              'message': created_notice[0]['id'], 'reservation': first}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
