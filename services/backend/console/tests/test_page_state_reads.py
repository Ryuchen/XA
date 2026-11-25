"""Replay the thirteen audited admin reads against real related records.

These are isolated API contract tests, not browser or financial acceptance.
Every request uses the same URL and query shape as the admin frontend and a
real store-account JWT. Records are created through the existing test factories
and model constraints so list serializers execute their non-empty paths.
"""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminMembership
from console.payroll_calculation import plan_snapshot
from console.payroll_models import (
    PayrollConfig,
    PayrollRun,
    SalaryActivity,
    SalaryAssignment,
    SalaryPlan,
)
from console.permissions import ALL_PERMISSIONS
from orders.models import Order, OrderSegmentSettlementManifest, Reservation
from orders.tests.factories import (
    make_completed_order,
    make_console_user,
    make_evaluation,
    make_promotion,
    make_provider,
    make_service,
    make_user,
    make_wallet,
)
from wallet.models import ProviderReport, RechargeRecord, Transaction


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
)
class AuditedPageReadTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = make_console_user(ALL_PERMISSIONS)
        cls.staff_account = get_or_create_account_for_legacy_user(cls.staff_user)
        link_legacy_relations(cls.staff_user, cls.staff_account)
        AdminMembership.objects.filter(user=cls.staff_user).update(account=cls.staff_account)

        cls.boss_user = make_user(nickname='读取回归老板')
        cls.boss_account = get_or_create_account_for_legacy_user(cls.boss_user)
        link_legacy_relations(cls.boss_user, cls.boss_account)
        cls.provider_user = make_provider(display_name='读取回归陪玩')
        cls.provider_account = get_or_create_account_for_legacy_user(cls.provider_user)
        link_legacy_relations(cls.provider_user, cls.provider_account)
        profile = cls.provider_user.escort_profile
        profile.account = cls.provider_account
        profile.save(update_fields=['account'])

        cls.service = make_service(name='读取回归服务', price=10000)
        cls.order = make_completed_order(
            cls.boss_user,
            cls.provider_user,
            service=cls.service,
            amount=10000,
            provider_income=8000,
            payment_status=Order.PaymentStatus.PAID,
            customer_account=cls.boss_account,
            provider_account=cls.provider_account,
        )
        cls.report = ProviderReport.objects.create(
            provider=cls.provider_user,
            provider_account=cls.provider_account,
            order=cls.order,
            game_name='读取回归游戏',
            amount=10000,
            status=ProviderReport.Status.PENDING,
        )
        make_evaluation(cls.order, content='读取回归评价')
        make_promotion(title='读取回归活动', items=[cls.service])
        cls.wallet = make_wallet(cls.boss_user, balance=20000)
        cls.wallet.account = cls.boss_account
        cls.wallet.save(update_fields=['account'])
        Transaction.objects.create(
            wallet=cls.wallet,
            order=cls.order,
            tx_type=Transaction.TxType.PAY,
            amount=-10000,
            balance_before=20000,
            balance_after=10000,
            operator_account=cls.staff_account,
        )

        start = timezone.now() + timedelta(days=1)
        cls.reservation = Reservation.objects.create(
            customer=cls.boss_user,
            customer_account=cls.boss_account,
            provider=cls.provider_user,
            provider_account=cls.provider_account,
            service=cls.service,
            start_time=start,
            end_time=start + timedelta(hours=1),
            duration_minutes=60,
            estimated_amount=10000,
            status=Reservation.Status.PENDING,
        )
        SalaryActivity.objects.create(
            kind=SalaryActivity.Kind.DISPATCH,
            account=cls.staff_account,
            source_key=f'ORDER:{cls.order.pk}',
            order=cls.order,
            source_snapshot={},
            performed_at=timezone.now(),
            reason='读取回归：原派单归属',
            created_by=cls.staff_account,
        )
        PayrollConfig.objects.create(pk=1, night_start=None, night_end=None, revision=1)
        PayrollRun.objects.create(
            month=timezone.localdate().replace(day=1),
            fingerprint='a' * 64,
            snapshot={'lines': [], 'totals': {'total_fen': 0}},
            confirmed_by=cls.staff_account,
        )

    def setUp(self):
        token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def read_ok(self, url, params=None):
        response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response.data.get('code'), 0, response.data)
        return response.data['data']

    def assert_fields(self, row, *fields):
        missing = sorted(set(fields) - row.keys())
        self.assertEqual(missing, [], f'{row!r} missing fields: {missing}')

    def test_dashboard_stats_with_order(self):
        data = self.read_ok('/api/admin/dashboard/stats')
        self.assertGreaterEqual(data['order_total'], 1)
        self.assertTrue(data['recent_orders'])
        self.assert_fields(
            data['recent_orders'][0], 'order_no', 'status', 'amount',
            'customer', 'provider',
        )

    def test_orders_with_order(self):
        data = self.read_ok('/api/admin/orders/', {'page': 1, 'page_size': 10})
        self.assertIn(self.order.pk, [row['id'] for row in data['list']])
        row = next(row for row in data['list'] if row['id'] == self.order.pk)
        self.assert_fields(
            row, 'order_no', 'status', 'payment_status', 'amount',
            'service_item', 'customer', 'provider', 'settlement_status',
            'provider_income', 'service_name_snapshot', 'can_aftersale',
        )

    def test_reservations_with_reservation(self):
        data = self.read_ok('/api/orders/reservations/', {
            'page': 1, 'page_size': 10, 'status': '',
        })
        self.assertIn(self.reservation.pk, [row['id'] for row in data['items']])
        row = next(row for row in data['items'] if row['id'] == self.reservation.pk)
        self.assert_fields(
            row, 'reservation_no', 'status', 'start_time', 'end_time',
            'customer', 'provider', 'seats', 'service', 'allowed_actions',
        )
        filtered = self.read_ok('/api/orders/reservations/', {
            'page': 1, 'page_size': 10, 'status': 'pending', 'sort_start': 'asc',
        })
        self.assertIn(self.reservation.pk, [row['id'] for row in filtered['items']])

    def test_reports_with_report(self):
        data = self.read_ok('/api/admin/reports/', {'page': 1, 'page_size': 10})
        self.assertIn(self.report.pk, [row['id'] for row in data['list']])
        row = next(row for row in data['list'] if row['id'] == self.report.pk)
        self.assert_fields(row, 'order', 'provider', 'status', 'amount', 'expected_income', 'created_at')

    def test_evaluations_with_evaluation(self):
        data = self.read_ok('/api/admin/evaluations/', {
            'page': 1, 'page_size': 10, 'keyword': '', 'only_unreplied': '', 'score': '',
        })
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['order'], self.order.pk)
        self.assert_fields(
            data['list'][0], 'order', 'provider', 'customer', 'score',
            'content', 'reply_content', 'created_at',
        )

    def test_escorts_with_profile(self):
        data = self.read_ok('/api/admin/escorts/', {
            'page': 1, 'page_size': 10, 'keyword': '', 'status': '',
            'is_verified': '', 'gender': '',
        })
        self.assertIn(self.provider_user.escort_profile.pk, [row['id'] for row in data['list']])
        row = next(row for row in data['list'] if row['id'] == self.provider_user.escort_profile.pk)
        self.assert_fields(
            row, 'user', 'username', 'display_name', 'phone', 'status',
            'price_per_hour', 'avatar_url', 'service_item_names',
        )

    def test_player_dashboard_with_completed_order(self):
        today = timezone.localdate()
        data = self.read_ok('/api/admin/dashboard/player', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat(),
        })
        self.assert_fields(
            data, 'kpi', 'secondary', 'rank', 'income_review', 'snapshot_at', 'timezone',
        )

    def test_service_items_with_service(self):
        data = self.read_ok('/api/admin/service-items/', {
            'page': 1, 'page_size': 10, 'keyword': '', 'is_active': '', 'gameplay': '',
        })
        self.assertIn(self.service.pk, [row['id'] for row in data['list']])
        row = next(row for row in data['list'] if row['id'] == self.service.pk)
        self.assert_fields(
            row, 'name', 'price', 'price_unit', 'is_active', 'gameplay',
            'gameplay_name', 'service_type',
        )

    def test_promotions_with_promotion(self):
        data = self.read_ok('/api/admin/promotions/', {'page': 1, 'page_size': 10})
        self.assertEqual(data['total'], 1)
        self.assertTrue(data['list'][0]['item_names'])
        self.assert_fields(
            data['list'][0], 'title', 'item_names', 'discount_rate',
            'start_at', 'end_at', 'scope', 'is_active', 'window_status',
        )

    def test_transactions_with_payment(self):
        data = self.read_ok('/api/admin/transactions/', {'page': 1, 'page_size': 10})
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['order_no'], self.order.order_no)
        self.assert_fields(
            data['list'][0], 'order_no', 'wallet', 'username', 'tx_type',
            'amount', 'balance_before', 'balance_after', 'status', 'created_at',
        )
        filtered = self.read_ok('/api/admin/transactions/', {
            'page': 1, 'page_size': 10, 'order_no_exact': self.order.order_no,
        })
        self.assertEqual(filtered['total'], 1)

    def test_salary_activities_with_dispatch(self):
        data = self.read_ok('/api/admin/salary-activities/', {
            'page': 1, 'page_size': 10, 'month': timezone.localdate().strftime('%Y-%m'),
            'kind': 'DISPATCH',
        })
        self.assertGreaterEqual(data['total'], 1)
        self.assertEqual(data['list'][0]['order_no'], self.order.order_no)
        self.assert_fields(
            data['list'][0], 'order_no', 'account', 'account_name', 'kind',
            'source_amount_fen', 'performed_at', 'created_at', 'is_void',
        )

    def test_payroll_three_independent_reads(self):
        month = timezone.localdate().strftime('%Y-%m')
        preview = self.read_ok('/api/admin/payroll/preview/', {'month': month})
        config = self.read_ok('/api/admin/payroll/config/')
        runs = self.read_ok('/api/admin/payroll/runs/', {'month': month})
        self.assert_fields(preview, 'lines', 'totals', 'status', 'can_confirm', 'run_id')
        self.assert_fields(config, 'revision', 'night_start', 'night_end')
        self.assert_fields(
            runs['list'][0], 'month', 'fingerprint', 'snapshot',
            'confirmed_at', 'confirmed_by_name',
        )

    def test_operating_roi_preserves_incomplete_financial_state(self):
        today = timezone.localdate()
        data = self.read_ok('/api/admin/operating-roi/', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat(),
        })
        self.assert_fields(
            data, 'start_date', 'end_date', 'currency', 'roas', 'operating',
            'provisional', 'daily', 'warnings', 'basis',
        )
        self.assert_fields(
            data['operating'], 'net_consumption_fen', 'salary_cost_fen',
            'ad_spend_fen', 'total_cost_fen', 'profit_fen', 'roi', 'complete',
        )
        self.assert_fields(data['roas'], 'net_consumption_fen', 'ad_spend_fen', 'value')
        self.assertFalse(data['operating']['complete'])
        self.assertIsNone(data['operating']['total_cost_fen'])
        self.assertIsNone(data['operating']['profit_fen'])
        self.assertTrue(data['operating']['unknown_cost_order_ids'])
        self.assertTrue(data['warnings'])

    def current_month_payroll_plan(self):
        month = timezone.localdate().replace(day=1)
        PayrollRun.objects.filter(month=month).delete()
        position = BusinessDictionaryItem.objects.create(
            type=BusinessDictionaryItem.Type.SALARY_POSITION,
            code='M0_READ_POSITION',
            name='读取回归岗位',
        )
        plan = SalaryPlan.objects.create(
            position=position,
            name='读取回归薪资方案',
            base_fen=1000,
            recharge_bps=1000,
        )
        SalaryAssignment.objects.create(
            month=month,
            account=self.staff_account,
            plan=plan,
            plan_snapshot=plan_snapshot(plan),
            created_by=self.staff_account,
        )

    @staticmethod
    def missing_recharge_activity(account, creator, *, recognition_month=None):
        return SalaryActivity.objects.create(
            kind=SalaryActivity.Kind.RECHARGE,
            account=account,
            source_key='M0_READ_MISSING_RECHARGE',
            recharge=None,
            source_snapshot={},
            performed_at=timezone.now(),
            recognition_month=recognition_month,
            reason='读取回归：充值凭证缺失',
            created_by=creator,
        )

    def proved_recharge_activity(self, *, trade_no='M0_READ_PROVED_RECHARGE',
                                 recognition_month=None, late_compensation=None):
        tx = Transaction.objects.create(
            wallet=self.wallet,
            tx_type=Transaction.TxType.TOPUP,
            amount=10000,
            balance_before=10000,
            balance_after=20000,
            status=Transaction.Status.SUCCESS,
        )
        recharge = RechargeRecord.objects.create(
            account=self.boss_account,
            amount=10000,
            trade_no=trade_no,
            recharge_tx=tx,
        )
        source_snapshot = {
            'recharge_tx_id': tx.pk,
            'source_amount_raw': recharge.amount,
            'buyer_account_id': self.boss_account.pk,
            'trade_no': recharge.trade_no,
        }
        if late_compensation is not None:
            source_snapshot['late_compensation'] = late_compensation
        return SalaryActivity.objects.create(
            kind=SalaryActivity.Kind.RECHARGE,
            account=self.staff_account,
            source_key=f'RECHARGE:{recharge.pk}',
            recharge=recharge,
            source_snapshot=source_snapshot,
            performed_at=tx.created_at,
            recognition_month=recognition_month,
            reason='读取回归：已核实充值保持已知金额',
            created_by=self.staff_account,
        )

    def test_salary_activity_missing_recharge_returns_unknown_amount(self):
        activity = self.missing_recharge_activity(self.staff_account, self.staff_account)
        data = self.read_ok('/api/admin/salary-activities/', {
            'page': 1, 'page_size': 10, 'kind': 'RECHARGE',
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        row = next(row for row in data['list'] if row['id'] == activity.pk)
        self.assert_fields(row, 'source_amount_fen', 'source_snapshot')
        self.assertIsNone(row['source_amount_fen'])

    def test_salary_activity_unproved_recharge_returns_unknown_amount(self):
        recharge = RechargeRecord.objects.create(
            account=self.boss_account,
            amount=12345,
            trade_no='M0_READ_UNPROVED_RECHARGE',
        )
        activity = SalaryActivity.objects.create(
            kind=SalaryActivity.Kind.RECHARGE,
            account=self.staff_account,
            source_key=f'RECHARGE:{recharge.pk}',
            recharge=recharge,
            source_snapshot={},
            performed_at=timezone.now(),
            reason='读取回归：充值凭证未核实',
            created_by=self.staff_account,
        )
        data = self.read_ok('/api/admin/salary-activities/', {
            'page': 1, 'page_size': 10, 'kind': 'RECHARGE',
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        row = next(row for row in data['list'] if row['id'] == activity.pk)
        self.assertIsNone(row['source_amount_fen'])

    def test_payroll_missing_recharge_preserves_unknown_totals(self):
        self.current_month_payroll_plan()
        self.missing_recharge_activity(self.staff_account, self.staff_account)
        data = self.read_ok('/api/admin/payroll/preview/', {
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        self.assertFalse(data['can_confirm'])
        self.assertTrue(any('充值业绩记录' in warning for warning in data['warnings']))
        line = next(row for row in data['lines'] if row['account'] == self.staff_account.pk)
        self.assert_fields(line, 'evidence_complete', 'recharge_amount_fen',
                           'recharge_commission_fen', 'total_fen')
        self.assertFalse(line['evidence_complete'])
        self.assertIsNone(line['recharge_amount_fen'])
        self.assertIsNone(line['recharge_commission_fen'])
        self.assertIsNone(line['total_fen'])
        self.assertIsNone(data['totals']['recharge_commission_fen'])
        self.assertIsNone(data['totals']['total_fen'])

    def test_proved_recharge_keeps_known_source_and_payroll_amounts(self):
        self.current_month_payroll_plan()
        activity = self.proved_recharge_activity()
        rows = self.read_ok('/api/admin/salary-activities/', {
            'page': 1, 'page_size': 10, 'kind': 'RECHARGE',
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        row = next(row for row in rows['list'] if row['id'] == activity.pk)
        self.assertEqual(row['source_amount_fen'], 10000)

        preview = self.read_ok('/api/admin/payroll/preview/', {
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        line = next(row for row in preview['lines'] if row['account'] == self.staff_account.pk)
        self.assertTrue(line['evidence_complete'])
        self.assertEqual(line['recharge_amount_fen'], 10000)
        self.assertEqual(line['recharge_commission_fen'], 1000)
        self.assertEqual(line['total_fen'], 2000)
        self.assertEqual(preview['totals']['total_fen'], 2000)

    def test_payroll_proved_recharge_after_invalid_keeps_unknown_amount(self):
        self.current_month_payroll_plan()
        invalid = self.missing_recharge_activity(self.staff_account, self.staff_account)
        self.proved_recharge_activity(trade_no='M0_READ_PROVED_AFTER_INVALID')
        data = self.read_ok('/api/admin/payroll/preview/', {
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        line = next(row for row in data['lines'] if row['account'] == self.staff_account.pk)
        self.assertEqual(line['unknown_salary_activity_ids'], [invalid.pk])
        self.assertFalse(line['evidence_complete'])
        self.assertIsNone(line['recharge_amount_fen'])
        self.assertIsNone(line['recharge_commission_fen'])
        self.assertIsNone(line['total_fen'])
        self.assertIsNone(data['totals']['recharge_commission_fen'])
        self.assertIsNone(data['totals']['total_fen'])
        self.assertTrue(any(row['amount_fen'] is None for row in data['daily_costs']))

    def test_payroll_invalid_late_recharge_stays_unknown_after_known_adjustment(self):
        self.current_month_payroll_plan()
        month = timezone.localdate().replace(day=1)
        invalid = self.missing_recharge_activity(
            self.staff_account, self.staff_account, recognition_month=month,
        )
        known = self.proved_recharge_activity(
            trade_no='M0_READ_PROVED_LATE_RECHARGE',
            recognition_month=month,
            late_compensation={'amount_fen': 500},
        )
        data = self.read_ok('/api/admin/payroll/preview/', {'month': month.strftime('%Y-%m')})
        line = next(row for row in data['lines'] if row['account'] == self.staff_account.pk)
        self.assertEqual(line['unknown_salary_activity_ids'], [invalid.pk])
        self.assertIn(known.pk, [row['activity_id'] for row in line['late_adjustments']])
        self.assertIsNone(line['recharge_amount_fen'])
        self.assertIsNone(line['late_adjustments_fen'])
        self.assertIsNone(line['total_fen'])
        self.assertIsNone(data['totals']['late_adjustments_fen'])
        self.assertIsNone(data['totals']['total_fen'])
        self.assertTrue(any(row['amount_fen'] is None for row in data['daily_costs']))

    def test_payroll_unproved_recharge_is_not_counted_as_known_income(self):
        self.current_month_payroll_plan()
        recharge = RechargeRecord.objects.create(
            account=self.boss_account,
            amount=12345,
            trade_no='M0_READ_UNPROVED_PAYROLL_RECHARGE',
        )
        SalaryActivity.objects.create(
            kind=SalaryActivity.Kind.RECHARGE,
            account=self.staff_account,
            source_key=f'RECHARGE:{recharge.pk}',
            recharge=recharge,
            source_snapshot={},
            performed_at=timezone.now(),
            reason='读取回归：未核实充值不计入已知工资',
            created_by=self.staff_account,
        )
        data = self.read_ok('/api/admin/payroll/preview/', {
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        line = next(row for row in data['lines'] if row['account'] == self.staff_account.pk)
        self.assertFalse(data['can_confirm'])
        self.assertFalse(line['evidence_complete'])
        self.assertIsNone(line['recharge_amount_fen'])
        self.assertIsNone(line['recharge_commission_fen'])
        self.assertIsNone(line['total_fen'])
        self.assertIsNone(data['totals']['total_fen'])

    def test_payroll_unassigned_missing_recharge_keeps_totals_unknown(self):
        self.current_month_payroll_plan()
        activity = self.missing_recharge_activity(self.provider_account, self.staff_account)
        data = self.read_ok('/api/admin/payroll/preview/', {
            'month': timezone.localdate().strftime('%Y-%m'),
        })
        self.assertFalse(data['can_confirm'])
        self.assertIn(activity.pk, data['activity_ids'])
        self.assertIsNone(data['totals']['recharge_commission_fen'])
        self.assertIsNone(data['totals']['total_fen'])
        self.assertTrue(any(str(activity.pk) in warning for warning in data['warnings']))
        self.assertTrue(any(row['amount_fen'] is None for row in data['daily_costs']))

    def test_operating_roi_survives_incomplete_recharge_payroll_evidence(self):
        self.current_month_payroll_plan()
        activity = self.missing_recharge_activity(self.staff_account, self.staff_account)
        today = timezone.localdate()
        data = self.read_ok('/api/admin/operating-roi/', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat(),
        })
        self.assertFalse(data['operating']['complete'])
        self.assertIsNone(data['operating']['salary_cost_fen'])
        self.assertIsNone(data['operating']['total_cost_fen'])
        self.assertIsNone(data['operating']['profit_fen'])
        invalid = data['provisional']['invalid_salary_months']
        self.assertTrue(any(
            str(activity.pk) in ' '.join(item['warnings']) for item in invalid
        ))
        self.assertIsNone(data['provisional']['salary_cost_fen'])
        self.assertIsNone(data['provisional']['total_cost_fen'])

    def test_operating_roi_confirmed_unknown_salary_stays_unknown(self):
        today = timezone.localdate()
        month = today.replace(day=1)
        run = PayrollRun.objects.get(month=month)
        snapshots = {
            'daily_costs': {
                'daily_costs': [
                    {'day': month.isoformat(), 'amount_fen': 1000},
                    {'day': today.isoformat(), 'amount_fen': None},
                ],
                'totals': {'total_fen': 1000},
            },
            'totals': {'totals': {'total_fen': None}},
        }
        for source, snapshot in snapshots.items():
            with self.subTest(snapshot=source):
                run.snapshot = snapshot
                run.save(update_fields=['snapshot'])
                data = self.read_ok('/api/admin/operating-roi/', {
                    'start_date': month.isoformat(), 'end_date': today.isoformat(),
                })
                operating = data['operating']
                provisional = data['provisional']
                self.assertFalse(operating['complete'])
                self.assertEqual(operating['salary_unconfirmed_months'], [])
                self.assertIsNone(operating['salary_cost_fen'])
                self.assertIsNone(operating['total_cost_fen'])
                self.assertIsNone(operating['profit_fen'])
                self.assertIsNone(operating['roi'])
                self.assertEqual(
                    [item['month'] for item in provisional['invalid_salary_months']],
                    [month.strftime('%Y-%m')],
                )
                self.assertFalse(provisional['complete'])
                self.assertIsNone(provisional['salary_cost_fen'])
                self.assertIsNone(provisional['total_cost_fen'])
                self.assertIsNone(provisional['profit_fen'])
                self.assertIsNone(provisional['roi'])
                self.assertTrue(any('薪资' in warning for warning in data['warnings']))

    def test_escort_without_linked_account_keeps_explicit_null_fields(self):
        legacy_provider = make_provider(display_name='未绑定账号陪玩')
        legacy_profile = legacy_provider.escort_profile
        legacy_profile.account = None
        legacy_profile.save(update_fields=['account'])
        data = self.read_ok('/api/admin/escorts/', {
            'page': 1, 'page_size': 10, 'keyword': '', 'status': '',
            'is_verified': '', 'gender': '',
        })
        row = next(row for row in data['list'] if row['id'] == legacy_profile.pk)
        self.assert_fields(row, 'user', 'username', 'phone', 'display_name')
        self.assertIsNone(row['user'])
        self.assertIsNone(row['username'])
        self.assertIsNone(row['phone'])
        self.assertEqual(row['display_name'], '未绑定账号陪玩')

    @override_settings(SEGMENT_SETTLEMENT_ENABLED=True, SEGMENT_SETTLEMENT_ENFORCE_ENABLED=True)
    def test_pending_segment_income_is_unknown_not_zero(self):
        OrderSegmentSettlementManifest.objects.create(
            order=self.order,
            plan={},
            plan_hash='m0-read-unverified',
        )
        order_data = self.read_ok('/api/admin/orders/', {'page': 1, 'page_size': 10})
        order_row = next(row for row in order_data['list'] if row['id'] == self.order.pk)
        self.assertIsNone(order_row['provider_income'])
        self.assertIsNone(order_row['inviter_commission'])
        self.assertIsNone(order_row['shop_income'])

        report_data = self.read_ok('/api/admin/reports/', {'page': 1, 'page_size': 10})
        report_row = next(row for row in report_data['list'] if row['id'] == self.report.pk)
        self.assert_fields(report_row, 'expected_income', 'payout_amount', 'commission_rate')
        self.assertIsNone(report_row['expected_income'])
        self.assertIsNone(report_row['payout_amount'])
        self.assertIsNone(report_row['commission_rate'])


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
)
class AuditedPageEmptyReadTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = make_console_user(ALL_PERMISSIONS)
        cls.staff_account = get_or_create_account_for_legacy_user(cls.staff_user)
        link_legacy_relations(cls.staff_user, cls.staff_account)
        AdminMembership.objects.filter(user=cls.staff_user).update(account=cls.staff_account)

    def setUp(self):
        token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def read_ok(self, url, params=None):
        response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, 200, getattr(response, 'data', None))
        self.assertEqual(response.data.get('code'), 0, response.data)
        return response.data['data']

    def test_empty_lists_are_successful_empty_results(self):
        endpoints = (
            '/api/admin/orders/',
            '/api/admin/reports/',
            '/api/admin/evaluations/',
            '/api/admin/escorts/',
            '/api/admin/service-items/',
            '/api/admin/promotions/',
            '/api/admin/transactions/',
            '/api/admin/salary-activities/',
        )
        for url in endpoints:
            with self.subTest(url=url):
                data = self.read_ok(url, {'page': 1, 'page_size': 10})
                self.assertEqual(data['total'], 0)
                self.assertEqual(data['list'], [])

        reservations = self.read_ok('/api/orders/reservations/', {'page': 1, 'page_size': 10})
        self.assertEqual(reservations['total'], 0)
        self.assertEqual(reservations['items'], [])

    def test_empty_dashboard_and_payroll_keep_zero_distinct_from_unknown(self):
        dashboard = self.read_ok('/api/admin/dashboard/stats')
        self.assertEqual(dashboard['order_total'], 0)
        self.assertEqual(dashboard['order_amount_total'], 0)
        self.assertEqual(dashboard['recent_orders'], [])

        today = timezone.localdate()
        player = self.read_ok('/api/admin/dashboard/player', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat(),
        })
        self.assertEqual(player['rank'], {'amount': [], 'income': [], 'count': []})
        self.assertEqual(player['income_review'], {
            'order_count': 0, 'provider_count': 0, 'unidentified_count': 0,
        })

        month = today.strftime('%Y-%m')
        preview = self.read_ok('/api/admin/payroll/preview/', {'month': month})
        self.assertFalse(preview['can_confirm'])
        self.assertEqual(preview['lines'], [])
        self.assertEqual(preview['totals']['total_fen'], 0)
        self.assertTrue(preview['warnings'])

        config = self.read_ok('/api/admin/payroll/config/')
        self.assertIsNone(config['night_start'])
        self.assertIsNone(config['night_end'])
        self.assertEqual(config['revision'], 0)

        runs = self.read_ok('/api/admin/payroll/runs/', {'month': month})
        self.assertEqual(runs['list'], [])

        roi = self.read_ok('/api/admin/operating-roi/', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.isoformat(),
        })
        self.assertFalse(roi['operating']['complete'])
        self.assertIsNone(roi['operating']['salary_cost_fen'])
        self.assertIsNone(roi['operating']['total_cost_fen'])
        self.assertIsNone(roi['operating']['profit_fen'])
        self.assertTrue(roi['warnings'])

    def test_invalid_read_filters_return_http_400(self):
        today = timezone.localdate()
        requests = (
            ('/api/admin/dashboard/player', {'start_date': 'not-a-date'}),
            ('/api/admin/payroll/preview/', {'month': '2026-13'}),
            ('/api/admin/salary-activities/', {'month': 'bad-month'}),
            ('/api/admin/operating-roi/', {
                'start_date': today.replace(day=1).isoformat(), 'end_date': 'not-a-date',
            }),
        )
        for url, params in requests:
            with self.subTest(url=url):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 400, getattr(response, 'data', None))


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    LICENSE_BENEFIT_ENFORCEMENT_ENABLED=False,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
)
class AuditedPagePermissionReadTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = make_console_user([])
        cls.staff_account = get_or_create_account_for_legacy_user(cls.staff_user)
        link_legacy_relations(cls.staff_user, cls.staff_account)
        AdminMembership.objects.filter(user=cls.staff_user).update(account=cls.staff_account)

        cls.reservation_customer = make_user(nickname='无权限预约老板')
        cls.reservation_provider = make_provider(display_name='无权限预约陪玩')
        cls.reservation_provider_account = get_or_create_account_for_legacy_user(cls.reservation_provider)
        cls.reservation_service = make_service(name='无权限预约服务', price=10000)
        start = timezone.now() + timedelta(days=1)
        cls.reservation = Reservation.objects.create(
            customer=cls.reservation_customer,
            provider=cls.reservation_provider,
            provider_account=cls.reservation_provider_account,
            service=cls.reservation_service,
            start_time=start,
            end_time=start + timedelta(hours=1),
            duration_minutes=60,
            estimated_amount=10000,
            status=Reservation.Status.PENDING,
        )

    def setUp(self):
        token, _ = issue_account_tokens(self.staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_console_reads_without_permission_return_http_403(self):
        endpoints = (
            '/api/admin/dashboard/stats',
            '/api/admin/dashboard/player',
            '/api/admin/orders/',
            '/api/admin/reports/',
            '/api/admin/evaluations/',
            '/api/admin/escorts/',
            '/api/admin/service-items/',
            '/api/admin/promotions/',
            '/api/admin/transactions/',
            '/api/admin/salary-activities/',
            '/api/admin/payroll/preview/',
            '/api/admin/payroll/config/',
            '/api/admin/payroll/runs/',
            '/api/admin/operating-roi/',
        )
        for url in endpoints:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403, getattr(response, 'data', None))

    def test_reservation_read_without_permission_requires_http_403(self):
        response = self.client.get('/api/orders/reservations/', {'page': 1, 'page_size': 10})
        self.assertEqual(response.status_code, 403, getattr(response, 'data', None))
        self.assertEqual(response.data.get('code'), 403)

        response = self.client.get(f'/api/orders/reservations/{self.reservation.pk}/')
        self.assertEqual(response.status_code, 403, getattr(response, 'data', None))
        self.assertEqual(response.data.get('code'), 403)
