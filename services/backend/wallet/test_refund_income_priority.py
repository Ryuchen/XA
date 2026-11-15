"""Real gift payments must not bypass an earlier eligible frozen income."""
from datetime import timedelta
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.deposit_clearance import clearance_evidence
from orders.models import Order, OrderRefund
from orders.tests.factories import make_completed_order, make_console_user
from wallet.income import PRIORITY_GIFT_FREEZE_REMARK, release_due_income
from wallet.models import IncomeHold, RefundDepositIncomeApplication, Transaction, get_platform_wallet
from wallet.test_gift_income_clocks import GiftIncomeClocksTest as GiftFixture
from stores.context import current_store, resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store


@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class RefundIncomePriorityTest(TestCase):
    send = GiftFixture.send
    configure = GiftFixture.configure

    def setUp(self):
        GiftFixture.setUp(self)
        store = current_store()
        host = 'xa.esports.club' if store and store.slug == 'xinan' else 'sl.esports.club'
        self.clients[0].defaults['HTTP_HOST'] = host
        self.clients[1].defaults['HTTP_HOST'] = host
        self.clients[2].defaults['HTTP_HOST'] = 'admin.esports.club'
        # Replace the shared fixture's direct balance with a real staff recharge.
        self.boss_wallet.balance = 0
        self.boss_wallet.save(update_fields=['balance'])
        staff = make_console_user(['wallet:recharge', 'escort:view', 'escort:deposit_topup', 'order:refund'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        quote = admin.get('/api/admin/wallets/recharge-quote/', {'amount': 8000})
        self.assertEqual(quote.data['code'], 0, quote.data)
        result = admin.post('/api/admin/wallets/recharge/', {'account_id': self.accounts[0].pk,
            'amount': 8000, 'gift_amount': 0, 'trade_no': 'income-priority-recharge',
            'client_request_id': 'income-priority-recharge', 'bonus_version': quote.data['data']['version']}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.account = self.accounts[1]
        self.profile = self.provider.escort_profile
        self.profile.deposit_mode = 'CASH'
        self.profile.deposit_paid = 0
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        topup = admin.post(f'/api/admin/escorts/{self.profile.pk}/deposit-topups/', {
            'amount': 10000, 'reason': '隔离线下押金', 'reference': 'PRIORITY-RECEIPT',
            'client_request_id': 'priority-cash-receipt'}, format='multipart')
        self.assertEqual(topup.data['code'], 0, topup.data)
        self.wallet = self.provider_wallet
        self.platform = get_platform_wallet()
        self.operator, self.operator_account = staff, account
        self.order = make_completed_order(self.boss, self.provider, amount=10000,
            provider_account=self.account, payment_status='PAID')
        Order.objects.filter(pk=self.order.pk).update(completed_at=self.order.created_at,
            settled_at=self.order.created_at, provider_income=0)

    def gap(self, amount):
        from wallet.refund_deposit import charge_refund_deposit
        refund = OrderRefund.objects.create(order=self.order, amount=amount, reason='隔离退款夹具',
            operator=self.operator, operator_account=self.operator_account)
        with transaction.atomic():
            return charge_refund_deposit(refund=refund, profile=self.profile, amount=amount,
                operator=self.operator, operator_account=self.operator_account)

    def test_due_gift_precedes_later_zero_clock_gift_and_clearance_accepts_queue(self):
        self.configure(1, 1)
        earlier = self.send('priority-earlier-gift')
        deficit = self.gap(2000)
        prior_hold = IncomeHold.objects.get(income_transaction__order=earlier)
        current = prior_hold.release_at + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=current):
            self.configure(0, 0, version=1)
            later = self.send('priority-later-gift')
            later_hold = IncomeHold.objects.get(income_transaction__order=later)
            freeze = Transaction.objects.get(order=later, wallet=self.wallet, tx_type='EARNINGS_FREEZE')
            self.assertEqual((later_hold.release_at, freeze.remark), (later.completed_at, PRIORITY_GIFT_FREEZE_REMARK))
            deficit.refresh_from_db()
            self.assertEqual(deficit.remaining, 2000)
            self.assertFalse(RefundDepositIncomeApplication.objects.filter(source_transaction__order=later).exists())
            self.assertEqual(release_due_income(now=current, limit=1), 1)
            deficit.refresh_from_db(); later_hold.refresh_from_db()
            self.assertEqual((deficit.remaining, later_hold.released_at), (400, None))
            self.assertEqual(release_due_income(now=current, limit=1), 1)
            deficit.refresh_from_db(); later_hold.refresh_from_db(); self.wallet.refresh_from_db()
            self.assertEqual((deficit.remaining, self.wallet.balance), (0, 1200))
            self.assertEqual(later_hold.released_at, current)
            evidence = clearance_evidence(self.profile, self.wallet, now=current)
            self.assertFalse(evidence.needs_review)
            self.assertEqual(evidence.last_closed_at, current)

    def test_mysql_isolation_cannot_be_assumed_from_session_default(self):
        from wallet.refund_priority import require_current_priority_read
        connection = MagicMock(vendor='mysql', in_atomic_block=True)
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch('wallet.refund_priority.transaction.get_connection', return_value=connection):
            cursor.fetchone.return_value = ('REPEATABLE READ',)
            with self.assertRaises(ValidationError):
                require_current_priority_read()
            cursor.fetchone.return_value = None
            with self.assertRaises(ValidationError):
                require_current_priority_read()

    def test_earlier_gift_with_open_aftersale_does_not_block_releasable_income(self):
        self.configure(1, 1)
        earlier = self.send('priority-open-case-gift')
        case = self.clients[0].post(f'/api/orders/orders/{earlier.pk}/aftersale/',
            {'reason': '礼物待核对', 'client_request_id': 'priority-open-case'}, format='json')
        self.assertEqual(case.data['code'], 0, case.data)
        deficit = self.gap(2000)
        current = earlier.completed_at + timedelta(hours=2)
        with patch('django.utils.timezone.now', return_value=current):
            self.configure(0, 0, version=1)
            later = self.send('priority-unblocked-gift')
            self.assertFalse(IncomeHold.objects.filter(income_transaction__order=later).exists())
            deficit.refresh_from_db()
            self.assertEqual(deficit.remaining, 400)

    def test_corrupt_due_income_blocks_gift_and_rolls_back_payment(self):
        self.configure(1, 1)
        earlier = self.send('priority-corrupt-earlier')
        deficit = self.gap(2000)
        prior = IncomeHold.objects.get(income_transaction__order=earlier)
        current = prior.release_at + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=current), transaction.atomic():
            self.configure(0, 0, version=1)
            IncomeHold.objects.filter(pk=prior.pk).update(recovered_amount=prior.amount + 1)
            self.boss_wallet.refresh_from_db()
            before = self.boss_wallet.balance
            result = self.clients[0].post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
                'provider_id': self.account.pk, 'quantity': 1, 'expected_amount': 2000,
                'client_request_id': 'priority-corrupt-later'}, format='json')
            self.assertNotEqual(result.status_code, 200)
            self.boss_wallet.refresh_from_db(); deficit.refresh_from_db()
            self.assertEqual((self.boss_wallet.balance, deficit.remaining), (before, 2000))
            transaction.set_rollback(True)

    def test_unavailable_isolation_proof_rolls_back_gift(self):
        self.configure(0, 0)
        deficit = self.gap(2000)
        self.boss_wallet.refresh_from_db()
        before = (self.boss_wallet.balance, Transaction.objects.count())
        with patch('wallet.refund_priority.require_current_priority_read',
                   side_effect=ValidationError('收益释放顺序暂无法核对')):
            result = self.clients[0].post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
                'provider_id': self.account.pk, 'quantity': 1, 'expected_amount': 2000,
                'client_request_id': 'priority-isolation-failed'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.boss_wallet.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((self.boss_wallet.balance, Transaction.objects.count()), before)
        self.assertEqual(deficit.remaining, 2000)

    def test_late_aftersale_without_deficit_keeps_normal_release(self):
        self.configure(1, 1)
        earlier = self.send('no-gap-case-earlier')
        opened = self.clients[0].post(f'/api/orders/orders/{earlier.pk}/aftersale/', {
            'reason': '核对礼物', 'client_request_id': 'no-gap-case-open'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        later = self.send('no-gap-case-later')
        first = IncomeHold.objects.get(income_transaction__order=earlier)
        second = IncomeHold.objects.get(income_transaction__order=later)
        staff = make_console_user(['order:view', 'order:settle'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        current = max(first.release_at, second.release_at) + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=current):
            resolved = admin.post(f"/api/admin/aftersales/{opened.data['data']['id']}/resolve/",
                                  {'resolution': '已核实并完成'}, format='json')
            self.assertEqual(resolved.data['code'], 0, resolved.data)
            self.assertEqual(release_due_income(now=current), 2)
        first.refresh_from_db(); second.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((first.released_at, second.released_at, self.wallet.balance), (current, current, 3200))

    def test_unordered_aftersale_closure_stays_pending(self):
        self.configure(1, 1)
        earlier = self.send('uncertain-case-earlier')
        opened = self.clients[0].post(f'/api/orders/orders/{earlier.pk}/aftersale/', {
            'reason': '核对礼物', 'client_request_id': 'uncertain-case-open'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        staff = make_console_user(['order:view', 'order:settle'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        resolved = admin.post(f"/api/admin/aftersales/{opened.data['data']['id']}/resolve/",
                              {'resolution': '已核实并完成'}, format='json')
        self.assertEqual(resolved.data['code'], 0, resolved.data)
        later = self.send('uncertain-case-later')
        first = IncomeHold.objects.get(income_transaction__order=earlier)
        second = IncomeHold.objects.get(income_transaction__order=later)
        deficit = self.gap(2000)
        current = max(first.release_at, second.release_at) + timedelta(minutes=1)
        with self.assertLogs('wallet.income', level='WARNING'):
            self.assertEqual(release_due_income(now=current), 0)
        first.refresh_from_db(); second.refresh_from_db(); deficit.refresh_from_db()
        self.assertEqual((first.released_at, second.released_at, deficit.remaining), (None, None, 2000))

    def test_late_aftersale_closure_does_not_use_freeze_deadline_as_priority(self):
        from orders.tests.factories import make_provider
        from wallet.services import get_wallet
        self.configure(1, 1)
        earlier = self.send('priority-case-earlier')
        opened = self.clients[0].post(f'/api/orders/orders/{earlier.pk}/aftersale/', {
            'reason': '核对礼物', 'client_request_id': 'priority-case-open'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        later = self.send('priority-case-later')
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        other.escort_profile.account = other_account
        other.escort_profile.save(update_fields=['account'])
        gift = self.clients[0].post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
            'provider_id': other_account.pk, 'quantity': 1, 'expected_amount': 2000,
            'client_request_id': 'priority-other-wallet'}, format='json')
        self.assertEqual(gift.data['code'], 0, gift.data)
        other_order = Order.objects.get(pk=gift.data['data']['id'])
        other_wallet = get_wallet(account=other_account, user=other)
        first_hold = IncomeHold.objects.get(income_transaction__order=earlier)
        second_hold = IncomeHold.objects.get(income_transaction__order=later)
        other_hold = IncomeHold.objects.get(income_transaction__order=other_order)
        deficit = self.gap(2000)
        staff = make_console_user(['order:view', 'order:settle'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        current = max(first_hold.release_at, second_hold.release_at, other_hold.release_at) + timedelta(minutes=1)
        with patch('django.utils.timezone.now', return_value=current):
            resolved = admin.post(f"/api/admin/aftersales/{opened.data['data']['id']}/resolve/",
                                  {'resolution': '已核实并完成'}, format='json')
            self.assertEqual(resolved.data['code'], 0, resolved.data)
            from orders.models import AfterSale
            self.assertEqual(AfterSale.objects.get(pk=opened.data['data']['id']).history[-1].get(
                'gift_income_priority_guard'), 1)
            self.assertEqual(release_due_income(now=current, limit=1), 1)
            first_hold.refresh_from_db(); second_hold.refresh_from_db(); other_hold.refresh_from_db()
            deficit.refresh_from_db(); other_wallet.refresh_from_db()
            self.assertEqual((first_hold.released_at, second_hold.released_at, deficit.remaining), (None, current, 400))
            self.assertEqual((other_hold.released_at, other_wallet.balance), (None, 0))
            self.assertEqual(release_due_income(now=current), 2)
            first_hold.refresh_from_db(); deficit.refresh_from_db(); other_wallet.refresh_from_db()
            self.assertEqual((first_hold.released_at, deficit.remaining, other_wallet.balance), (current, 0, 1600))


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ALLOWED_HOSTS=['*'])
class RefundIncomePriorityStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    send = GiftFixture.send
    configure = GiftFixture.configure
    gap = RefundIncomePriorityTest.gap

    def test_priority_is_reconciled_independently_in_both_stores(self):
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                RefundIncomePriorityTest.setUp(self)
                RefundIncomePriorityTest.test_due_gift_precedes_later_zero_clock_gift_and_clearance_accepts_queue(self)

    def test_late_aftersale_does_not_starve_other_wallet_in_either_store(self):
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                RefundIncomePriorityTest.setUp(self)
                RefundIncomePriorityTest.test_late_aftersale_closure_does_not_use_freeze_deadline_as_priority(self)


@skipUnless(connection.vendor == 'mysql' and os.environ.get('DJANGO_SETTINGS_MODULE') ==
            'config.ordinary_followup_mysql_test_settings', 'requires the disposable MySQL runner')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ALLOWED_HOSTS=['*'])
class RefundIncomePriorityMySQLTest(TransactionTestCase):
    setUp = RefundIncomePriorityTest.setUp
    send = GiftFixture.send
    configure = GiftFixture.configure
    gap = RefundIncomePriorityTest.gap

    def test_late_aftersale_does_not_starve_other_wallet(self):
        RefundIncomePriorityTest.test_late_aftersale_closure_does_not_use_freeze_deadline_as_priority(self)

    def test_due_release_and_new_zero_clock_gift_race(self):
        self.configure(1, 1)
        earlier = self.send('priority-mysql-earlier')
        deficit = self.gap(2000)
        prior = IncomeHold.objects.get(income_transaction__order=earlier)
        current = prior.release_at + timedelta(minutes=1)
        store, token, gate = resolve_store(), issue_account_tokens(self.accounts[0])[0], Barrier(2)
        with patch('django.utils.timezone.now', return_value=current):
            self.configure(0, 0, version=1)

            def release():
                close_old_connections()
                try:
                    with store_context(store):
                        gate.wait(timeout=15)
                        return release_due_income(now=current, limit=1)
                finally:
                    close_old_connections()

            def send():
                close_old_connections()
                try:
                    with store_context(store):
                        client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                        gate.wait(timeout=15)
                        return client.post('/api/orders/gifts/send/', {'gift_service_id': self.gift.pk,
                            'provider_id': self.account.pk, 'quantity': 1, 'expected_amount': 2000,
                            'client_request_id': 'priority-mysql-later'}, format='json').data
                finally:
                    close_old_connections()

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = pool.submit(release), pool.submit(send)
                first.result(timeout=30)
                result = second.result(timeout=30)
            self.assertEqual(result['code'], 0, result)
            later_id = result['data']['id']
            release_due_income(now=current)
            deficit.refresh_from_db()
            self.assertEqual(deficit.remaining, 0)
            self.assertEqual(list(RefundDepositIncomeApplication.objects.order_by('pk')
                .values_list('source_transaction__order_id', flat=True)), [earlier.pk, later_id])
