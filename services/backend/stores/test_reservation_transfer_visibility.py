import os
from unittest import skipUnless
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, Reservation
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
class ReservationTransferVisibilityIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_replacement_can_only_open_own_store_booking(self):
        stores = (resolve_store(), xinan_store())
        identities = []
        for index, store in enumerate(stores):
            with store_context(store):
                customer = make_user(); boss = get_or_create_account_for_legacy_user(customer)
                original = make_provider(deposit_mode='EARNINGS'); old = get_or_create_account_for_legacy_user(original)
                replacement = make_provider(deposit_mode='EARNINGS'); new = get_or_create_account_for_legacy_user(replacement)
                service = make_service()
                order = make_order(customer, customer_account=boss, service=service,
                    provider=original if index else replacement, provider_account=old if index else new,
                    status='IN_SERVICE', payment_status='PAID')
                OrderProvider.objects.create(order=order, provider=original if index else replacement,
                    provider_account=old if index else new, provider_name_snapshot='本店现履约陪玩')
                booking = Reservation.objects.create(customer=customer, customer_account=boss, provider=original,
                    provider_account=old, service=service, status='CONVERTED', order=order,
                    start_time=timezone.now()+timedelta(days=1), end_time=timezone.now()+timedelta(days=1, hours=1))
                identities.append((new.pk, booking.pk, issue_account_tokens(new)[0]))
        self.assertEqual(identities[0][:2], identities[1][:2])
        first = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][2])
        second = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[1][2])
        path = f'/api/orders/reservations/{identities[0][1]}/'
        visible = first.get(path)
        self.assertEqual(visible.data['code'], 0, visible.data)
        self.assertEqual(visible.data['data']['current_providers'][0]['account_id'], identities[0][0])
        denied = second.get(path)
        self.assertEqual(denied.data['code'], 404, denied.data)
        crossed = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][2]).get(path)
        self.assertNotEqual(crossed.data.get('code'), 0, crossed.data)

    @override_settings(PRESTART_HANDOFF_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
    def test_handoff_changes_only_authenticated_store_even_with_same_ids(self):
        from orders.models import OrderPrestartHandoff
        from orders.tests.factories import make_console_user
        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                buyer = make_user(); boss = get_or_create_account_for_legacy_user(buyer)
                providers = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
                accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
                for user, account in zip(providers, accounts):
                    user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
                order = make_order(buyer, customer_account=boss, provider=providers[0], provider_account=accounts[0],
                    status='GRABBED', payment_status='PAID', amount=10000, provider_income=8000,
                    commission_rate=20, shop_income=2000)
                OrderProvider.objects.create(order=order, provider=providers[0], provider_account=accounts[0],
                    settlement_base=10000, provider_income=8000, commission_rate=20)
                user = make_console_user(['order:view','order:dispatch']); admin = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = admin; user.admin_membership.save(update_fields=['account'])
                identities.append((order.pk, accounts[0].pk, accounts[1].pk, issue_account_tokens(admin)[0]))
        self.assertEqual(identities[0][:3], identities[1][:3])
        first = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+identities[0][3])
        response = first.post(f'/api/admin/orders/{identities[0][0]}/transfer/', {'reason':'隔离改派', 'transfers':[{
            'old_provider_id':identities[0][1], 'new_provider_id':identities[0][2]}]}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        for index, store in enumerate(stores):
            with store_context(store):
                order = Order.objects.get(pk=identities[index][0])
                self.assertEqual(order.provider_account_id, identities[index][2] if index == 0 else identities[index][1])
                self.assertEqual(OrderPrestartHandoff.objects.filter(order=order).count(), 1 if index == 0 else 0)
