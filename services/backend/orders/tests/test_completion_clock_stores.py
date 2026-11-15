from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import ProviderReport, ProviderReportImage, Transaction, Wallet
from wallet.services import get_wallet
from stores.db import transaction


@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_RECHARGE_PROOF=False,
                   REQUIRE_ORDER_EVIDENCE_IMAGES=False, LEGACY_FORCE_AUTH_COMPAT=False,
                   BOSS_FUNDING_SELECTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ALLOWED_HOSTS=['*'])
class CompletionClockStoreTests(TestCase):
    databases = {'default', 'store_b'}

    def test_completion_waits_for_report_and_both_clocks_in_each_store(self):
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss_user = make_user()
                boss = get_or_create_account_for_legacy_user(boss_user)
                player_user = make_provider(deposit_mode='CASH', deposit_paid=40000)
                player = get_or_create_account_for_legacy_user(player_user)
                player_user.escort_profile.account = player
                player_user.escort_profile.save(update_fields=['account'])
                staff_user = make_console_user(['wallet:recharge', 'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff
                staff_user.admin_membership.save(update_fields=['account'])
                def client(account):
                    return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                owner, provider, admin = client(boss), client(player), client(staff)
                recharge = admin.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 2000,
                    'client_request_id': 'completion-clock-fund', 'trade_no': 'completion-clock-fund'}, format='json')
                self.assertEqual(recharge.data['code'], 0, recharge.data)
                service = make_service(price=1000, commission_rate=20)
                result = owner.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': player.pk,
                    'client_request_id': 'completion-clock-order'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                order = Order.objects.get(pk=result.data['data']['id'])
                self.assertEqual((order.settlement_mode, order.service_aftersale_hours, order.service_freeze_hours),
                                 ('COMPLETION', 72, 72))
                for action in ('start', 'complete'):
                    response = provider.post(f'/api/orders/orders/{order.pk}/{action}/', {}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db()
                self.assertIsNone(order.settled_at)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                report = ProviderReport.objects.get(order=order, provider_account=player)
                for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                    response = provider.post(f'/api/wallet/reports/{report.pk}/images/',
                        {'kind': kind, 'image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                self.assertEqual(provider.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                order.refresh_from_db()
                self.assertIsNotNone(order.settled_at)
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
                self.assertEqual(Wallet.objects.get(account=player).balance, 800)

    def test_double_shares_wait_for_whole_order_and_each_report(self):
        from orders.services import settle_order
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss_user, first_user, second_user = make_user(), make_provider(), make_provider()
                boss, first, second = [get_or_create_account_for_legacy_user(user)
                                       for user in (boss_user, first_user, second_user)]
                for user, account in ((first_user, first), (second_user, second)):
                    user.escort_profile.account = account
                    user.escort_profile.save(update_fields=['account'])
                wallet = get_wallet(user=boss_user, account=boss)
                order = make_order(boss_user, service=make_service(price=500, billing_provider_count=2),
                    provider=first_user, customer_account=boss, provider_account=first,
                    status='IN_SERVICE', payment_status='PAID', amount=1000,
                    escort_mode='DOUBLE', provider_income=800, shop_income=200)
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-1000,
                                           balance_before=1000, balance_after=0)
                now = timezone.now()
                for user, account, offset in ((first_user, first, 0), (second_user, second, 3)):
                    OrderProvider.objects.create(order=order, provider=user, provider_account=account,
                                                 settlement_base=500, provider_income=400, commission_rate=20)
                    report = ProviderReport.objects.create(order=order, provider=user, provider_account=account,
                        game_name='双陪隔离测试', amount=1000, payout_amount=400, status='APPROVED',
                        submitted_at=now + timedelta(hours=offset), entry_image='test/entry.png',
                        completion_image='test/completion.png')
                    ProviderReportImage.objects.create(report=report, image='test/result.png')
                with patch('django.utils.timezone.now', return_value=now + timedelta(hours=80)), transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    self.assertEqual(settle_order(locked), [])
                order.status, order.completed_at = 'COMPLETED', now
                order.save(update_fields=['status', 'completed_at'])
                clock_version = order.service_clock_version
                Order.objects.filter(pk=order.pk).update(service_clock_version=None)
                with patch('django.utils.timezone.now', return_value=now + timedelta(hours=80)), transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    self.assertEqual(settle_order(locked), [])
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                Order.objects.filter(pk=order.pk).update(service_clock_version=clock_version)
                with patch('django.utils.timezone.now', return_value=now + timedelta(hours=74)), transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    self.assertEqual(settle_order(locked), [first_user.pk])
                self.assertEqual((get_wallet(user=first_user, account=first).balance,
                                  get_wallet(user=second_user, account=second).balance), (400, 0))
                with patch('django.utils.timezone.now', return_value=now + timedelta(hours=76)), transaction.atomic():
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    self.assertEqual(settle_order(locked), [second_user.pk])
                self.assertEqual(get_wallet(user=second_user, account=second).balance, 400)
