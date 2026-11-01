from django.db import connection
from types import SimpleNamespace
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.models import LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from console.serializers import AdminProviderReportSerializer
from orders.direct_clearing import post_test_clearing
from orders.models import OrderProvider, OrderRefund
from orders.serializers import OrderSerializer
from orders.tests.factories import make_console_user, make_order, make_provider, make_user, make_wallet
from orders.tests.test_direct_clearing import ClearingFixture
from wallet.models import ProviderReport, Transaction
from wallet.serializers import ReportSerializer
from wallet.settlement_display import order_settlement_status, prepare_order_settlement_display, report_settlement_status


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class SettlementDisplayTest(ClearingFixture, TestCase):
    def fund(self, order):
        wallet = make_wallet(order.customer, balance=0)
        return Transaction.objects.create(wallet=wallet, order=order, amount=-order.amount,
                                          tx_type='PAY', balance_before=order.amount, balance_after=0)

    def test_simulation_never_becomes_wallet_income_or_exposes_channel_credentials(self):
        order, intent, _ = self.make_completed(count=1)
        report = order.provider_reports.first()
        post_test_clearing(order.pk)
        self.assertIsNone(order.settled_at)
        self.assertEqual(order_settlement_status(order), 'REVIEW')
        for data in (OrderSerializer(order).data, ReportSerializer(report).data,
                     AdminProviderReportSerializer(report).data):
            self.assertEqual(data['settlement_status'], 'REVIEW')
            for private in ('mchid', 'appid', 'openid', 'transaction_id', 'merchant_order_no'):
                self.assertNotIn(private, data)
        self.assertEqual(OrderSerializer([order], many=True).data[0]['settlement_status'], 'REVIEW')
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_balance_requires_matching_successful_income(self):
        customer, provider = make_user(), make_provider()
        wallet = make_wallet(provider, balance=800, frozen_amount=800)
        order = make_order(customer, provider=provider, provider_income=800)
        self.fund(order)
        self.assertEqual(order_settlement_status(order), 'WAITING')
        order.settled_at = timezone.now()
        order.save(update_fields=['settled_at'])
        self.assertEqual(order_settlement_status(order), 'REVIEW')
        tx = Transaction.objects.create(wallet=wallet, order=order, amount=800,
                                        tx_type='INCOME', status='SUCCESS', balance_after=800)
        self.assertEqual(order_settlement_status(order), 'SETTLED')
        wallet.refresh_from_db()
        self.assertEqual(wallet.frozen_amount, 800)
        tx.status = 'FAILED'
        tx.save(update_fields=['status'])
        self.assertEqual(order_settlement_status(order), 'REVIEW')

    def test_manual_report_requires_its_real_matching_transaction(self):
        provider = make_provider()
        wallet = make_wallet(provider, balance=0)
        report = ProviderReport.objects.create(provider=provider, game_name='历史报单', amount=1000,
                                                payout_amount=800, status='APPROVED')
        self.assertEqual(report_settlement_status(report), 'REVIEW')
        tx = Transaction.objects.create(wallet=wallet, amount=800, tx_type='INCOME', status='SUCCESS', balance_after=800)
        report.transaction = tx
        report.save(update_fields=['transaction'])
        self.assertEqual(report_settlement_status(report), 'SETTLED')
        tx.amount = 799
        tx.save(update_fields=['amount'])
        report.refresh_from_db()
        self.assertEqual(report_settlement_status(report), 'REVIEW')

    def test_manual_report_reversal_must_be_reviewed_even_when_approved(self):
        provider = make_provider()
        wallet = make_wallet(provider)
        tx = Transaction.objects.create(wallet=wallet, amount=800, tx_type='INCOME', balance_after=800)
        report = ProviderReport.objects.create(provider=provider, game_name='历史报单', amount=1000,
                                               payout_amount=800, status='APPROVED', transaction=tx)
        self.assertEqual(report_settlement_status(report), 'SETTLED')
        Transaction.objects.create(wallet=wallet, amount=-100, tx_type='INCOME', reversal_of=tx,
                                   balance_before=800, balance_after=700)
        self.assertEqual(report_settlement_status(report), 'REVIEW')

    def test_two_provider_wallets_and_zero_share_are_checked_independently(self):
        customer, first, second = make_user(), make_provider(), make_provider()
        first_wallet = make_wallet(first)
        order = make_order(customer, provider=first, amount=1000, required_provider_count=2, settled_at=timezone.now())
        self.fund(order)
        stamp = timezone.now()
        OrderProvider.objects.create(order=order, provider=first, settlement_base=500,
                                     provider_income=400, settled_at=stamp)
        OrderProvider.objects.create(order=order, provider=second, settlement_base=500,
                                     provider_income=0, settled_at=stamp)
        self.assertEqual(order_settlement_status(order), 'REVIEW')
        Transaction.objects.create(wallet=first_wallet, order=order, amount=400,
                                   tx_type='INCOME', status='SUCCESS', balance_after=400)
        self.assertEqual(order_settlement_status(order), 'SETTLED')
        Transaction.objects.create(wallet=make_wallet(second), order=order, amount=10,
                                   tx_type='INCOME', status='SUCCESS', balance_after=10)
        self.assertEqual(order_settlement_status(order), 'REVIEW')

    def test_legacy_wallet_requires_proven_mapping_when_order_has_bound_account(self):
        customer, provider = make_user(), make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        wallet = make_wallet(provider)
        order = make_order(customer, provider=provider, provider_account=account, amount=1000,
                           provider_income=800, settled_at=timezone.now())
        self.fund(order)
        Transaction.objects.create(wallet=wallet, order=order, amount=800, tx_type='INCOME', balance_after=800)
        self.assertIsNone(wallet.account_id)
        self.assertEqual(order_settlement_status(order), 'SETTLED')
        LegacyAccountMap.objects.filter(legacy_user_id=provider.pk).delete()
        self.assertEqual(order_settlement_status(order), 'REVIEW')

    def test_released_before_service_is_ignored_but_settled_released_share_is_verified(self):
        customer, first, second = make_user(), make_provider(), make_provider()
        wallet = make_wallet(second)
        stamp = timezone.now()
        order = make_order(customer, provider=second, amount=500, settled_at=stamp)
        self.fund(order)
        old = OrderProvider.objects.create(order=order, provider=first, released_at=stamp)
        OrderProvider.objects.create(order=order, provider=second, settlement_base=500,
                                     provider_income=400, settled_at=stamp)
        Transaction.objects.create(wallet=wallet, order=order, amount=400, tx_type='INCOME', balance_after=400)
        self.assertEqual(order_settlement_status(order), 'SETTLED')
        old.settled_at, old.settlement_base, old.provider_income = stamp, 100, 80
        old.save(update_fields=['settled_at', 'settlement_base', 'provider_income'])
        self.assertEqual(order_settlement_status(order), 'REVIEW')

    def test_bulk_settlement_evidence_has_constant_queries_per_bounded_page(self):
        customer, provider = make_user(), make_provider()
        wallet = make_wallet(provider)
        orders = []
        for _ in range(20):
            order = make_order(customer, provider=provider, provider_income=80, settled_at=timezone.now())
            self.fund(order)
            Transaction.objects.create(wallet=wallet, order=order, amount=80, tx_type='INCOME', balance_after=80)
            orders.append(order)
        for rows in (orders[:1], orders):
            with CaptureQueriesContext(connection) as captured:
                prepare_order_settlement_display(rows)
                self.assertEqual([order_settlement_status(order) for order in rows], ['SETTLED'] * len(rows))
            self.assertEqual(len(captured), 4)

    def test_paid_flag_cannot_hide_refund_record_refund_payment_or_reversal(self):
        for evidence in ('record', 'refund', 'reversal', 'reversal_other_order', 'bad_snapshot'):
            customer, provider = make_user(), make_provider()
            wallet = make_wallet(provider)
            order = make_order(customer, provider=provider, amount=1000, provider_income=800, settled_at=timezone.now())
            self.fund(order)
            income = Transaction.objects.create(wallet=wallet, order=order, amount=800,
                                                tx_type='INCOME', balance_after=800)
            self.assertEqual(order_settlement_status(order), 'SETTLED')
            if evidence == 'record':
                OrderRefund.objects.create(order=order, amount=100, reason='test')
            elif evidence == 'refund':
                Transaction.objects.create(wallet=make_wallet(customer), order=order, amount=100,
                                           tx_type='REFUND', balance_after=100)
            elif evidence in ('reversal', 'reversal_other_order'):
                other = make_order(customer) if evidence == 'reversal_other_order' else order
                Transaction.objects.create(wallet=wallet, order=other, amount=-100, tx_type='INCOME',
                                           reversal_of=income, balance_before=800, balance_after=700)
            else:
                income.balance_after = 0
                income.save(update_fields=['balance_after'])
            self.assertEqual(order.payment_status, 'PAID')
            self.assertEqual(order_settlement_status(order), 'REVIEW', evidence)

    def test_incomplete_double_and_invalid_report_never_inherit_settled(self):
        customer, provider, outsider = make_user(), make_provider(), make_provider()
        wallet = make_wallet(provider)
        account = get_or_create_account_for_legacy_user(provider)
        order = make_order(customer, provider=provider, amount=1000, required_provider_count=2, settled_at=timezone.now())
        self.fund(order)
        share = OrderProvider.objects.create(order=order, provider=provider, provider_account=account,
                     settlement_base=1000, provider_income=800, settled_at=timezone.now())
        Transaction.objects.create(wallet=wallet, order=order, amount=800, tx_type='INCOME', balance_after=800)
        self.assertEqual(order_settlement_status(order), 'REVIEW')
        order.required_provider_count = 1
        order.save(update_fields=['required_provider_count'])
        self.assertEqual(order_settlement_status(order), 'SETTLED')
        report = ProviderReport.objects.create(order=order, provider=provider, provider_account=account,
                           game_name='服务', amount=1000, payout_amount=800, status='REJECTED')
        self.assertEqual(report_settlement_status(report), 'REVIEW')
        report.status = 'APPROVED'
        report.save(update_fields=['status'])
        self.assertEqual(report_settlement_status(report), 'SETTLED')
        report.payout_amount = 799
        report.save(update_fields=['payout_amount'])
        self.assertEqual(report_settlement_status(report), 'REVIEW')
        data = ReportSerializer(report).data
        self.assertIsNone(data['expected_income'])
        self.assertIsNone(data['payout_amount'])
        report.provider = outsider
        report.save(update_fields=['provider'])
        self.assertEqual(report_settlement_status(report), 'REVIEW')
        share.released_at = timezone.now()
        share.save(update_fields=['released_at'])
        self.assertEqual(report_settlement_status(report), 'REVIEW')

    def test_double_report_api_never_returns_other_provider_income_when_share_missing(self):
        order, _, providers = self.make_completed(count=2)
        report = order.provider_reports.get(provider=providers[0])
        OrderProvider.objects.filter(order=order, provider=providers[0]).delete()
        account = get_or_create_account_for_legacy_user(providers[0])
        serialized_order = OrderSerializer(context={'request': SimpleNamespace(account=account, legacy_user=providers[0])})
        self.assertIsNone(serialized_order.get_expected_income(order))
        token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/api/wallet/reports/')
        self.assertEqual(response.status_code, 200)
        result = next(row for row in response.data['data'] if row['id'] == report.pk)
        self.assertIsNone(result['expected_income'])
        self.assertIsNone(result['payout_amount'])
        self.assertEqual(result['settlement_status'], 'REVIEW')

    def test_mismatched_account_and_user_do_not_expose_another_persons_income(self):
        order, _, providers = self.make_completed(count=2)
        outsider = make_provider()
        outsider_account = get_or_create_account_for_legacy_user(outsider)
        report = order.provider_reports.get(provider=providers[0])
        OrderProvider.objects.filter(order=order, provider=providers[0]).update(provider_account=outsider_account)
        report.provider_account = outsider_account
        report.save(update_fields=['provider_account'])
        token, _ = issue_account_tokens(outsider_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = client.get('/api/wallet/reports/')
        result = next(row for row in response.data['data'] if row['id'] == report.pk)
        self.assertIsNone(result['expected_income'])
        self.assertIsNone(result['payout_amount'])
        self.assertEqual(result['settlement_status'], 'REVIEW')
        serialized = OrderSerializer(context={'request': SimpleNamespace(account=outsider_account, legacy_user=outsider)})
        self.assertIsNone(serialized.get_expected_income(order))

    def test_report_api_query_count_does_not_grow_per_order(self):
        customer, provider = make_user(), make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        wallet = make_wallet(provider)
        token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        def add_report():
            order = make_order(customer, provider=provider, provider_account=account, amount=1000,
                               provider_income=800, settled_at=timezone.now())
            self.fund(order)
            OrderProvider.objects.create(order=order, provider=provider, provider_account=account,
                                         settlement_base=1000, provider_income=800, settled_at=timezone.now())
            Transaction.objects.create(wallet=wallet, order=order, amount=800, tx_type='INCOME', balance_after=800)
            ProviderReport.objects.create(order=order, provider=provider, provider_account=account,
                                         game_name='服务', amount=1000, payout_amount=800, status='APPROVED')
        add_report()
        with CaptureQueriesContext(connection) as one:
            response = client.get('/api/wallet/reports/')
            self.assertEqual(response.status_code, 200)
        for _ in range(19):
            add_report()
        with CaptureQueriesContext(connection) as twenty:
            response = client.get('/api/wallet/reports/')
            self.assertEqual(len(response.data['data']), 20)
        self.assertEqual(len(twenty), len(one), (len(one), len(twenty)))

    def test_report_status_respects_existing_account_scope_and_staff_permissions(self):
        order, _, providers = self.make_completed(count=1)
        outsider = make_provider()
        client = APIClient()
        def authenticate(user):
            account = get_or_create_account_for_legacy_user(user)
            token, _ = issue_account_tokens(account)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            return account

        authenticate(outsider)
        response = client.get('/api/wallet/reports/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(any(row['order_id'] == order.pk for row in response.data['data']))
        authenticate(providers[0])
        response = client.get('/api/wallet/reports/')
        own = [row for row in response.data['data'] if row['order_id'] == order.pk]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]['settlement_status'], 'REVIEW')
        staff = make_console_user([])
        account = authenticate(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.assertEqual(client.get('/api/admin/reports/').status_code, 403)
        viewer = make_console_user(['report:view'])
        account = authenticate(viewer)
        viewer.admin_membership.account = account
        viewer.admin_membership.save(update_fields=['account'])
        response = client.get('/api/admin/reports/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['data']
        if isinstance(rows, dict):
            rows = rows['list']
        self.assertEqual(next(row for row in rows if row['order'] == order.pk)['settlement_status'], 'REVIEW')
