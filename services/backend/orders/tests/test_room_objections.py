from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import Order, OrderProvider, OrderRoomObjection
from orders.state_machine import IllegalTransitionError, transition
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from orders.tests.test_order_rooms import OrderRoomTests
from site_messages.models import Message
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomObjectionTests(APITestCase):
    def setUp(self):
        OrderRoomTests.setUp(self)
        self.order = OrderRoomTests.order(self)
        OrderRoomTests.assign(self, self.order, [(self.provider, self.provider_account)])
        self.staff = self.staff_client(['order:view', 'support:edit'])
        self.limited = self.staff_client(['order:view'])
        self.url = f'/api/orders/orders/{self.order.pk}/room/objections/'
        self.payload = {'reason': '战果有异议', 'client_request_id': 'case-one'}

    def staff_client(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])

    def test_owner_submits_idempotently_and_staff_resolves_with_audit(self):
        first = self.boss_client.post(self.url, self.payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        objection_id = first.data['data']['id']
        self.assertEqual(self.boss_client.post(self.url, self.payload, format='json').data['data']['id'], objection_id)
        self.assertEqual(self.boss_client.post(self.url, {**self.payload, 'reason': '其他'}, format='json').status_code, 409)
        self.assertEqual(self.boss_client.post(self.url, {**self.payload, 'client_request_id': 'case-two'}, format='json').status_code, 409)
        self.assertEqual(OrderRoomObjection.objects.count(), 1)
        self.assertEqual(Message.objects.filter(title='房间异议待处理').count(), 1)
        self.assertEqual(self.limited.get('/api/admin/room-objections/').status_code, 403)
        queue = self.staff.get('/api/admin/room-objections/').data['data']
        self.assertEqual((queue['total'], queue['list'][0]['id']), (1, objection_id))
        self.assertEqual(self.provider_client.get(self.url).status_code, 404)
        self.assertEqual(self.other_client.post(self.url, self.payload, format='json').status_code, 404)
        self.assertTrue(self.boss_client.get(f'/api/orders/orders/{self.order.pk}/room/').data['data']['has_open_objection'])
        resolve_url = f'/api/admin/room-objections/{objection_id}/resolve/'
        action = {'resolution': '核实后继续履约', 'client_request_id': 'resolve-one'}
        self.assertEqual(self.limited.post(resolve_url, action, format='json').status_code, 403)
        result = self.staff.post(resolve_url, action, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.staff.post(resolve_url, action, format='json').status_code, 200)
        self.assertEqual(self.staff.post(resolve_url, {**action, 'resolution': '其他'}, format='json').status_code, 409)
        self.assertEqual(AdminAuditLog.objects.filter(resource='room-objection').count(), 1)
        self.assertEqual(Message.objects.filter(recipient_account=self.boss_account, title='房间异议已处理').count(), 1)
        self.assertEqual(self.staff.get('/api/admin/room-objections/').data['data']['total'], 0)
        self.assertFalse(self.boss_client.get(f'/api/orders/orders/{self.order.pk}/room/').data['data']['has_open_objection'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.GRABBED)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        self.assertFalse(self.order.aftersales.exists())

    def test_failures_roll_back_objection_notification_or_resolution(self):
        with patch('site_messages.models.Message.objects.bulk_create', side_effect=RuntimeError('message unavailable')):
            with self.assertRaises(RuntimeError):
                self.boss_client.post(self.url, self.payload, format='json')
        self.assertFalse(OrderRoomObjection.objects.exists())
        self.assertFalse(Message.objects.filter(title='房间异议待处理').exists())
        row = self.boss_client.post(self.url, self.payload, format='json').data['data']
        path = f"/api/admin/room-objections/{row['id']}/resolve/"
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.staff.post(path, {'resolution': '核实', 'client_request_id': 'resolve-fail'}, format='json')
        self.assertEqual(OrderRoomObjection.objects.get(pk=row['id']).status, 'OPEN')
        with patch('site_messages.models.Message.objects.create', side_effect=RuntimeError('message unavailable')):
            with self.assertRaises(RuntimeError):
                self.staff.post(path, {'resolution': '核实', 'client_request_id': 'resolve-fail'}, format='json')
        self.assertEqual(OrderRoomObjection.objects.get(pk=row['id']).status, 'OPEN')
        self.assertFalse(AdminAuditLog.objects.filter(resource='room-objection').exists())

    def test_open_objection_prevents_order_completion(self):
        transition(self.order.pk, Order.Status.IN_SERVICE)
        result = self.boss_client.post(self.url, self.payload, format='json')
        self.assertEqual(result.status_code, 200)
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_SERVICE)
        resolve = self.staff.post(f"/api/admin/room-objections/{result.data['data']['id']}/resolve/",
                                  {'resolution': '已核实', 'client_request_id': 'finish-review'}, format='json')
        self.assertEqual(resolve.status_code, 200, resolve.data)
        # Resolving the objection alone must not bypass the new room review and
        # boss confirmation flow, which is still being connected.
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomObjectionStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_objections_and_alerts_stay_in_the_owner_store(self):
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
                boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
                service = make_service(room_enabled=True)
                order = make_order(boss, service=service, customer_account=boss_account, payment_status='PAID')
                def assign(row):
                    row.provider, row.provider_account = provider, player_account
                    OrderProvider.objects.create(order=row, provider=provider, provider_account=player_account)
                transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
                staff = make_console_user(['order:view', 'support:edit'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss_account)[0])
                staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
                rows.append((store, order.pk, boss_client, staff_client))
        self.assertEqual(rows[0][1], rows[1][1])
        response = rows[1][2].post(f'/api/orders/orders/{rows[1][1]}/room/objections/',
            {'reason': '仅兴安异议', 'client_request_id': 'store-case'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(rows[0][3].get('/api/admin/room-objections/').data['data']['total'], 0)
        self.assertEqual(rows[1][3].get('/api/admin/room-objections/').data['data']['total'], 1)
        for index, (store, _, _, _) in enumerate(rows):
            with store_context(store):
                self.assertEqual(OrderRoomObjection.objects.count(), index)
                self.assertEqual(Message.objects.filter(title='房间异议待处理').count(), index)
