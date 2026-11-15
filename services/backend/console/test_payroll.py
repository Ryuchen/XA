"""Real MySQL/OTP API checks for payroll, attribution and operating ROI."""
from datetime import datetime, time, timedelta, timezone as datetime_timezone

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.models import ClubAccount, OtpCredential
from club_accounts.otp import encrypt_secret, hotp
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminMembership
from console.payroll import aftersale_cycles
from console.payroll_calculation import calculate_payroll, operating_roi
from console.payroll_models import (OperatingCost, PayrollConfig, PayrollRun, SalaryActivity,
                                   SalaryAssignment, SalaryPlan)
from orders.models import (AfterSale, DirectPaymentIntent, Order, OrderPlatformSubsidy,
                          OrderPlatformSubsidyShare, OrderProvider, OrderRefund, OrderStatusLog)
from orders.tests.factories import make_console_user, make_order, make_provider, make_user, make_wallet
from wallet.models import RechargeRecord, Transaction, get_platform_wallet


def moment(day=15, hour=12, month=10):
    return timezone.make_aware(datetime(2026, month, day, hour), timezone.get_current_timezone())


@override_settings(LOGIN_OTP_REQUIRED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PayrollWorkflowTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        from console.test_operating_workflow import seed_payroll_fixture
        seed_payroll_fixture()
        cls.editor_account, cls.editor_token = cls.create_staff([
            'salary:view', 'salary:edit', 'salary:confirm', 'cost:view', 'cost:edit', 'roi:view'])
        cls.worker_user = make_console_user([])
        cls.worker = get_or_create_account_for_legacy_user(cls.worker_user)
        AdminMembership.objects.filter(user=cls.worker_user).update(account=cls.worker)
        cls.boss_user = make_user()
        cls.boss = get_or_create_account_for_legacy_user(cls.boss_user)
        cls.provider_user = make_provider()
        cls.provider = get_or_create_account_for_legacy_user(cls.provider_user)
        link_legacy_relations(cls.provider_user, cls.provider)

    @classmethod
    def create_staff(cls, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        AdminMembership.objects.filter(user=user).update(account=account)
        otp_secret = 'JBSWY3DPEHPK3PXP'
        OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(otp_secret))
        client = APIClient()
        first = client.post('/api/admin/auth/login', {'username': account.username, 'password': 'pass1234'}, format='json')
        assert first.data.get('code') == 0, first.data
        gate = first.data['data']
        assert gate['requiresOTP'] and not gate['requiresOTPSetup'], first.data
        signed = client.post('/api/admin/auth/login', {'username': account.username, 'password': 'pass1234',
            'otpChallenge': gate['otpChallenge'],
            'otpCode': hotp(otp_secret, int(timezone.now().timestamp()) // 30)}, format='json')
        assert signed.data['code'] == 0, signed.data
        return account, signed.data['data']['token']

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + self.editor_token)

    def ok(self, response, statuses=(200, 201)):
        self.assertIn(response.status_code, statuses, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def plan(self, code='CUSTOMER_SERVICE_FULLTIME', **values):
        if not values:
            return SalaryPlan.objects.get(position__code=code)
        return SalaryPlan.objects.create(position=BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code=code),
                                         name='隔离测试薪资', **values)

    def assign(self, plan=None, month='2026-10', account=None):
        return self.ok(self.client.post('/api/admin/salary-assignments/', {
            'month': month, 'account': (account or self.worker).pk, 'plan': (plan or self.plan()).pk}, format='json'))

    def dispatch_order(self, amount=10000, day=15, hour=12, **kwargs):
        order = make_order(self.boss_user, provider=self.provider_user, amount=amount,
                           customer_account=self.boss, provider_account=self.provider,
                           dispatch_route='STAFF', provider_income=amount * 80 // 100, **kwargs)
        Order.objects.filter(pk=order.pk).update(created_at=moment(day, hour))
        log = OrderStatusLog.objects.create(order=order, action='CREATE', operator_account=self.editor_account)
        OrderStatusLog.objects.filter(pk=log.pk).update(created_at=moment(day, hour))
        order.refresh_from_db()
        return order

    def record_roi_payment(self, order, at=None):
        """Give the reporting fixture an explicit payment and source receipt."""
        from engagement.models import OrderFundingAllocation, PointFundingLot
        from wallet.services import get_wallet
        wallet = get_wallet(user=self.boss_user, account=self.boss)
        wallet.balance = 0
        wallet.save(update_fields=['balance'])
        payment = Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-order.amount,
                                             balance_before=order.amount, balance_after=0)
        Transaction.objects.filter(pk=payment.pk).update(created_at=at or moment())
        lot = PointFundingLot.objects.create(account=self.boss, source_key=f'payroll-roi:{order.pk}',
                                             kind='PREPAID', remaining=0)
        OrderFundingAllocation.objects.create(order=order, lot=lot, kind='PREPAID', amount=order.amount)
        if order.provider_account_id and not order.providers.exists():
            OrderProvider.objects.create(order=order, provider=order.provider, provider_account=order.provider_account,
                settlement_base=order.amount, provider_income=order.provider_income,
                commission_rate=(order.amount - order.provider_income) * 100 // order.amount)
        if order.inviter_commission:
            rate = order.inviter_commission * 100 // order.amount
            Order.objects.filter(pk=order.pk).update(inviter_commission_rate_snapshot=rate)
            order.inviter_commission_rate_snapshot = rate

    def record_roi_refund(self, order, amount):
        from wallet.services import get_wallet
        wallet = get_wallet(user=self.boss_user, account=self.boss)
        refund = OrderRefund.objects.create(order=order, amount=amount, reason='隔离测试退款',
                                            operator_account=self.editor_account, created_at=moment())
        before = wallet.balance
        wallet.balance += amount
        wallet.save(update_fields=['balance'])
        credit = Transaction.objects.create(wallet=wallet, order=order, tx_type='REFUND', amount=amount,
            balance_before=before, balance_after=wallet.balance, operator_account=self.editor_account)
        Transaction.objects.filter(pk=credit.pk).update(created_at=moment())
        order.funding_allocations.update(refunded_amount=amount)
        return refund

    def activity(self, kind='DISPATCH', source=None, **values):
        payload = {'kind': kind, 'account': self.worker.pk, 'reason': '隔离测试人工核实归属', **values}
        if source:
            payload['source_id'] = source.pk
        return self.client.post('/api/admin/salary-activities/', payload, format='json')

    def preview(self):
        return self.ok(self.client.get('/api/admin/payroll/preview/', {'month': '2026-10'}))

    def cost(self, amount=1000, day='2026-10-15', key='cost-1', **values):
        return self.client.post('/api/admin/operating-costs/', {
            'day': day, 'category': BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='AD_SPEND').pk,
            'channel': BusinessDictionaryItem.objects.get(type='AD_CHANNEL', code='UNKNOWN').pk,
            'amount_fen': amount, 'note': '隔离测试成本，不是实际投放', 'client_request_id': key, **values}, format='json')

    def roi(self):
        return self.ok(self.client.get('/api/admin/operating-roi/', {'start_date': '2026-10-15', 'end_date': '2026-10-15'}))

    def test_reference_plans_are_per_person_and_exam_fee_is_net(self):
        self.assertEqual(SalaryPlan.objects.count(), 9)
        full = self.plan()
        self.assertEqual((full.base_fen, full.performance_fen, full.target_fen), (120000, 80000, 20000000))
        self.assertEqual((full.dispatch_day_bps, full.dispatch_night_bps, full.recharge_bps), (100, 150, 100))
        manager = self.plan('STORE_MANAGER')
        self.assertEqual((manager.base_fen, manager.performance_fen, manager.target_fen), (300000, 160000, 60000000))
        self.assertEqual(SalaryAssignment.objects.count(), 0)
        self.assertEqual(OperatingCost.objects.count(), 0)
        examiner = self.plan('EXAMINER_SHENGLANG')
        self.assertEqual((examiner.exam_fee_fen, examiner.exam_commission_bps), (5000, 3000))

    def test_assignment_snapshots_terms_and_rejects_duplicate_employee(self):
        assigned = self.assign()
        self.assertEqual(assigned['plan_snapshot']['base_fen'], 120000)
        duplicate = self.client.post('/api/admin/salary-assignments/', {'month': '2026-10', 'account': self.worker.pk,
                                   'plan': self.plan().pk}, format='json')
        self.assertEqual(duplicate.status_code, 409)
        full = self.plan()
        changed = self.ok(self.client.patch(f'/api/admin/salary-plans/{full.pk}/',
            {'base_fen': 999999, 'revision': full.revision}, format='json'))
        self.assertEqual(changed['base_fen'], 999999)
        self.assertEqual(self.preview()['totals']['base_fen'], 120000)
        stale = self.client.patch(f'/api/admin/salary-plans/{full.pk}/', {'base_fen': 1, 'revision': 1}, format='json')
        self.assertEqual(stale.status_code, 409)

    def test_same_plan_save_keeps_terms_until_explicit_current_revision_adoption(self):
        assigned = self.assign()
        plan = self.plan()
        self.ok(self.client.patch(f'/api/admin/salary-plans/{plan.pk}/', {'base_fen': 150000, 'revision': 1}, format='json'))
        same = self.ok(self.client.patch(f'/api/admin/salary-assignments/{assigned["id"]}/',
            {'plan': plan.pk, 'revision': 1}, format='json'))
        self.assertEqual(same['plan_snapshot']['base_fen'], 120000)
        stale = self.client.patch(f'/api/admin/salary-assignments/{assigned["id"]}/',
            {'plan': plan.pk, 'revision': 2, 'adopt_current_plan': True, 'expected_plan_revision': 1}, format='json')
        self.assertEqual(stale.status_code, 409)
        adopted = self.ok(self.client.patch(f'/api/admin/salary-assignments/{assigned["id"]}/',
            {'plan': plan.pk, 'revision': 2, 'adopt_current_plan': True, 'expected_plan_revision': 2}, format='json'))
        self.assertEqual(adopted['plan_snapshot']['base_fen'], 150000)
        self.assertEqual(self.client.delete(f'/api/admin/salary-assignments/{assigned["id"]}/', {'revision': 2}, format='json').status_code, 409)
        self.ok(self.client.delete(f'/api/admin/salary-assignments/{assigned["id"]}/', {'revision': 3}, format='json'))

    def test_new_assignment_checks_displayed_template_revision(self):
        plan = self.plan()
        SalaryPlan.objects.filter(pk=plan.pk).update(revision=2)
        response = self.client.post('/api/admin/salary-assignments/', {'month': '2026-10', 'account': self.worker.pk,
            'plan': plan.pk, 'expected_plan_revision': 1}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertFalse(SalaryAssignment.objects.exists())

    def test_inactive_plan_or_position_is_not_selectable_but_history_is_visible(self):
        assigned = self.assign()
        plan = self.plan()
        SalaryPlan.objects.filter(pk=plan.pk).update(is_active=False)
        visible = self.ok(self.client.get('/api/admin/salary-plans/', {'is_active': 'true', 'page_size': 100}))
        self.assertNotIn(plan.pk, [row['id'] for row in visible['list']])
        self.assertEqual(self.client.get(f'/api/admin/salary-plans/{plan.pk}/').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/salary-plans/', {'is_active': 'broken'}).status_code, 400)
        SalaryPlan.objects.filter(pk=plan.pk).update(is_active=True)
        BusinessDictionaryItem.objects.filter(pk=plan.position_id).update(is_active=False)
        visible = self.ok(self.client.get('/api/admin/salary-plans/', {'is_active': 'true', 'page_size': 100}))
        self.assertNotIn(plan.pk, [row['id'] for row in visible['list']])
        self.assertEqual(self.client.post('/api/admin/salary-assignments/', {'month': '2026-11',
            'account': self.worker.pk, 'plan': plan.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.get(f'/api/admin/salary-assignments/{assigned["id"]}/').status_code, 200)

    def test_night_is_unconfigured_until_explicitly_set_and_real_event_time_is_used(self):
        self.assign()
        order = self.dispatch_order(hour=23)
        self.ok(self.activity(source=order))
        preview = self.preview()
        self.assertFalse(preview['can_confirm'])
        self.assertIn('夜班', ' '.join(preview['warnings']))
        config = self.ok(self.client.put('/api/admin/payroll/config/', {'night_start': '22:00', 'night_end': '08:00', 'revision': 0}, format='json'))
        self.assertEqual(config['night_start'], '22:00')
        preview = self.preview()
        self.assertTrue(preview['can_confirm'])
        self.assertEqual(preview['lines'][0]['dispatch_night_amount_fen'], 10000)
        self.assertEqual(preview['lines'][0]['dispatch_commission_fen'], 150)
        forged = self.activity(source=self.dispatch_order(day=16), performed_at=moment(hour=23).isoformat())
        self.assertEqual(forged.status_code, 400)

    def test_night_end_is_exclusive_and_performance_is_capped(self):
        plan = self.plan(dispatch_day_bps=100, dispatch_night_bps=150, performance_fen=800, target_fen=10000)
        self.assign(plan)
        self.ok(self.client.put('/api/admin/payroll/config/', {'night_start': '22:00', 'night_end': '08:00', 'revision': 0}, format='json'))
        self.ok(self.activity(source=self.dispatch_order(amount=10000, hour=8)))
        self.ok(self.activity(source=self.dispatch_order(amount=10000, day=16, hour=22)))
        line = self.preview()['lines'][0]
        self.assertEqual((line['dispatch_day_amount_fen'], line['dispatch_night_amount_fen']), (10000, 10000))
        self.assertEqual((line['performance_fen'], line['dispatch_commission_fen']), (800, 250))

    def test_recharge_is_explicitly_owned_and_gift_credit_is_excluded(self):
        self.assign(self.plan(recharge_bps=100))
        wallet = make_wallet(self.boss_user, 12000)
        tx = Transaction.objects.create(wallet=wallet, amount=10000, tx_type='TOPUP',
                                        balance_before=0, balance_after=10000)
        record = RechargeRecord.objects.create(user=self.boss_user, account=self.boss, amount=10000, gift_amount=2000,
            operator_account=self.editor_account, recharge_tx=tx)
        RechargeRecord.objects.filter(pk=record.pk).update(created_at=moment())
        saved = self.ok(self.activity('RECHARGE', record))
        self.assertEqual(saved['account'], self.worker.pk)
        self.assertNotEqual(saved['account'], self.editor_account.pk)
        line = self.preview()['lines'][0]
        self.assertEqual((line['recharge_amount_fen'], line['recharge_commission_fen']), (10000, 100))
        self.assertEqual(self.activity('RECHARGE', record).status_code, 409)

    def test_unattributed_dispatch_prevents_unknown_commission_becoming_zero(self):
        self.assign(self.plan(dispatch_day_bps=100, dispatch_night_bps=100))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        self.record_roi_payment(order)
        self.ok(self.cost(0))
        preview = self.preview()
        self.assertFalse(preview['can_confirm'])
        self.assertEqual(preview['unattributed']['dispatch']['count'], 1)
        self.assertFalse(self.roi()['provisional']['complete'])
        self.ok(self.activity(source=order))
        self.assertTrue(self.preview()['can_confirm'])
        self.assertTrue(self.roi()['provisional']['complete'])

    def test_unattributed_boss_recharge_is_pending_but_provider_topup_is_not_sales(self):
        self.assign(self.plan(recharge_bps=100))
        for user, account in ((self.boss_user, self.boss), (self.provider_user, self.provider)):
            wallet = make_wallet(user, 10000)
            tx = Transaction.objects.create(wallet=wallet, amount=10000, tx_type='TOPUP',
                                            balance_before=0, balance_after=10000)
            row = RechargeRecord.objects.create(user=user, account=account, amount=10000,
                operator_account=self.editor_account, recharge_tx=tx)
            RechargeRecord.objects.filter(pk=row.pk).update(created_at=moment())
        preview = self.preview()
        self.assertEqual(preview['unattributed']['recharge']['count'], 1)
        self.assertFalse(preview['can_confirm'])
        source = self.ok(self.client.get('/api/admin/payroll/sources/', {'kind': 'RECHARGE', 'source_id': row.pk}))
        self.assertEqual(source['total'], 0)
        self.assertEqual(self.activity('RECHARGE', row).status_code, 404)
        boss_source = RechargeRecord.objects.get(account=self.boss)
        self.ok(self.activity('RECHARGE', boss_source))
        self.assertTrue(self.preview()['can_confirm'])

    def test_unattributed_completed_aftersale_cycle_requires_explicit_salary_owner(self):
        self.assign(self.plan(aftersale_fee_fen=5000))
        order = self.dispatch_order()
        row = AfterSale.objects.create(order=order, account=self.boss, reason='待归属的真实售后周期',
            status='RESOLVED', operator=self.editor_account, resolved_at=moment())
        preview = self.preview()
        self.assertFalse(preview['can_confirm'])
        self.assertEqual(preview['unattributed']['aftersale']['count'], 1)
        self.ok(self.activity('AFTERSALE', row, cycle_key=aftersale_cycles(row)[0]['cycle_key']))
        self.assertTrue(self.preview()['can_confirm'])

    def test_aftersale_cycle_key_and_dedup_are_stable_across_equivalent_offsets(self):
        order = self.dispatch_order()
        row = AfterSale.objects.create(order=order, account=self.boss, reason='时区等价售后',
            status='RESOLVED', operator=self.editor_account, resolved_at=moment())
        row.created_at = moment(day=14)
        row.history = [{'status': 'RESOLVED', 'operator_id': self.editor_account.pk, 'client_request_id': '',
            'created_at': row.created_at.astimezone(datetime_timezone.utc).isoformat(),
            'resolved_at': row.resolved_at.astimezone(datetime_timezone.utc).isoformat()}]
        row.save(update_fields=['created_at', 'history'])
        memory_cycles = aftersale_cycles(row)
        self.assertEqual(len(memory_cycles), 1)
        row.refresh_from_db()
        database_cycles = aftersale_cycles(row)
        self.assertEqual(memory_cycles, database_cycles)
        self.assertEqual(len(database_cycles), 1)
        self.assign(self.plan(aftersale_fee_fen=5000))
        self.ok(self.activity('AFTERSALE', row, cycle_key=memory_cycles[0]['cycle_key']))
        self.assertEqual(self.preview()['lines'][0]['aftersale_commission_fen'], 5000)

    def test_recharge_adjustment_or_unmatched_receipt_is_not_a_commission_source(self):
        self.assign(self.plan(recharge_bps=100))
        wallet = make_wallet(self.boss_user, 10000)
        tx = Transaction.objects.create(wallet=wallet, amount=10000, tx_type='ADJUST')
        row = RechargeRecord.objects.create(user=self.boss_user, account=self.boss, amount=10000,
            operator_account=self.editor_account, recharge_tx=tx)
        RechargeRecord.objects.filter(pk=row.pk).update(created_at=moment())
        self.assertEqual(self.activity('RECHARGE', row).status_code, 404)
        self.assertEqual(self.preview()['unattributed']['recharge']['count'], 0)
        Transaction.objects.filter(pk=tx.pk).update(tx_type='TOPUP', amount=10001)
        self.assertEqual(self.activity('RECHARGE', row).status_code, 404)

    def test_aftersale_completed_cycles_deduplicate_current_and_history(self):
        self.assign(self.plan(aftersale_fee_fen=5000))
        order = self.dispatch_order()
        case = AfterSale.objects.create(order=order, account=self.boss, reason='隔离测试售后', status='RESOLVED',
                                       operator=self.editor_account, resolved_at=moment())
        AfterSale.objects.filter(pk=case.pk).update(created_at=moment(day=14))
        case.refresh_from_db()
        cycle = aftersale_cycles(case)[0]
        case.history = [dict(cycle, created_at=case.created_at.isoformat(), client_request_id=''),
                        dict(cycle, created_at=case.created_at.isoformat(), client_request_id='')]
        case.save()
        cycles = aftersale_cycles(case)
        self.assertEqual(len(cycles), 1)
        self.ok(self.activity('AFTERSALE', case, cycle_key=cycles[0]['cycle_key']))
        self.assertEqual(self.activity('AFTERSALE', case, cycle_key=cycles[0]['cycle_key']).status_code, 409)
        self.assertEqual(self.preview()['lines'][0]['aftersale_commission_fen'], 5000)
        self.assertEqual(self.activity('AFTERSALE', case, cycle_key='invented').status_code, 400)

    def test_exam_is_verified_count_with_net35_and_request_replay(self):
        self.assign(self.plan('EXAMINER_SHENGLANG'))
        payload = {'performed_at': moment().isoformat(), 'client_request_id': 'exam-verified-1'}
        first = self.ok(self.activity('EXAM', **payload))
        replay = self.ok(self.activity('EXAM', **payload))
        self.assertEqual(first['id'], replay['id'])
        line = self.preview()['lines'][0]
        self.assertEqual((line['exam_count'], line['exam_gross_fen'], line['exam_deduction_fen'], line['exam_commission_fen']), (1, 5000, 1500, 3500))
        self.assertEqual(self.activity('EXAM', performed_at=moment(day=16).isoformat(), client_request_id='exam-verified-1').status_code, 409)
        self.assertEqual(self.activity('EXAM', performed_at=(timezone.now() + timedelta(days=1)).isoformat(), client_request_id='future').status_code, 400)

    def test_confirm_freezes_salary_and_attribution_and_replay_is_once(self):
        assigned = self.assign(self.plan(base_fen=3100, dispatch_day_bps=100, dispatch_night_bps=100))
        event = self.ok(self.activity(source=self.dispatch_order()))
        preview = self.preview()
        confirmed = self.ok(self.client.post('/api/admin/payroll/confirm/', {'month': '2026-10', 'fingerprint': preview['fingerprint']}, format='json'))
        self.assertEqual(confirmed['status'], 'CONFIRMED')
        replay = self.ok(self.client.post('/api/admin/payroll/confirm/', {'month': '2026-10', 'fingerprint': preview['fingerprint']}, format='json'))
        self.assertEqual(confirmed['run_id'], replay['run_id'])
        self.assertEqual(PayrollRun.objects.count(), 1)
        self.assertEqual(self.client.delete(f'/api/admin/salary-activities/{event["id"]}/').status_code, 409)
        self.assertEqual(self.client.patch(f'/api/admin/salary-assignments/{assigned["id"]}/', {'revision': 1}, format='json').status_code, 409)
        self.assertEqual(self.activity(source=self.dispatch_order(day=16)).status_code, 409)
        SalaryPlan.objects.filter(pk=assigned['plan']).update(base_fen=9999999)
        self.assertEqual(self.preview()['totals']['base_fen'], 3100)

    def test_refund_after_preview_requires_recalculation_before_confirm(self):
        self.assign(self.plan(dispatch_day_bps=100, dispatch_night_bps=100))
        order = self.dispatch_order()
        self.ok(self.activity(source=order))
        preview = self.preview()
        OrderRefund.objects.create(order=order, amount=2000, reason='测试退款', operator_account=self.editor_account)
        Order.objects.filter(pk=order.pk).update(payment_status='PARTIALLY_REFUNDED')
        response = self.client.post('/api/admin/payroll/confirm/', {'month': '2026-10', 'fingerprint': preview['fingerprint']}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.preview()['lines'][0]['dispatch_commission_fen'], 80)

    def test_cost_zero_idempotency_revision_and_voided_replay(self):
        created = self.ok(self.cost(0))
        self.assertEqual(created['amount_fen'], 0)
        self.assertEqual(self.ok(self.cost(0))['id'], created['id'])
        self.assertEqual(self.cost(1).status_code, 409)
        changed = self.ok(self.client.patch(f'/api/admin/operating-costs/{created["id"]}/', {'amount_fen': 200, 'revision': 1}, format='json'))
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(self.client.delete(f'/api/admin/operating-costs/{created["id"]}/', {'revision': 1}, format='json').status_code, 409)
        self.ok(self.client.delete(f'/api/admin/operating-costs/{created["id"]}/', {'revision': 2}, format='json'))
        self.assertEqual(self.cost(0).status_code, 409)
        self.assertTrue(OperatingCost.objects.get(pk=created['id']).is_void)

    def test_activity_cannot_be_mutated_and_void_preserves_retry_protection(self):
        self.assign(self.plan())
        order = self.dispatch_order()
        event = self.ok(self.activity(source=order, client_request_id='dispatch-proof-1'))
        for method in ('put', 'patch'):
            response = getattr(self.client, method)(f'/api/admin/salary-activities/{event["id"]}/', {'account': self.editor_account.pk}, format='json')
            self.assertEqual(response.status_code, 405)
        self.ok(self.client.delete(f'/api/admin/salary-activities/{event["id"]}/'))
        self.assertEqual(self.activity(source=order, client_request_id='dispatch-proof-1').status_code, 409)
        self.ok(self.activity(source=order, client_request_id='dispatch-proof-corrected'))

    def test_read_only_salary_staff_cannot_write_and_boss_cannot_read(self):
        account, token = self.create_staff(['salary:view', 'cost:view', 'roi:view'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(self.client.get('/api/admin/salary-plans/').status_code, 200)
        self.assertEqual(self.activity('EXAM', performed_at=moment().isoformat(), client_request_id='denied').status_code, 403)
        self.assertEqual(self.cost().status_code, 403)
        self.assertEqual(self.client.post('/api/admin/payroll/confirm/', {}, format='json').status_code, 403)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss)[0])
        for path in ('salary-plans/', 'payroll/employees/', 'operating-costs/', 'operating-roi/?start_date=2026-10-15&end_date=2026-10-15'):
            self.assertEqual(self.client.get('/api/admin/' + path).status_code, 403)

    def test_employee_options_reach_after_hundred_without_repeating_page(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        users = User.objects.bulk_create([User(username=f'payroll-paginated-{i}', role='OPERATOR', boss_no=f'PAYROLL{i}') for i in range(105)])
        accounts = ClubAccount.objects.bulk_create([ClubAccount(username=f'payroll-paginated-{i}', account_type='STAFF',
            account_no=f'PAYROLL-PAGE-{i}') for i in range(105)])
        users = list(User.objects.filter(username__startswith='payroll-paginated-').order_by('username'))
        accounts = list(ClubAccount.objects.filter(username__startswith='payroll-paginated-').order_by('username'))
        AdminMembership.objects.bulk_create([AdminMembership(user=user, account=account) for user, account in zip(users, accounts)])
        first = self.ok(self.client.get('/api/admin/payroll/employees/', {'page_size': 100}))
        second = self.ok(self.client.get('/api/admin/payroll/employees/', {'page_size': 100, 'page': 2}))
        self.assertEqual(first['total'], 107)
        self.assertEqual(len(first['list']), 100)
        self.assertEqual(len(second['list']), 7)
        self.assertFalse({row['id'] for row in first['list']} & {row['id'] for row in second['list']})

    def test_roi_keeps_payment_and_completion_in_separate_months(self):
        self.assign(self.plan(base_fen=3100))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        Order.objects.filter(pk=order.pk).update(created_at=moment(month=9))
        self.record_roi_payment(order, at=moment(month=9))
        self.ok(self.cost(1000))
        preview = self.preview()
        self.ok(self.client.post('/api/admin/payroll/confirm/', {'month': '2026-10', 'fingerprint': preview['fingerprint']}, format='json'))
        roi = self.roi()
        self.assertEqual(roi['roas']['net_consumption_fen'], 0)
        current = roi['operating']
        self.assertEqual((current['net_consumption_fen'], current['provider_cost_fen'], current['salary_cost_fen']), (0, 0, 100))
        self.assertTrue(current['complete'])
        self.assertEqual((current['total_cost_fen'], current['profit_fen']), (1100, -1100))
        previous = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': '2026-09-15', 'end_date': '2026-09-15'}))['operating']
        self.assertEqual((previous['net_consumption_fen'], previous['provider_cost_fen']), (10000, 8000))
        self.assertIsNone(order.settled_at)

    def test_roi_has_current_provisional_salary_without_ignoring_commission(self):
        self.assign(self.plan(base_fen=3100, dispatch_day_bps=100, dispatch_night_bps=100))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        self.record_roi_payment(order)
        self.ok(self.activity(source=order))
        self.ok(self.cost(1000))
        roi = self.roi()
        self.assertIsNone(roi['operating']['roi'])
        self.assertTrue(roi['provisional']['complete'])
        self.assertTrue(roi['provisional']['estimated'])
        self.assertEqual(roi['provisional']['salary_cost_fen'], 200)
        self.assertAlmostEqual(roi['provisional']['roi'], 800 / 9200)

    def test_roi_missing_payroll_or_ad_cost_is_unknown_but_zero_is_explicit(self):
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        self.record_roi_payment(order)
        roi = self.roi()
        self.assertFalse(roi['provisional']['complete'])
        self.assertIsNone(roi['operating']['salary_cost_fen'])
        self.assertIsNone(roi['provisional']['salary_cost_fen'])
        self.assertEqual(roi['roas']['missing_ad_dates'], ['2026-10-15'])
        self.assign(self.plan(base_fen=0))
        self.ok(self.cost(0))
        roi = self.roi()
        self.assertEqual(roi['roas']['missing_ad_dates'], [])
        self.assertIsNone(roi['roas']['value'])
        self.assertTrue(roi['provisional']['complete'])
        self.assertAlmostEqual(roi['provisional']['roi'], .25)

    def test_roi_salary_cross_month_unknown_estimated_and_confirmed(self):
        params = {'start_date': '2026-10-31', 'end_date': '2026-11-01'}
        result = self.ok(self.client.get('/api/admin/operating-roi/', params))
        self.assertIsNone(result['operating']['salary_cost_fen'])
        self.assertIsNone(result['provisional']['salary_cost_fen'])
        self.assign(self.plan(base_fen=3100), month='2026-10')
        preview = self.preview()
        self.ok(self.client.post('/api/admin/payroll/confirm/', {
            'month': '2026-10', 'fingerprint': preview['fingerprint']}, format='json'))
        result = self.ok(self.client.get('/api/admin/operating-roi/', params))
        self.assertEqual(result['operating']['salary_unconfirmed_months'], ['2026-11'])
        self.assertIsNone(result['operating']['salary_cost_fen'])
        self.assertIsNone(result['provisional']['salary_cost_fen'])
        self.assertIsNone(result['operating']['roi'])
        self.assertIsNone(result['provisional']['roi'])
        self.assign(self.plan(base_fen=3000), month='2026-11')
        result = self.ok(self.client.get('/api/admin/operating-roi/', params))
        self.assertIsNone(result['operating']['salary_cost_fen'])
        self.assertEqual(result['provisional']['salary_cost_fen'], 200)
        preview = self.ok(self.client.get('/api/admin/payroll/preview/', {'month': '2026-11'}))
        self.ok(self.client.post('/api/admin/payroll/confirm/', {
            'month': '2026-11', 'fingerprint': preview['fingerprint']}, format='json'))
        result = self.ok(self.client.get('/api/admin/operating-roi/', params))
        self.assertEqual(result['operating']['salary_cost_fen'], 200)
        self.assertEqual(result['provisional']['salary_cost_fen'], 200)
        self.assertFalse(result['operating']['salary_unconfirmed_months'])

    def test_partial_refund_without_supported_agreement_requires_review(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment(), payment_status='PARTIALLY_REFUNDED')
        self.record_roi_payment(order)
        self.record_roi_refund(order, 2000)
        wallet = make_wallet(self.provider_user, 6400)
        original = Transaction.objects.create(wallet=wallet, order=order, amount=8000, tx_type='INCOME')
        Transaction.objects.create(wallet=wallet, order=order, amount=-1600, tx_type='INCOME', reversal_of=original)
        self.ok(self.cost(0))
        roi = self.roi()['provisional']
        self.assertEqual((roi['net_consumption_fen'], roi['provider_cost_fen']), (8000, None))
        self.assertFalse(roi['complete'])

    def test_unproved_partial_refund_cost_blocks_false_profitable_roi(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment(), payment_status='PARTIALLY_REFUNDED')
        OrderRefund.objects.create(order=order, amount=2000, reason='待核退款', operator_account=self.editor_account)
        self.ok(self.cost(0))
        roi = self.roi()
        self.assertEqual(roi['operating']['unknown_cost_order_ids'], [order.pk])
        self.assertIsNone(roi['provisional']['roi'])
        self.assertFalse(roi['provisional']['complete'])

    def test_roi_referral_reversal_requires_review(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment(),
                                    inviter=self.provider_user, inviter_account=self.provider, inviter_commission=1000)
        self.record_roi_payment(order)
        self.ok(self.cost(0))
        roi = self.roi()['provisional']
        self.assertEqual(roi['referral_cost_fen'], 1000)
        self.assertEqual(roi['total_cost_fen'], 9000)
        self.assertAlmostEqual(roi['roi'], 1000 / 9000)
        Order.objects.filter(pk=order.pk).update(payment_status='PARTIALLY_REFUNDED')
        self.record_roi_refund(order, 2000)
        wallet = make_wallet(self.provider_user, 7200)
        pay = Transaction.objects.create(wallet=wallet, order=order, amount=8000, tx_type='INCOME', remark='订单收入：测试')
        ref = Transaction.objects.create(wallet=wallet, order=order, amount=1000, tx_type='INCOME', remark='推荐分佣：测试')
        Transaction.objects.create(wallet=wallet, order=order, amount=-1600, tx_type='INCOME', reversal_of=pay)
        Transaction.objects.create(wallet=wallet, order=order, amount=-200, tx_type='INCOME', reversal_of=ref)
        roi = self.roi()['provisional']
        self.assertFalse(roi['complete'])
        self.assertIsNone(roi['referral_cost_fen'])
        Transaction.objects.filter(pk=ref.pk).update(remark='缺少推荐标签的历史数据')
        roi = self.roi()
        self.assertIsNone(roi['operating']['referral_cost_fen'])
        self.assertFalse(roi['provisional']['complete'])

    def test_fully_refunded_order_keeps_verified_unrecovered_referral_as_cost(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='CANCELLED', completed_at=moment(), settled_at=moment(),
            payment_status='REFUNDED', inviter=self.provider_user, inviter_account=self.provider, inviter_commission=1000)
        self.record_roi_payment(order)
        self.record_roi_refund(order, 10000)
        wallet = make_wallet(self.provider_user, 1000)
        income = Transaction.objects.create(wallet=wallet, order=order, amount=8000, tx_type='INCOME', remark='订单收入：测试')
        Transaction.objects.create(wallet=wallet, order=order, amount=-8000, tx_type='INCOME', reversal_of=income)
        referral = Transaction.objects.create(wallet=wallet, order=order, amount=1000, tx_type='INCOME', remark='推荐分佣：测试')
        self.ok(self.cost(0))
        result = self.roi()['provisional']
        from console.payroll_calculation import refunded_referral_cost
        self.assertFalse(result['complete'])
        self.assertIsNone(result['referral_cost_fen'])
        self.assertEqual(refunded_referral_cost(Order.objects.get(pk=order.pk)), 1000)
        Transaction.objects.filter(pk=referral.pk).update(remark='推荐来源不明')
        uncertain = self.roi()
        self.assertIsNone(uncertain['operating']['referral_cost_fen'])
        self.assertIn(order.pk, uncertain['operating']['unknown_referral_order_ids'])
        self.assertFalse(uncertain['provisional']['complete'])

    def test_platform_subsidy_is_a_cost_even_when_order_consumption_is_zero(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(amount=0, status='COMPLETED', completed_at=moment())
        assignment = OrderProvider.objects.create(order=order, provider=self.provider_user,
            provider_account=self.provider, settlement_base=0, provider_income=0)
        funded = OrderPlatformSubsidy.objects.create(order=order, basis=10000, coupon_id_snapshot=1)
        OrderPlatformSubsidyShare.objects.create(subsidy=funded, assignment=assignment, amount=8000)
        self.ok(self.cost(0))
        roi = self.roi()['provisional']
        self.assertEqual((roi['provider_cost_fen'], roi['subsidy_cost_fen'], roi['total_cost_fen']), (0, 8000, 8000))
        self.assertEqual(roi['roi'], -1)

    def test_simulation_direct_payment_order_is_excluded_from_sources_and_roi(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        DirectPaymentIntent.objects.create(order=order, account=self.boss, client_request_id='sim-only',
            request_fingerprint='0' * 64, merchant_order_no='PAYROLL_SIM_ORDER', appid='sim-app', mchid='sim-merchant',
            openid='sim-openid', amount_fen=10000)
        self.ok(self.cost(0))
        roi = self.roi()
        self.assertEqual(roi['roas']['net_consumption_fen'], 0)
        self.assertEqual(roi['operating']['order_count'], 0)
        self.assertEqual(self.activity(source=order).status_code, 404)
        sources = self.ok(self.client.get('/api/admin/payroll/sources/', {'kind': 'DISPATCH', 'source_id': order.pk}))
        self.assertEqual(sources['total'], 0)

    def test_realized_shop_income_includes_reversal_but_excludes_deposit_topup(self):
        self.assign(self.plan(base_fen=0))
        order = self.dispatch_order(status='COMPLETED', completed_at=moment())
        wallet = get_platform_wallet()
        original = Transaction.objects.create(wallet=wallet, order=order, amount=2000, tx_type='SHOP_INCOME')
        reversal = Transaction.objects.create(wallet=wallet, order=order, amount=-400, tx_type='SHOP_INCOME', reversal_of=original)
        ignored = Transaction.objects.create(wallet=wallet, amount=999999, tx_type='DEPOSIT_INCOME')
        for row in (original, reversal, ignored):
            Transaction.objects.filter(pk=row.pk).update(created_at=moment())
        self.ok(self.cost(0))
        self.assertEqual(self.roi()['operating']['realized_shop_income_fen'], 1600)

    def test_invalid_month_range_rates_and_night_window_are_rejected(self):
        self.assertEqual(self.client.get('/api/admin/payroll/preview/', {'month': '2026-13'}).status_code, 400)
        self.assertEqual(self.client.get('/api/admin/operating-roi/', {'start_date': '2026-11-01', 'end_date': '2026-10-01'}).status_code, 400)
        self.assertEqual(self.client.put('/api/admin/payroll/config/', {'night_start': '22:00', 'night_end': '22:00', 'revision': 0}, format='json').status_code, 400)
        plan = self.plan()
        self.assertEqual(self.client.patch(f'/api/admin/salary-plans/{plan.pk}/', {'dispatch_day_bps': 10001, 'revision': 1}, format='json').status_code, 400)
