"""旧普通服务报单的 SUBMIT→REVIEW→结算/实销证据链。"""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation
from orders.models import Order, OrderProvider
from orders.settlement import SettlementError
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store
from wallet.models import (
    ProviderReport, RefundDepositRecoveryPermit, SettlementClockEvidence, Transaction,
    get_platform_wallet,
)
from wallet.services import get_wallet
from wallet.settlement_clocks import ClockConflict, ClockUnavailable


@override_settings(
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
)
class LegacyReportClockChainTest(APITestCase):
    databases = {'default', 'store_b'}
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    finish = flow.ReleaseFlowTest.finish

    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        self._order_seq = 0
        self.prepare_profile()

    def prepare_profile(self):
        profile = self.provider.escort_profile
        profile.deposit_paid = profile.effective_deposit_required
        profile.save(update_fields=['deposit_paid'])

    def new_order(self):
        self._order_seq += 1
        self.ok(self.recharge(
            trade_no=f'chain-receipt-{self._order_seq}',
            request_id=f'chain-recharge-{self._order_seq}',
        ))
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk,
            'client_request_id': f'chain-order-{self._order_seq}',
        }))
        return Order.objects.get(pk=data['id'])

    def submitted_report(self, at=None):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
            'kind': 'RESULT', 'image': flow.screenshot(),
        }, format='multipart'))
        if at is None:
            response = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        else:
            with patch('django.utils.timezone.now', return_value=at):
                response = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.ok(response)
        report.refresh_from_db()
        return order, report

    def review(self, report, *, action='approve', at=None):
        self.login(self.admin)
        data = {'audit_remark': '请补交战绩说明'} if action == 'reject' else {}
        if at is None:
            response = self.client.post(f'/api/admin/reports/{report.pk}/{action}/', data, format='json')
        else:
            with patch('django.utils.timezone.now', return_value=at):
                response = self.client.post(f'/api/admin/reports/{report.pk}/{action}/', data, format='json')
        self.ok(response)
        report.refresh_from_db()
        return report

    def approved_report(self):
        order, report = self.submitted_report()
        self.review(report)
        return order, report

    def process_at(self, order, at):
        with patch('django.utils.timezone.now', return_value=at):
            return process_order(order.pk)

    def assert_unpaid(self, order):
        order.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertFalse(Transaction.objects.filter(
            order=order, tx_type__in=['INCOME', 'SHOP_INCOME'],
        ).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    def test_backfilled_submission_time_cannot_release_income_or_consumption(self):
        order, report = self.approved_report()
        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(completed_at=now - timedelta(hours=74))
        self.assertFalse(process_order(order.pk))
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=now - timedelta(hours=73))

        with self.assertRaises(ClockConflict):
            process_order(order.pk)
        self.assert_unpaid(order)

    def test_submit_and_review_clock_outages_are_503_and_retry_preserves_facts(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
            'kind': 'RESULT', 'image': flow.screenshot(),
        }, format='multipart'))
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            denied = self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.assertEqual(denied.status_code, 503, denied.content)
        report.refresh_from_db()
        self.assertEqual((report.status, report.submitted_at), ('DRAFT', None))
        self.assertFalse(SettlementClockEvidence.objects.filter(order=order).exists())

        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        report.refresh_from_db()
        submitted_at = report.submitted_at
        self.login(self.admin)
        with override_settings(SETTLEMENT_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=False):
            denied = self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        self.assertEqual(denied.status_code, 503, denied.content)
        report.refresh_from_db()
        self.assertEqual((report.status, report.submitted_at, report.audited_at),
                         ('PENDING', submitted_at, None))
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order, operation='SUBMIT').count(), 1)
        self.assertFalse(SettlementClockEvidence.objects.filter(order=order, operation='REVIEW').exists())

        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        self.assertEqual(report.submitted_at, submitted_at)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order, operation='SUBMIT').count(), 1)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order, operation='REVIEW').count(), 1)
        self.assert_unpaid(order)

    def test_reject_image_resubmit_and_second_review_form_two_complete_rounds(self):
        base = timezone.now()
        first_submit = base
        rejected_at = base + timedelta(hours=1)
        image_at = base + timedelta(hours=2)
        second_submit = base + timedelta(hours=3)
        approved_at = base + timedelta(hours=4)
        order, report = self.submitted_report(at=first_submit)
        first_clock = SettlementClockEvidence.objects.get(order=order, operation='SUBMIT')
        self.assertEqual(first_clock.observed_at, first_submit)

        self.review(report, action='reject', at=rejected_at)
        self.assertEqual((report.status, report.submitted_at, report.audited_at),
                         ('REJECTED', first_submit, rejected_at))
        rejected_clock = SettlementClockEvidence.objects.exclude(
            subject_id=first_clock.subject_id,
        ).get(order=order, operation='REVIEW')
        self.assertEqual(rejected_clock.observed_at, rejected_at)

        # 驳回后的补图仍属于原轮次，不能重置提交或审核时间。
        self.login(self.provider)
        with patch('django.utils.timezone.now', return_value=image_at):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
                'kind': 'RESULT', 'image': flow.screenshot(),
            }, format='multipart'))
        report.refresh_from_db()
        self.assertEqual((report.submitted_at, report.audited_at), (first_submit, rejected_at))
        self.assertEqual(report.result_images.count(), 2)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order, operation='SUBMIT').count(), 1)

        # 仅把驳回状态篡改为通过，没有通过 REVIEW 回执，仍不能结算。
        ProviderReport.objects.filter(pk=report.pk).update(status='APPROVED')
        with self.assertRaises(ClockConflict):
            self.process_at(order, first_submit + timedelta(hours=73))
        self.assert_unpaid(order)
        ProviderReport.objects.filter(pk=report.pk).update(status='REJECTED')

        self.login(self.provider)
        with patch('django.utils.timezone.now', return_value=second_submit):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        report.refresh_from_db()
        self.assertEqual((report.status, report.submitted_at, report.audited_at),
                         ('PENDING', second_submit, None))
        self.review(report, at=approved_at)
        submits = list(SettlementClockEvidence.objects.filter(
            order=order, operation='SUBMIT',
        ).order_by('subject_id'))
        self.assertEqual(len(submits), 2)
        self.assertEqual([row.observed_at for row in submits], [first_submit, second_submit])
        self.assertNotEqual(submits[0].subject_id, submits[1].subject_id)
        approved_clock = SettlementClockEvidence.objects.get(
            order=order, operation='REVIEW', subject_id=submits[1].subject_id,
        )
        self.assertEqual(approved_clock.observed_at, approved_at)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order).count(), 4)

        deadline = second_submit + timedelta(hours=72)
        self.assertFalse(self.process_at(order, deadline - timedelta(microseconds=1)))
        self.assert_unpaid(order)
        platform = get_platform_wallet()
        platform.refresh_from_db()
        platform_before = platform.balance
        self.assertTrue(self.process_at(order, deadline))
        order.refresh_from_db()
        platform.refresh_from_db()
        provider_wallet = get_wallet(account=self.accounts[self.provider.pk])
        self.assertEqual(provider_wallet.balance, 8000)
        self.assertEqual(platform.balance, platform_before + 2000)
        self.assertEqual((order.settled_at, order.consumption_confirmed_at), (deadline, deadline))
        income = Transaction.objects.get(order=order, tx_type='INCOME')
        shop = Transaction.objects.get(order=order, tx_type='SHOP_INCOME')
        self.assertEqual((income.amount, shop.amount), (8000, 2000))
        self.assertEqual(sum(order.transactions.values_list('amount', flat=True)), 0)
        confirmation = ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual((confirmation.amount, confirmation.refunded_amount, confirmation.confirmed_at),
                         (10000, 0, deadline))
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order).count(), 4)

        self.assertFalse(self.process_at(order, deadline + timedelta(seconds=1)))
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SHOP_INCOME').count(), 1)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        self.assertEqual(SettlementClockEvidence.objects.filter(order=order).count(), 4)

    def test_missing_tampered_or_cross_database_receipts_stop_settlement(self):
        for mode in ('MISSING_SUBMIT', 'SUBMIT_DATABASE', 'MISSING_REVIEW',
                     'REVIEW_DATABASE', 'REVIEW_TIME'):
            with self.subTest(mode=mode):
                order, report = self.approved_report()
                submit = SettlementClockEvidence.objects.get(order=order, operation='SUBMIT')
                review = SettlementClockEvidence.objects.get(
                    order=order, operation='REVIEW', subject_id=submit.subject_id,
                )
                if mode == 'MISSING_SUBMIT':
                    SettlementClockEvidence.objects.filter(pk=submit.pk).delete()
                elif mode == 'SUBMIT_DATABASE':
                    SettlementClockEvidence.objects.filter(pk=submit.pk).update(
                        clock_evidence={**submit.clock_evidence, 'database_identity': 'another-store-database'},
                    )
                elif mode == 'MISSING_REVIEW':
                    SettlementClockEvidence.objects.filter(pk=review.pk).delete()
                elif mode == 'REVIEW_DATABASE':
                    SettlementClockEvidence.objects.filter(pk=review.pk).update(
                        clock_evidence={**review.clock_evidence, 'database_identity': 'another-store-database'},
                    )
                else:
                    ProviderReport.objects.filter(pk=report.pk).update(
                        audited_at=report.audited_at + timedelta(seconds=1),
                    )
                with self.assertRaises(ClockConflict):
                    self.process_at(order, report.submitted_at + timedelta(hours=73))
                self.assert_unpaid(order)

    def test_cross_person_or_changed_amount_cannot_use_original_receipts(self):
        for mode in ('PERSON', 'AMOUNT'):
            with self.subTest(mode=mode):
                order, report = self.approved_report()
                if mode == 'PERSON':
                    other = make_provider()
                    other_account = get_or_create_account_for_legacy_user(other)
                    ProviderReport.objects.filter(pk=report.pk).update(provider_account=other_account)
                else:
                    ProviderReport.objects.filter(pk=report.pk).update(
                        payout_amount=report.payout_amount + 1,
                    )
                with self.assertRaises(SettlementError):
                    self.process_at(order, report.submitted_at + timedelta(hours=73))
                self.assert_unpaid(order)

    def test_real_other_store_evidence_cannot_authorize_same_numeric_report(self):
        first = resolve_store()
        second = xinan_store()
        first_order, first_report = self.approved_report()
        first_submit = SettlementClockEvidence.objects.get(order=first_order, operation='SUBMIT')
        first_evidence = dict(first_submit.clock_evidence)
        first_provider_account = self.accounts[self.provider.pk]

        self.client = APIClient(HTTP_HOST='xa.esports.club')
        with store_context(second):
            flow.ReleaseFlowTest.setUp(self)
            self._order_seq = 0
            self.prepare_profile()
            second_order, second_report = self.approved_report()
            second_submit = SettlementClockEvidence.objects.get(order=second_order, operation='SUBMIT')
            self.assertEqual((first_order.pk, first_report.pk), (second_order.pk, second_report.pk))
            self.assertNotEqual(first_evidence['database_identity'],
                                  second_submit.clock_evidence['database_identity'])
            SettlementClockEvidence.objects.filter(pk=second_submit.pk).update(
                clock_evidence=first_evidence,
            )
            with self.assertRaises(ClockConflict):
                self.process_at(second_order, second_report.submitted_at + timedelta(hours=73))
            self.assert_unpaid(second_order)

        with store_context(first):
            self.assertTrue(self.process_at(
                first_order, first_report.submitted_at + timedelta(hours=73),
            ))
            self.assertEqual(get_wallet(account=first_provider_account).balance, 8000)
            self.assertTrue(ConsumptionConfirmation.objects.filter(order=first_order).exists())


    @override_settings(
        REFUND_DEPOSIT_DEFICIT_ENABLED=True,
        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
    )
    def test_zero_service_close_shares_clock_and_closed_or_foreign_retry_needs_none(self):
        self.service.commission_rate = 100
        self.service.save(update_fields=['commission_rate'])
        order, report = self.submitted_report()
        account = self.accounts[self.provider.pk]
        profile = self.provider.escort_profile
        assignment = OrderProvider.objects.get(
            order=order, provider=self.provider, provider_account=account,
        )
        self.assertEqual((assignment.settlement_base, assignment.provider_income,
                          assignment.commission_rate), (order.amount, 0, 100))
        permit = RefundDepositRecoveryPermit.objects.create(
            profile=profile, account=account, order=order,
        )
        self.review(report)
        self.assertEqual((order.provider_income, report.payout_amount), (0, 0))
        deadline = report.submitted_at + timedelta(hours=72)
        platform = get_platform_wallet()
        platform.refresh_from_db()
        provider_wallet = get_wallet(account=account)
        before = (platform.balance, provider_wallet.balance)

        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('zero service close disconnected'), create=True):
            with self.assertRaises(ClockUnavailable):
                self.process_at(order, deadline)
        order.refresh_from_db()
        assignment.refresh_from_db()
        permit.refresh_from_db()
        platform.refresh_from_db()
        provider_wallet.refresh_from_db()
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertIsNone(assignment.settled_at)
        self.assertIsNone(permit.closed_at)
        self.assertEqual((platform.balance, provider_wallet.balance), before)
        self.assertFalse(Transaction.objects.filter(
            order=order, tx_type__in=['INCOME', 'SHOP_INCOME'],
        ).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

        self.assertTrue(self.process_at(order, deadline))
        order.refresh_from_db()
        assignment.refresh_from_db()
        permit.refresh_from_db()
        platform.refresh_from_db()
        provider_wallet.refresh_from_db()
        confirmation = ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual(
            (assignment.settled_at, order.settled_at, order.consumption_confirmed_at,
             permit.closed_at, confirmation.confirmed_at),
            (deadline, deadline, deadline, deadline, deadline),
        )
        self.assertEqual((permit.close_reason, permit.zero_service_assignment_id),
                         ('ZERO_SERVICE_SETTLED', assignment.pk))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        shop = Transaction.objects.get(order=order, tx_type='SHOP_INCOME')
        self.assertEqual((shop.amount, shop.balance_before, shop.balance_after),
                         (10000, before[0], before[0] + 10000))
        self.assertEqual((platform.balance, provider_wallet.balance),
                         (before[0] + 10000, before[1]))
        self.assertEqual(sum(order.transactions.values_list('amount', flat=True)), 0)
        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (10000, 0))
        counts = (Transaction.objects.filter(order=order).count(),
                  ConsumptionConfirmation.objects.filter(order=order).count())

        # 已关闭及异单未关闭资格都在原记录检查后返回，不取得新时钟。
        from wallet.refund_recovery_order import close_for_zero_service
        foreign_order = self.new_order()
        foreign = RefundDepositRecoveryPermit.objects.create(
            profile=profile, account=account, order=foreign_order,
        )
        with patch('wallet.refund_recovery_order.require_clock',
                   side_effect=ClockUnavailable('closed or foreign retry needs no clock'), create=True):
            self.assertFalse(self.process_at(order, deadline + timedelta(seconds=1)))
            with transaction.atomic():
                close_for_zero_service(profile, assignment)
        permit.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(permit.closed_at, deadline)
        self.assertIsNone(foreign.closed_at)
        self.assertEqual(Transaction.objects.filter(order=order).count(), counts[0])
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), counts[1])
