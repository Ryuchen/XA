"""Deposit refund clock after actual gift income in two isolated stores."""
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.income import release_due_income
from wallet.models import IncomeHold, Transaction


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False,
                   WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class GiftDepositClearanceStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_real_gift_and_refund_obey_each_store_snapshot_and_seven_days(self):
        for store, host, hours in ((resolve_store(), 'sl.esports.club', 0),
                                   (xinan_store(), 'xa.esports.club', 48)):
            with self.subTest(store=store.slug), store_context(store):
                staff = make_console_user(['escort:view', 'escort:deposit_topup', 'escort:deposit_refund',
                                           'wallet:recharge', 'deposit_rule:edit'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account
                staff.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
                clocks = admin.put('/api/admin/config/settlement-clocks', {
                    'aftersale_hours': hours, 'income_freeze_hours': hours, 'expected_version': 0}, format='json')
                self.assertEqual(clocks.data['code'], 0, clocks.data)
                provider = make_provider(deposit_mode='CASH', deposit_paid=0)
                recipient = get_or_create_account_for_legacy_user(provider)
                profile = provider.escort_profile
                profile.account = recipient
                profile.save(update_fields=['account'])
                topup = admin.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                    'amount': 40000, 'reason': '线下押金', 'reference': 'GIFT-CLEARANCE',
                    'client_request_id': 'gift-clearance-deposit'}, format='multipart')
                self.assertEqual(topup.data['code'], 0, topup.data)
                boss = make_user()
                buyer = get_or_create_account_for_legacy_user(boss)
                quote = admin.get('/api/admin/wallets/recharge-quote/', {'amount': 8000})
                self.assertEqual(quote.data['code'], 0, quote.data)
                recharge = admin.post('/api/admin/wallets/recharge/', {
                    'account_id': buyer.pk, 'amount': 8000, 'gift_amount': 0,
                    'trade_no': 'gift-clearance-recharge', 'client_request_id': 'gift-clearance-recharge',
                    'bonus_version': quote.data['data']['version']}, format='json')
                self.assertEqual(recharge.data['code'], 0, recharge.data)
                gift = make_service(price=2100, service_category=make_service_category(is_gift=True))
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(buyer)[0])
                sent = client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk,
                    'provider_id': recipient.pk, 'quantity': 1, 'expected_amount': 2100,
                    'client_request_id': 'gift-clearance-send'}, format='json')
                self.assertEqual(sent.data['code'], 0, sent.data)
                order = Order.objects.get(pk=sent.data['data']['id'])
                income = Transaction.objects.get(order=order, wallet__account=recipient, tx_type='GIFT')
                refund_url = f'/api/admin/escorts/{profile.pk}/deposit-refund/'
                if hours:
                    hold = IncomeHold.objects.get(income_transaction=income)
                    self.assertEqual(hold.release_at, order.completed_at + timedelta(hours=hours))
                    self.assertEqual(admin.get(refund_url).data['data']['wait_status'], 'BLOCKED')
                    # Later configuration changes do not shorten this gift's clocks.
                    changed = admin.put('/api/admin/config/settlement-clocks', {
                        'aftersale_hours': 0, 'income_freeze_hours': 0, 'expected_version': 1}, format='json')
                    self.assertEqual(changed.data['code'], 0, changed.data)
                    with patch('django.utils.timezone.now', return_value=hold.release_at):
                        self.assertEqual(release_due_income(now=hold.release_at), 1)
                        preview = admin.get(refund_url).data['data']
                    self.assertEqual((preview['wait_status'], preview['cleared_at']), ('WAITING', hold.release_at))
                    cleared = hold.release_at
                else:
                    self.assertFalse(IncomeHold.objects.filter(income_transaction=income).exists())
                    preview = admin.get(refund_url).data['data']
                    self.assertEqual((preview['wait_status'], preview['cleared_at']), ('WAITING', income.created_at))
                    cleared = income.created_at
                payment = Transaction.objects.get(order=order, wallet__account=buyer, tx_type='GIFT')
                with patch('django.utils.timezone.now', return_value=cleared), transaction.atomic():
                    Transaction.objects.filter(pk=payment.pk).update(amount=payment.amount + 1)
                    self.assertEqual(admin.get(refund_url).data['data']['wait_status'], 'REVIEW')
                    transaction.set_rollback(True)
                body = {'amount': 10000, 'reason': '押金退还', 'client_request_id': 'gift-clearance-refund'}
                with patch('django.utils.timezone.now', return_value=cleared + timedelta(days=7) - timedelta(microseconds=1)):
                    self.assertEqual(admin.post(refund_url, body, format='json').data['code'], 409)
                with patch('django.utils.timezone.now', return_value=cleared + timedelta(days=7)):
                    result = admin.post(refund_url, body, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    self.assertEqual(admin.post(refund_url, body, format='json').data['code'], 409)
                profile.refresh_from_db()
                self.assertEqual(profile.deposit_paid, 30000)
                self.assertEqual(Transaction.objects.filter(wallet__account=recipient,
                    tx_type='DEPOSIT', amount=10000).count(), 1)
