from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from orders.models import Order
from orders.tests import test_release_flow as release_flow
from orders.tests import test_reservation_api as reservation_api
from support.models import SupportContactCard


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class CardAssignmentTest(APITestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def test_selected_card_binds_order_owner_and_remains_stable(self):
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
        self.ok(self.recharge())
        self.login(self.boss)
        response = self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'support_contact_id': self.card.pk,
            'client_request_id': 'staff-card-order'}, format='json')
        result = self.ok(response)
        order = Order.objects.get(pk=result['id'])
        self.assertEqual(order.responsible_staff_id, self.accounts[self.admin.pk].pk)
        self.card.staff_account = None
        self.card.save(update_fields=['staff_account'])
        order.refresh_from_db()
        self.assertEqual(order.responsible_staff_id, self.accounts[self.admin.pk].pk)

    def test_unbound_card_cannot_be_selected_or_listed(self):
        self.ok(self.recharge())
        self.login(self.boss)
        response = self.client.get('/api/support/contact-cards/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data'], [])
        rejected = self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': self.accounts[self.provider.pk].pk, 'support_contact_id': self.card.pk,
            'client_request_id': 'unbound-card-order'}, format='json')
        self.assertEqual(rejected.data['code'], 400)
        self.assertFalse(Order.objects.filter(customer_account=self.accounts[self.boss.pk]).exists())

    @override_settings(GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                       GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_staff_dispatch_snapshots_selected_staff(self):
        from console.grab_config import save_grab_window
        save_grab_window(1, 0, lambda *_: None)
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
        self.ok(self.recharge())
        response = self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'staff-card-dispatch',
        }, format='json')
        result = self.ok(response)
        order = Order.objects.get(pk=result['id'])
        self.assertEqual((order.support_contact_id, order.responsible_staff_id),
                         (self.card.pk, self.accounts[self.admin.pk].pk))
        self.assertEqual((order.service_aftersale_hours, order.service_freeze_hours), (72, 72))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LICENSE_ACCOUNT_ADMISSION_ENABLED=False, ALLOWED_HOSTS=['*'])
class CardStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_card_binds_only_store_employee(self):
        from club_accounts.tokens import issue_account_tokens
        from stores.context import resolve_store, store_context
        from stores.tests_foundation import admin
        from stores.tests_hosts import xinan_store
        first, second = resolve_store(), xinan_store()
        with store_context(first):
            owner = admin('card-owner', super_admin=True)
            role = owner.admin_membership.roles.get(code=f'foundation_{owner.pk}')
            role.permissions = ['support:view', 'support:edit']
            role.save(update_fields=['permissions'])
            token, _ = issue_account_tokens(owner)
        with store_context(second):
            admin('second-owner')
            foreign = admin('second-only-employee')
        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        path = '/api/admin/support-cards/'
        rejected = client.post(path, {'name': '跨店名片', 'staff_account': foreign.pk}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        created = client.post(path, {'name': '本店名片', 'staff_account': owner.pk}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['data']['staff_account'], owner.pk)
        with store_context(second):
            self.assertFalse(SupportContactCard.objects.filter(name='本店名片').exists())


class ReservationCardTest(reservation_api.ReservationApiTestBase):
    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_reservation_conversion_snapshots_selected_staff(self):
        from club_accounts.tokens import issue_account_tokens
        self.client.force_authenticate(user=None)
        def login(account):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        login(self.boss_account)
        from engagement.models import PointFundingLot
        PointFundingLot.objects.create(account=self.boss_account, source_key='reservation-card-funding',
                                       kind='PREPAID', remaining=100000)
        card = SupportContactCard.objects.create(name='预约负责人', staff_account=self.staff_account)
        reservation = self.create_reservation_via_api()
        login(self.provider_account)
        confirmed = self.client.post(f'{reservation_api.LIST_URL}{reservation.pk}/confirm/', {})
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        login(self.boss_account)
        response = self.client.post(f'{reservation_api.LIST_URL}{reservation.pk}/convert/',
            {'client_request_id': 'reservation-card', 'support_contact_id': card.pk}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order = Order.objects.get(pk=response.data['data']['order']['id'])
        self.assertEqual((order.support_contact_id, order.responsible_staff_id), (card.pk, self.staff_account.pk))
        self.assertEqual((order.service_aftersale_hours, order.service_freeze_hours), (72, 72))
