"""A late direct-payment report review must not pretend to settle funds."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, PointLedger
from orders.tests.factories import make_console_user
from orders.tests.test_direct_clearing import ClearingFixture
from site_messages.models import Message
from wallet.models import IncomeHold, ProviderReport, Transaction, Wallet


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
class DirectReportLateReviewTest(ClearingFixture, TestCase):
    def verify_late_review(self, count):
        order, intent, participants = self.make_completed(count=count)
        staff = make_console_user(['report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        token, _ = issue_account_tokens(staff_account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        submitted = timezone.now() - timedelta(hours=1)
        reports = list(ProviderReport.objects.filter(order=order).order_by('pk'))
        ProviderReport.objects.filter(order=order).update(
            status='PENDING', auditor=None, auditor_account=None, audited_at=None, submitted_at=submitted)
        balances_before = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))

        with self.captureOnCommitCallbacks(execute=True):
            for report in reports:
                response = client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)

        messages = list(Message.objects.filter(related_order_id=order.pk, title='报单审核通过'))
        self.assertEqual(len(messages), count)
        self.assertEqual({message.recipient_id for message in messages},
                         {provider.pk for provider in participants})
        self.assertTrue(all(message.preview == '凭证已通过，收益待核对，请联系客服'
                            for message in messages))
        order.refresh_from_db()
        self.assertEqual(order.report_ready_at, submitted)
        self.assertEqual(order.consumption_ready_at, submitted + timedelta(hours=72))
        self.assertIsNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertEqual(ProviderReport.objects.filter(order=order, status='APPROVED',
            auditor_account=staff_account).count(), count)
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')),
                         balances_before)
        self.assertFalse(Transaction.objects.filter(order=order).exists())
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        intent.refresh_from_db()
        self.assertEqual(intent.status, 'PAID')

    def test_single_report_after_window(self):
        self.verify_late_review(count=1)

    def test_double_reports_after_window(self):
        self.verify_late_review(count=2)
