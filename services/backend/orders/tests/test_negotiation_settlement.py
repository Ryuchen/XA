from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from stores.db import transaction

from orders.models import Order
from orders.negotiation_settlement import settle_due_negotiated
from orders.settlement import SettlementError
from orders.tests import test_negotiation_termination as termination_fixture
from wallet.models import ProviderReport, ProviderReportImage, Transaction, get_platform_wallet
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiationSettlementTest(APITestCase):
    account = termination_fixture.NegotiationTerminationTests.account
    client_for = termination_fixture.NegotiationTerminationTests.client_for
    case = termination_fixture.NegotiationTerminationTests.case
    propose = termination_fixture.NegotiationTerminationTests.propose
    accepted = termination_fixture.NegotiationTerminationTests.accepted
    execute = termination_fixture.NegotiationTerminationTests.execute

    def setUp(self):
        termination_fixture.NegotiationTerminationTests.setUp(self)

    def report(self, account, submitted_at):
        report = ProviderReport.objects.get(order=self.order, provider_account=account)
        report.entry_image = 'test/entry.png'; report.completion_image = 'test/complete.png'
        report.status = 'APPROVED'; report.submitted_at = submitted_at
        report.audited_at = submitted_at; report.auditor = self.operator; report.auditor_account = self.operator_account
        report.payout_amount = self.order.providers.get(provider_account=account).provider_income
        report.save(update_fields=['entry_image', 'completion_image', 'status', 'submitted_at', 'payout_amount',
                                   'audited_at', 'auditor', 'auditor_account'])
        ProviderReportImage.objects.create(report=report, image='test/result.png')
        return report

    def settle(self, receipt, at):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order.pk)
            return settle_due_negotiated(order, receipt, now=at)

    def prior_unaffected_payment(self, amount=1000):
        """Use the ordinary settlement path while A's scoped case stays open."""
        from orders.services import ensure_fulfillment_reports
        from orders.service_settlement import settle_due_service_shares
        case, proposal = self.accepted(amount=amount)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        ensure_fulfillment_reports(self.order)
        self.report(self.a_account, completed)
        report = self.report(self.b_account, completed)
        paid_at = completed + timedelta(hours=73)
        with patch('django.utils.timezone.now', return_value=paid_at), transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order.pk)
            self.assertEqual(settle_due_service_shares(order), [self.b.pk])
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        return case, proposal, report, paid_at

    def test_unpaid_referral_uses_net_paid_after_negotiated_refund(self):
        from engagement.models import PointFundingLot
        from orders.tests.factories import make_user
        inviter_user = make_user()
        inviter = self.account(inviter_user)
        self.order.inviter, self.order.inviter_account = inviter_user, inviter
        self.order.inviter_commission_rate_snapshot = 10
        self.order.inviter_commission, self.order.shop_income = 1000, 1000
        self.order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot',
                                       'inviter_commission', 'shop_income'])
        case, proposal = self.accepted(amount=1000)
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        Order.objects.filter(pk=self.order.pk).update(status='COMPLETED', completed_at=completed)
        self.report(self.a_account, completed)
        self.report(self.b_account, completed)
        with patch('django.utils.timezone.now', return_value=completed + timedelta(hours=73)):
            self.assertEqual(set(self.settle(receipt, completed + timedelta(hours=73))), {self.a.pk, self.b.pk})
            self.assertEqual(self.settle(receipt, completed + timedelta(hours=74)), [])
        receipt.refresh_from_db()
        self.assertEqual(receipt.inviter_transaction.amount, 900)
        self.assertEqual(receipt.shop_transaction.amount, 1100)
        self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 900)
        lot = PointFundingLot.objects.get(source_key=f'negotiation-referral:{receipt.pk}')
        self.assertEqual((lot.account_id, lot.kind, lot.remaining), (inviter.pk, 'PREPAID', 900))

    def test_service_referral_is_recorded_as_boss_principal(self):
        from engagement.models import PointFundingLot
        from orders.service_settlement import settle_due_service_shares
        from orders.services import ensure_fulfillment_reports
        from orders.tests.factories import make_user
        inviter_user = make_user()
        inviter = self.account(inviter_user)
        self.order.inviter, self.order.inviter_account = inviter_user, inviter
        self.order.inviter_commission_rate_snapshot = 10
        self.order.inviter_commission, self.order.shop_income = 1000, 1000
        completed = timezone.now()
        self.order.status, self.order.completed_at = 'COMPLETED', completed
        self.order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot',
            'inviter_commission', 'shop_income', 'status', 'completed_at'])
        ensure_fulfillment_reports(self.order)
        self.report(self.a_account, completed)
        self.report(self.b_account, completed)
        with patch('django.utils.timezone.now', return_value=completed + timedelta(hours=73)), transaction.atomic():
            order = Order.objects.select_for_update().get(pk=self.order.pk)
            self.assertEqual(set(settle_due_service_shares(order)), {self.a.pk, self.b.pk})
        tx = Transaction.objects.get(order=self.order, wallet__account=inviter, tx_type='INCOME')
        lot = PointFundingLot.objects.get(source_key=f'referral-income:{tx.pk}')
        self.assertEqual((tx.amount, lot.kind, lot.remaining), (1000, 'PREPAID', 1000))

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_previously_paid_unaffected_provider_is_preserved_through_refund_and_final_settlement(self):
        case, proposal, report, paid_at = self.prior_unaffected_payment()
        client = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'prior-unaffected-paid'}
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            result = client.post(url, payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            retry = client.post(url, payload, format='json')
            self.assertEqual(retry.data['data']['execution_id'], result.data['data']['execution_id'])
        from orders.models import NegotiationExecution
        receipt = NegotiationExecution.objects.get(pk=result.data['data']['execution_id'])
        assignment = self.order.providers.get(provider_account=self.b_account)
        credit = Transaction.objects.get(order=self.order, tx_type='INCOME', wallet__account=self.b_account)
        self.assertEqual(receipt.unaffected_report_evidence[str(assignment.pk)]['report_id'], report.pk)
        self.assertEqual(receipt.unaffected_report_evidence[str(assignment.pk)]['income_transaction_id'], credit.pk)
        self.assertEqual(self.settle(receipt, paid_at + timedelta(minutes=2)), [self.a.pk])
        self.assertEqual(self.settle(receipt, paid_at + timedelta(minutes=3)), [])
        self.order.refresh_from_db(); receipt.refresh_from_db()
        self.assertEqual(self.order.settled_at, receipt.finalized_at)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME', wallet__account=self.b_account).count(), 1)
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 3000)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SHOP_INCOME').count(), 1)

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_corrupt_previously_paid_report_rolls_back_entire_refund(self):
        case, proposal, report, paid_at = self.prior_unaffected_payment()
        report.entry_image = ''; report.save(update_fields=['entry_image'])
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = boss.balance
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            result = self.client_for(self.operator_account).post(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'prior-invalid'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        boss.refresh_from_db(); case.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((boss.balance, case.status, self.order.payment_status), (before, 'OPEN', 'PAID'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='INCOME', wallet__account=self.b_account).count(), 1)

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_previously_paid_unaffected_share_must_have_completed_both_waiting_periods(self):
        case, proposal, report, paid_at = self.prior_unaffected_payment()
        assignment = self.order.providers.get(provider_account=self.b_account)
        assignment.settled_at = report.submitted_at + timedelta(hours=71)
        assignment.save(update_fields=['settled_at'])
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = boss.balance
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            result = self.client_for(self.operator_account).post(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'prior-too-early'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        boss.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, case.status), (before, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)

    def test_previously_paid_review_evidence_cannot_change_before_remaining_settlement(self):
        case, proposal, report, paid_at = self.prior_unaffected_payment()
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            receipt = self.execute(case, proposal)
        report.audited_at += timedelta(seconds=1)
        report.save(update_fields=['audited_at'])
        with self.assertRaisesMessage(SettlementError, '审核凭证异常'):
            self.settle(receipt, paid_at + timedelta(minutes=2))
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 0)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_previously_paid_report_without_review_evidence_cannot_trigger_refund(self):
        case, proposal, report, paid_at = self.prior_unaffected_payment()
        report.audited_at = None; report.save(update_fields=['audited_at'])
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = boss.balance
        with patch('django.utils.timezone.now', return_value=paid_at + timedelta(minutes=1)):
            result = self.client_for(self.operator_account).post(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'prior-without-audit'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        boss.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, case.status), (before, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    def test_only_after_whole_order_and_own_report_clock_independent_release(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        self.assertEqual(self.settle(receipt, timezone.now() + timedelta(days=10)), [])
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed)
        self.report(self.b_account, completed + timedelta(hours=3))
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=74)), [self.a.pk])
        wallet_a = get_wallet(user=self.a, account=self.a_account)
        wallet_b = get_wallet(user=self.b, account=self.b_account)
        self.assertEqual((wallet_a.balance, wallet_b.balance), (3000, 0))
        self.order.refresh_from_db(); self.assertIsNone(self.order.settled_at)
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=76)), [self.b.pk])
        wallet_b.refresh_from_db(); self.order.refresh_from_db(); receipt.refresh_from_db()
        self.assertEqual((wallet_a.balance, wallet_b.balance, self.order.shop_income), (3000, 4000, 2000))
        self.assertEqual(receipt.shop_transaction.amount, 2000)
        self.assertEqual(self.order.settled_at, receipt.finalized_at)
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=77)), [])
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SHOP_INCOME').count(), 1)

    def test_rejected_and_resubmitted_report_resets_freeze_clock(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        report = self.report(self.a_account, completed + timedelta(hours=10))
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=75)), [])
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=83)), [self.a.pk])
        report.refresh_from_db(); self.assertEqual(report.status, 'APPROVED')

    def test_refund_receipt_corruption_prevents_income_release(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed)
        tx = receipt.customer_transaction; tx.amount -= 10; tx.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '退款凭证待核对'):
            self.settle(receipt, completed + timedelta(hours=73))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    def test_missing_refund_audit_prevents_income_release(self):
        from console.models import AdminAuditLog
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed)
        AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/').delete()
        with self.assertRaisesMessage(SettlementError, '退款凭证待核对'):
            self.settle(receipt, completed + timedelta(hours=73))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    def test_payment_status_tampering_prevents_income_release(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.payment_status = 'REFUNDED'
        self.order.save(update_fields=['status', 'completed_at', 'payment_status'])
        self.report(self.a_account, completed)
        with self.assertRaisesMessage(SettlementError, '退款状态或时间凭证'):
            self.settle(receipt, completed + timedelta(hours=73))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    def test_tampered_final_credit_blocks_consumption_confirmation(self):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed); self.report(self.b_account, completed)
        self.settle(receipt, completed + timedelta(hours=73))
        receipt.refresh_from_db()
        tx = receipt.shop_transaction
        tx.amount -= 10; tx.save(update_fields=['amount'])
        with patch('django.utils.timezone.now', return_value=completed + timedelta(hours=74)):
            with self.assertRaisesMessage(SettlementError, '收益结算流水待核对'):
                process_order(self.order.pk)
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=self.order).exists())

    def test_tampered_unaffected_report_blocks_consumption_confirmation(self):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed)
        report = self.report(self.b_account, completed)
        self.settle(receipt, completed + timedelta(hours=73))
        report.submitted_at += timedelta(hours=1); report.save(update_fields=['submitted_at'])
        with patch('django.utils.timezone.now', return_value=completed + timedelta(hours=74)):
            with self.assertRaisesMessage(SettlementError, '未受影响陪玩的报单或时限凭证异常'):
                process_order(self.order.pk)
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=self.order).exists())

    def test_changed_submitted_time_after_first_settlement_blocks_second_payment(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        report = self.report(self.a_account, completed)
        self.report(self.b_account, completed + timedelta(hours=3))
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=74)), [self.a.pk])
        report.submitted_at += timedelta(hours=1); report.save(update_fields=['submitted_at'])
        with self.assertRaisesMessage(SettlementError, '报单或时限凭证异常'):
            self.settle(receipt, completed + timedelta(hours=76))
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)

    def test_corrupted_deposit_repayment_blocks_other_participants_settlement(self):
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositIncomeApplication
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        current = self.order
        self.account = self.a_account; self.profile = self.a.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(user=self.a, account=self.a_account)
        self.platform = get_platform_wallet(); self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.a, amount=10000,
                                          provider_account=self.a_account, payment_status='PAID')
        RefundDepositDeficitTest.deficit(self, 200)
        self.order = current
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed); self.report(self.b_account, completed + timedelta(hours=3))
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=74)), [self.a.pk])
        application = RefundDepositIncomeApplication.objects.get(source_transaction__order=current)
        repayment = application.repayments.get()
        self.assertEqual(repayment.amount, 200)
        debit = repayment.provider_transaction; debit.amount -= 1; debit.save(update_fields=['amount'])
        with self.assertRaisesMessage(SettlementError, '收益补押金流水异常'):
            self.settle(receipt, completed + timedelta(hours=76))
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)

    def test_dual_rounding_surplus_is_credited_to_store_once(self):
        scope = sorted([self.a_account.pk, self.b_account.pk])
        case, proposal = self.accepted(scope=scope, amount=510)
        receipt = self.execute(case, proposal)
        completed = self.order.completed_at
        self.order.refresh_from_db(); completed = self.order.completed_at
        self.report(self.a_account, completed); self.report(self.b_account, completed)
        platform = get_platform_wallet(); before = platform.balance
        self.assertEqual(set(self.settle(receipt, completed + timedelta(hours=73))), {self.a.pk, self.b.pk})
        receipt.refresh_from_db(); platform.refresh_from_db()
        self.assertEqual((receipt.rounding_to_store_units, receipt.shop_transaction.amount, platform.balance - before),
                         (10, 2010, 2010))

    @override_settings(REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_zero_negotiated_income_closes_only_its_recovery_permit(self):
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositRecoveryPermit
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        current = self.order
        self.account = self.a_account; self.profile = self.a.escort_profile
        self.profile.deposit_mode = 'EARNINGS'; self.profile.deposit_paid = 10000
        self.profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        self.wallet = get_wallet(user=self.a, account=self.a_account)
        self.platform = get_platform_wallet(); self.platform.balance = 10000; self.platform.save(update_fields=['balance'])
        self.order = make_completed_order(self.boss, self.a, amount=10000,
                                          provider_account=self.a_account, payment_status='PAID')
        deficit = RefundDepositDeficitTest.deficit(self, 200)
        self.order = current
        permit = RefundDepositRecoveryPermit.objects.create(profile=self.profile, account=self.a_account, order=current)
        case, proposal = self.accepted(amount=4000)
        receipt = self.execute(case, proposal)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, completed); self.report(self.b_account, completed)
        self.assertEqual(set(self.settle(receipt, completed + timedelta(hours=73))), {self.a.pk, self.b.pk})
        permit.refresh_from_db(); deficit.refresh_from_db()
        share = receipt.shares.get(account=self.a_account)
        self.assertEqual((permit.close_reason, permit.zero_negotiated_share_id, share.retained_income, deficit.remaining),
                         ('ZERO_NEGOTIATED_SETTLED', share.pk, 0, 200))
        self.assertIsNotNone(permit.closed_at)
        self.assertEqual(self.settle(receipt, completed + timedelta(hours=74)), [])
        from orders.negotiation_settlement import report_settlement_snapshot
        report = ProviderReport.objects.get(order=self.order, provider_account=self.a_account)
        self.assertEqual(report_settlement_snapshot(report), ('SETTLED', share.settled_at))

    def test_confirmation_worker_releases_affected_earlier_than_later_team_completion_clock(self):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation, PointLedger
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        consent = proposal.accepted_at
        completed = consent + timedelta(hours=60)
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        self.report(self.a_account, consent)
        self.report(self.b_account, completed)
        with patch('django.utils.timezone.now', return_value=consent + timedelta(hours=80)):
            self.assertFalse(process_order(self.order.pk))
        a = get_wallet(user=self.a, account=self.a_account)
        b = get_wallet(user=self.b, account=self.b_account)
        self.assertEqual((a.balance, b.balance), (3000, 0))
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=self.order).exists())
        with patch('django.utils.timezone.now', return_value=consent + timedelta(hours=133)):
            self.assertTrue(process_order(self.order.pk))
        b.refresh_from_db(); self.order.refresh_from_db(); receipt.refresh_from_db()
        self.assertEqual((b.balance, self.order.settled_at), (4000, receipt.finalized_at))
        confirmation = ConsumptionConfirmation.objects.get(order=self.order)
        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (10000, 1000))
        self.assertEqual(PointLedger.objects.get(order=self.order, kind='CONSUMPTION').amount, 9000)


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class NegotiatedReportQueueStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    account = termination_fixture.NegotiationTerminationTests.account
    case = termination_fixture.NegotiationTerminationTests.case
    propose = termination_fixture.NegotiationTerminationTests.propose
    accepted = termination_fixture.NegotiationTerminationTests.accepted
    execute = termination_fixture.NegotiationTerminationTests.execute

    def client_for(self, account):
        from club_accounts.tokens import issue_account_tokens
        from rest_framework.test import APIClient
        from stores.context import current_store
        host = 'xa.esports.club' if current_store().slug == 'xinan' else 'sl.esports.club'
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def test_matching_ids_and_corrupt_receipt_do_not_change_other_store_queue(self):
        from console.serializers import pending_report_order_block_q
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        stores = (resolve_store(), xinan_store())
        identities = []
        for store in stores:
            with store_context(store):
                termination_fixture.NegotiationTerminationTests.setUp(self)
                case, proposal = self.accepted()
                response = self.client_for(self.operator_account).post(
                    f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                    {'proposal_id':proposal.pk, 'client_request_id':'same-tenant-execution'}, format='json')
                self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)
                from orders.models import NegotiationExecution
                receipt = NegotiationExecution.objects.get(pk=response.data['data']['execution_id'])
                order = Order.objects.get(pk=self.order.pk)
                order.status = 'COMPLETED'; order.completed_at = timezone.now()
                order.save(update_fields=['status', 'completed_at'])
                report = ProviderReport.objects.get(order=order, provider_account=self.a_account)
                report.status = 'PENDING'; report.submitted_at = timezone.now()
                report.save(update_fields=['status', 'submitted_at'])
                identities.append((order.pk, report.pk, receipt.customer_transaction_id, case.pk, proposal.pk))
                if store.slug == 'xinan':
                    wrong_store_client = self.client_for(self.operator_account)
                self.assertFalse(ProviderReport.objects.filter(pending_report_order_block_q()).exists())
        self.assertEqual(identities[0], identities[1])
        with store_context(stores[0]):
            denied = wrong_store_client.get(f'/api/admin/aftersales/{identities[0][3]}/negotiation-execution/',
                {'proposal_id':identities[0][4], 'client_request_id':'same-tenant-execution'}, HTTP_HOST='sl.esports.club')
            self.assertEqual(denied.status_code, 401)
            tx = Transaction.objects.get(pk=identities[0][2]); tx.amount -= 1; tx.save(update_fields=['amount'])
            self.assertEqual(list(ProviderReport.objects.filter(pending_report_order_block_q())
                                  .values_list('pk', flat=True)), [identities[0][1]])
        with store_context(stores[1]):
            self.assertFalse(ProviderReport.objects.filter(pending_report_order_block_q()).exists())
