from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import DefaultPaymentSource, PointFundingLot
from engagement.services import wallet_funding_parts
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(BOSS_FUNDING_SELECTION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class DefaultPrincipalMultistoreTest(TestCase):
    databases = {'default', 'store_b'}

    @override_settings(DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
                       ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True)
    def test_staff_and_reservation_payments_use_one_principal_ledger(self):
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                user, player_user = make_user(), make_provider(deposit_mode='CASH', deposit_paid=40000)
                boss = get_or_create_account_for_legacy_user(user)
                player = get_or_create_account_for_legacy_user(player_user)
                player_user.escort_profile.account = player
                player_user.escort_profile.save(update_fields=['account'])
                wallet = get_wallet(account=boss, user=user)
                wallet.balance = 5000
                wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=boss, kind='PREPAID', remaining=5000,
                                               source_key='principal-formal')
                from console.grab_config import save_grab_window
                from console.reservation_deadlines import save_deadlines
                save_grab_window(1, 0, lambda *_: None)
                save_deadlines(30, 60, 0, lambda *_: None)
                staff_user = make_console_user(['order:view', 'order:dispatch'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
                worker = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(player)[0])
                service = make_service(price=800)
                dispatched = admin.post('/api/admin/orders/dispatch/', {'customer_id': boss.pk,
                    'service_id': service.pk, 'client_request_id': 'formal-principal-dispatch'}, format='json')
                self.assertEqual(dispatched.data['code'], 0, dispatched.data)
                start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                reserved = client.post('/api/orders/reservations/', {'service_id': service.pk,
                    'provider_account_id': player.pk, 'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
                self.assertEqual(reserved.data['code'], 0, reserved.data)
                reservation_id = reserved.data['data']['id']
                self.assertEqual(worker.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json').data['code'], 0)
                converted = client.post(f'/api/orders/reservations/{reservation_id}/convert/',
                    {'client_request_id': 'formal-principal-reservation'}, format='json')
                self.assertEqual(converted.data['code'], 0, converted.data)
                orders = list(Order.objects.filter(customer_account=boss).order_by('pk'))
                self.assertEqual(len(orders), 2)
                for order in orders:
                    self.assertIsNotNone(order.funding_terms)
                    self.assertEqual(list(order.funding_allocations.values_list('kind', 'amount')), [('PREPAID', 800)])
                    self.assertFalse(DefaultPaymentSource.objects.filter(snapshot__order=order).exists())
                wallet.refresh_from_db()
                self.assertEqual(wallet.balance, 3400)

    def test_unselected_gift_and_compensation_are_not_spent(self):
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                user, player_user = make_user(), make_provider(deposit_mode='CASH', deposit_paid=40000)
                boss = get_or_create_account_for_legacy_user(user)
                player = get_or_create_account_for_legacy_user(player_user)
                player_user.escort_profile.account = player
                player_user.escort_profile.save(update_fields=['account'])
                wallet = get_wallet(account=boss, user=user)
                wallet.balance = 2000
                wallet.save(update_fields=['balance'])
                for kind, amount in (('GIFT', 600), ('COMPENSATION', 400), ('PREPAID', 1000)):
                    PointFundingLot.objects.create(account=boss, kind=kind, remaining=amount,
                                                   source_key=f'default-principal-{kind}')
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
                service = make_service(price=1500)
                payload = {'service_id': service.pk, 'provider_id': player.pk, 'client_request_id': 'principal-only'}
                quote = client.post('/api/orders/quote/', payload, format='json')
                self.assertEqual(quote.data['code'], 0, quote.data)
                self.assertFalse(quote.data['data']['balance_sufficient'])
                rejected = client.post('/api/orders/create/', payload, format='json')
                self.assertNotEqual(rejected.data['code'], 0, rejected.data)
                from console.grab_config import save_grab_window
                from console.reservation_deadlines import save_deadlines
                save_grab_window(1, 0, lambda *_: None)
                save_deadlines(30, 60, 0, lambda *_: None)
                staff_user = make_console_user(['order:view', 'order:dispatch'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                dispatched = admin.post('/api/admin/orders/dispatch/', {'customer_id': boss.pk,
                    'service_id': service.pk, 'client_request_id': 'principal-only-dispatch'}, format='json')
                self.assertNotEqual(dispatched.data['code'], 0, dispatched.data)
                self.assertIn('余额不足', dispatched.data.get('msg', ''))
                start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
                reserved = client.post('/api/orders/reservations/', {'service_id': service.pk,
                    'provider_account_id': player.pk, 'start_time': start.isoformat(),
                    'end_time': (start + timedelta(hours=1)).isoformat(), 'game_rounds': 1}, format='json')
                self.assertEqual(reserved.data['code'], 0, reserved.data)
                reservation_id = reserved.data['data']['id']
                worker = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(player)[0])
                confirmed = worker.post(f'/api/orders/reservations/{reservation_id}/confirm/', {}, format='json')
                self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                converted = client.post(f'/api/orders/reservations/{reservation_id}/convert/',
                    {'client_request_id': 'principal-only-reservation'}, format='json')
                self.assertNotEqual(converted.data['code'], 0, converted.data)
                self.assertIn('余额不足', converted.data.get('msg', ''))
                wallet.refresh_from_db()
                self.assertEqual(wallet.balance, 2000)
                self.assertFalse(Order.objects.filter(customer_account=boss).exists())
                self.assertFalse(Transaction.objects.filter(wallet=wallet, tx_type='PAY').exists())
                service.price = 800
                service.save(update_fields=['price'])
                paid = client.post('/api/orders/create/', payload, format='json')
                self.assertEqual(paid.data['code'], 0, paid.data)
                order = Order.objects.get(pk=paid.data['data']['id'])
                self.assertEqual(list(DefaultPaymentSource.objects.filter(snapshot__order=order)
                                      .values_list('kind', 'amount')), [('PREPAID', 800)])
                from engagement.default_principal_evidence import verify_locked
                from stores.db import transaction
                with transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    evidence = verify_locked(locked)
                    self.assertEqual(evidence.principal, 800)
                    self.assertEqual(evidence.order_id, order.pk)
                from rest_framework.exceptions import ValidationError
                source = DefaultPaymentSource.objects.get(snapshot__order=order)
                source.amount = 799
                source.save(update_fields=['amount'])
                with transaction.atomic(), self.assertRaises(ValidationError):
                    verify_locked(Order.objects.select_for_update().get(pk=order.pk))
                source.amount = 800
                source.save(update_fields=['amount'])
                wallet.refresh_from_db()
                self.assertEqual(wallet_funding_parts(wallet), {'prepaid_balance': 200, 'gift_balance': 600,
                                                                'compensation_balance': 400})
