from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from orders.points_followup_settlement import expected_report_payout


class FollowupReportTimeTests(SimpleTestCase):
    def test_audited_amount_is_selected_by_refund_time(self):
        ended = datetime(2026, 1, 1, tzinfo=timezone.utc)
        posting = SimpleNamespace(termination_processed_at=ended)
        share = SimpleNamespace(original_income=100, retained_income=60)
        report = SimpleNamespace(audited_at=ended - timedelta(microseconds=1))
        self.assertEqual(expected_report_payout(report, posting, share), 100)
        report.audited_at = ended + timedelta(microseconds=1)
        self.assertEqual(expected_report_payout(report, posting, share), 60)
        report.audited_at = ended
        with self.assertRaises(ValidationError):
            expected_report_payout(report, posting, share)
        share.retained_income = 100
        self.assertEqual(expected_report_payout(report, posting, share), 100)
        report.audited_at = None
        with self.assertRaises(ValidationError):
            expected_report_payout(report, posting, share)
