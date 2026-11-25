from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, OrderRoom
from orders.state_machine import IllegalTransitionError, transition
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_service_category, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


def client_for(user):
    account = get_or_create_account_for_legacy_user(user)
    return account, APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class OrderRoomTests(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.boss_account, self.boss_client = client_for(self.boss)
        self.provider = make_provider(deposit_mode='EARNINGS')
        self.provider_account, self.provider_client = client_for(self.provider)
        self.other = make_provider(deposit_mode='EARNINGS')
        self.other_account, self.other_client = client_for(self.other)
        self.service = make_service(room_enabled=True)

    def order(self, **kw):
        return make_order(self.boss, service=self.service, customer_account=self.boss_account,
                          payment_status=Order.PaymentStatus.PAID, **kw)

    def assign(self, order, accounts):
        def assign_locked(row):
            row.provider, row.provider_account = accounts[0][0], accounts[0][1]
            for user, account in accounts:
                OrderProvider.objects.create(order=row, provider=user, provider_account=account,
                                             provider_business_cycle=account.business_cycle)
        return transition(order.pk, Order.Status.GRABBED, side_effect=assign_locked,
                          update_fields=['provider', 'provider_account'])

    def test_assigned_single_room_is_private_and_precedes_service(self):
        order = self.order()
        self.assertFalse(OrderRoom.objects.exists())
        self.assign(order, [(self.provider, self.provider_account)])
        path = f'/api/orders/orders/{order.pk}/room/'
        boss = self.boss_client.get(path)
        player = self.provider_client.get(path)
        self.assertEqual((boss.status_code, boss.data['data']['role']), (200, 'BOSS'))
        self.assertEqual((player.status_code, player.data['data']['role']), (200, 'PROVIDER'))
        self.assertEqual(boss.data['data']['order_status'], 'GRABBED')
        self.assertEqual(self.other_client.get(path).status_code, 404)
        self.assertEqual(OrderRoom.objects.filter(order=order).count(), 1)

    def test_two_providers_must_be_assigned_together(self):
        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        order = self.order(escort_mode='DOUBLE')
        with self.assertRaises(IllegalTransitionError):
            self.assign(order, [(self.provider, self.provider_account)])
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(order.providers.exists())
        self.assertFalse(OrderRoom.objects.exists())
        self.assign(order, [(self.provider, self.provider_account), (self.other, self.other_account)])
        path = f'/api/orders/orders/{order.pk}/room/'
        self.assertEqual(self.provider_client.get(path).status_code, 200)
        self.assertEqual(self.other_client.get(path).status_code, 200)
        self.assertEqual(OrderRoom.objects.count(), 1)

    def test_room_creation_failure_rolls_back_order_and_assignments(self):
        order = self.order()
        with patch('orders.rooms.OrderRoom.objects.get_or_create', side_effect=RuntimeError('storage failure')):
            with self.assertRaises(RuntimeError):
                self.assign(order, [(self.provider, self.provider_account)])
        order.refresh_from_db()
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(order.providers.exists())
        self.assertFalse(OrderRoom.objects.exists())

    def test_configuration_snapshot_and_reassigned_provider_access(self):
        order = self.order()
        self.service.room_enabled = False
        self.service.save(update_fields=['room_enabled'])
        self.assign(order, [(self.provider, self.provider_account)])
        room = OrderRoom.objects.get(order=order)
        row = order.providers.get(provider_account=self.provider_account)
        row.released_at = timezone.now(); row.save(update_fields=['released_at'])
        OrderProvider.objects.create(order=order, provider=self.other, provider_account=self.other_account)
        Order.objects.filter(pk=order.pk).update(provider=self.other, provider_account=self.other_account)
        path = f'/api/orders/orders/{order.pk}/room/'
        former = self.provider_client.get(path)
        self.assertEqual(former.data['data']['role'], 'FORMER_PROVIDER')
        self.assertEqual(former.data['data']['capabilities'], ['OWN_MESSAGE_HISTORY'])
        self.assertEqual(self.other_client.get(path).data['data']['id'], room.pk)
        new_order = self.order()
        self.assign(new_order, [(self.provider, self.provider_account)])
        self.assertFalse(OrderRoom.objects.filter(order=new_order).exists())

    def test_order_list_and_detail_preserve_room_requirement_snapshot(self):
        room_order = self.order()
        self.service.room_enabled = False
        self.service.save(update_fields=['room_enabled'])
        ordinary_order = self.order()
        legacy_order = self.order()
        Order.objects.filter(pk=legacy_order.pk).update(room_required_snapshot=None)
        snapshots = {room_order.pk: True, ordinary_order.pk: False, legacy_order.pk: None}
        for order in (room_order, ordinary_order, legacy_order):
            self.assign(order, [(self.provider, self.provider_account)])

        for role, client in (('BOSS', self.boss_client), ('PROVIDER', self.provider_client)):
            with self.subTest(role=role):
                paginated = role == 'PROVIDER'
                listing = client.get('/api/orders/orders/', {'page': 1} if paginated else {})
                self.assertEqual(listing.status_code, 200, listing.data)
                payload = listing.data['data']
                rows = {row['id']: row for row in (payload['list'] if paginated else payload)}
                for order_id, snapshot in snapshots.items():
                    self.assertIs(rows[order_id]['room_required_snapshot'], snapshot)
                    detail = client.get(f'/api/orders/orders/{order_id}/')
                    self.assertEqual(detail.status_code, 200, detail.data)
                    self.assertIs(detail.data['data']['room_required_snapshot'], snapshot)

    def test_room_configuration_is_not_available_for_gifts_or_addons(self):
        from console.serializers import AdminServiceItemSerializer
        for service in (make_service(is_addon=True), make_service(service_category=make_service_category(is_gift=True))):
            serializer = AdminServiceItemSerializer(service, data={'room_enabled': True}, partial=True)
            self.assertFalse(serializer.is_valid())
            self.assertIn('room_enabled', serializer.errors)

    def test_staff_needs_order_view_permission(self):
        order = self.order()
        self.assign(order, [(self.provider, self.provider_account)])
        path = f'/api/orders/orders/{order.pk}/room/'
        for permissions, expected in ((['order:view'], 200), (['dictionary:view'], 404)):
            staff = make_console_user(permissions)
            account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            self.assertEqual(client.get(path).status_code, expected)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class OrderRoomStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_ids_are_scoped_to_bound_domain_and_token(self):
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
                boss_account = get_or_create_account_for_legacy_user(boss)
                provider_account = get_or_create_account_for_legacy_user(provider)
                service = make_service(room_enabled=True)
                order = make_order(boss, customer_account=boss_account, service=service, payment_status='PAID')
                def assigned(row):
                    row.provider, row.provider_account = provider, provider_account
                    OrderProvider.objects.create(order=row, provider=provider, provider_account=provider_account)
                transition(order.pk, Order.Status.GRABBED, side_effect=assigned, update_fields=['provider', 'provider_account'])
                token = issue_account_tokens(boss_account)[0]
                rows.append((host, token, order.pk))
        self.assertEqual(rows[0][2], rows[1][2])
        path = f'/api/orders/orders/{rows[0][2]}/room/'
        for host, token, _ in rows:
            own = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + token).get(path)
            self.assertEqual(own.status_code, 200, own.data)
        cross = APIClient(HTTP_HOST=rows[1][0], HTTP_AUTHORIZATION='Bearer ' + rows[0][1]).get(path)
        self.assertNotEqual(cross.status_code, 200)
