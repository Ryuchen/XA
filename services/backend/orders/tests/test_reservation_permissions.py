"""Reservation access follows the existing console order permissions."""
from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Reservation
from orders.tests.factories import make_console_user, make_provider
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationPermissionTest(ReservationApiTestBase):
    def client_for(self, account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def staff_for(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return self.client_for(account)

    def test_workbench_pending_reservations_match_real_queue_and_permission(self):
        boss, provider = self.client_for(self.boss_account), self.client_for(self.provider_account)
        staff = self.staff_for(['dashboard:view', 'order:view'])
        limited = self.staff_for(['dashboard:view'])
        created = boss.post(LIST_URL, self.create_payload(), format='json').data
        self.assertEqual(created['code'], 0, created)
        reservation_id = created['data']['id']
        def snapshot():
            return staff.get('/api/admin/dashboard/stats').data['data']['pending_reservation_total']
        def confirmed():
            return staff.get('/api/admin/dashboard/stats').data['data']['confirmed_reservation_total']
        self.assertEqual(snapshot(), 1)
        self.assertEqual(confirmed(), 0)
        queue = staff.get(LIST_URL, {'status': 'pending'}).data['data']
        self.assertEqual((queue['total'], queue['items'][0]['id']), (1, reservation_id))
        self.assertIsNone(limited.get('/api/admin/dashboard/stats').data['data']['pending_reservation_total'])
        self.assertIsNone(limited.get('/api/admin/dashboard/stats').data['data']['confirmed_reservation_total'])
        self.assertEqual(provider.post(f'{LIST_URL}{reservation_id}/confirm/', {}, format='json').data['code'], 0)
        self.assertEqual(snapshot(), 0)
        self.assertEqual(staff.get(LIST_URL, {'status': 'pending'}).data['data']['total'], 0)
        self.assertEqual(confirmed(), 1)
        self.assertEqual(staff.get(LIST_URL, {'status': 'confirmed'}).data['data']['total'], 1)
        quote = boss.get(f'{LIST_URL}{reservation_id}/quote/').data
        self.assertEqual(quote['code'], 0, quote)
        converted = boss.post(f'{LIST_URL}{reservation_id}/convert/', {
            'expected_amount': quote['data']['amount'], 'expected_service': quote['data']['service'],
            'client_request_id': 'dashboard-confirmed-reservation',
        }, format='json').data
        self.assertEqual(converted['code'], 0, converted)
        self.assertEqual(confirmed(), 0)
        self.assertEqual(staff.get(LIST_URL, {'status': 'confirmed'}).data['data']['total'], 0)

    def test_read_only_and_cancel_roles_have_matching_actions(self):
        boss, provider = self.client_for(self.boss_account), self.client_for(self.provider_account)
        created = boss.post(LIST_URL, self.create_payload(), format='json').data
        self.assertEqual(created['code'], 0, created)
        reservation_id = created['data']['id']
        path = f'{LIST_URL}{reservation_id}/'
        viewer = self.staff_for(['order:view'])
        reader = viewer.get(path).data
        self.assertEqual(reader['code'], 0, reader)
        self.assertEqual(reader['data']['allowed_actions'], [])
        self.assertEqual(viewer.post(path + 'confirm/', {}, format='json').data['code'], 403)
        self.assertEqual(viewer.post(path + 'cancel/', {}, format='json').data['code'], 403)
        self.assertEqual(viewer.get(path + 'quote/').data['code'], 403)
        self.assertEqual(viewer.post(path + 'convert/', {}, format='json').data['code'], 403)
        self.assertEqual(viewer.post(LIST_URL, self.create_payload(customer_account_id=self.boss_account.pk), format='json').data['code'], 403)
        self.assertEqual(provider.post(path + 'confirm/', {}, format='json').data['code'], 0)
        self.assertEqual(viewer.get(path).data['data']['allowed_actions'], [])
        cancel = self.staff_for(['order:view', 'order:cancel'])
        self.assertEqual(cancel.get(path).data['data']['allowed_actions'], ['cancel'])
        self.assertEqual(cancel.post(path + 'cancel/', {'reason': '客服处理'}, format='json').data['code'], 0)
        self.assertEqual(boss.get(path).data['data']['status'], 'CANCELLED')

    def test_no_view_staff_cannot_read_but_dispatch_authority_can_convert(self):
        boss, provider = self.client_for(self.boss_account), self.client_for(self.provider_account)
        dispatcher = self.staff_for(['order:dispatch'])
        self.assertEqual(dispatcher.get(LIST_URL).data['code'], 403)
        created = dispatcher.post(LIST_URL, self.create_payload(customer_account_id=self.boss_account.pk), format='json').data
        self.assertEqual(created['code'], 0, created)
        path = f"{LIST_URL}{created['data']['id']}/"
        self.assertEqual(dispatcher.get(path).data['code'], 403)
        self.assertEqual(provider.post(path + 'confirm/', {}, format='json').data['code'], 0)
        quote = dispatcher.get(path + 'quote/').data
        self.assertEqual(quote['code'], 0, quote)
        converted = dispatcher.post(path + 'convert/', {'expected_amount': quote['data']['amount'],
            'expected_service': quote['data']['service'], 'client_request_id': 'permission-test-convert'}, format='json').data
        self.assertEqual(converted['code'], 0, converted)
        self.assertEqual(boss.get(path).data['data']['status'], 'CONVERTED')
        self.assertEqual(Reservation.objects.get(pk=created['data']['id']).order.customer_account_id, self.boss_account.pk)

    def test_staff_without_order_permissions_and_unrelated_provider_cannot_operate(self):
        boss = self.client_for(self.boss_account)
        created = boss.post(LIST_URL, self.create_payload(), format='json').data
        self.assertEqual(created['code'], 0, created)
        path = f"{LIST_URL}{created['data']['id']}/"
        unprivileged = self.staff_for([])
        for response in (unprivileged.get(LIST_URL), unprivileged.get(path),
                         unprivileged.post(path + 'confirm/', {}, format='json'),
                         unprivileged.post(path + 'cancel/', {}, format='json'),
                         unprivileged.get(path + 'quote/'), unprivileged.post(path + 'convert/', {}, format='json')):
            self.assertEqual(response.data['code'], 403, response.data)
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        unrelated = self.client_for(other_account)
        self.assertEqual(unrelated.get(path).data['code'], 404)
        self.assertEqual(unrelated.post(path + 'confirm/', {}, format='json').data['code'], 404)
