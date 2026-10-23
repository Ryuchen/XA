from datetime import timedelta
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from console.serializers import AdminProviderReportSerializer
from orders.serializers import OrderSerializer
from wallet.models import ProviderReport


class QuantitySerializer(OrderSerializer):
    class Meta(OrderSerializer.Meta):
        fields = ['quantity_unit']


class ReportTimeSerializer(AdminProviderReportSerializer):
    class Meta(AdminProviderReportSerializer.Meta):
        fields = ['created_at', 'submitted_at']


class OrderDisplayContractTest(SimpleTestCase):
    def test_quantity_unit_uses_sale_snapshot_after_catalog_change(self):
        order = SimpleNamespace(
            gameplay_snapshot={'price_unit': '小时'},
            service=SimpleNamespace(price_unit='局'),
        )
        self.assertEqual(QuantitySerializer(order).data['quantity_unit'], '小时')
        order.service.price_unit = '次'
        self.assertEqual(QuantitySerializer(order).data['quantity_unit'], '小时')
        self.assertIn('quantity_unit', OrderSerializer().fields)
        self.assertTrue(OrderSerializer().fields['quantity_unit'].read_only)

    def test_old_or_invalid_unit_does_not_infer_current_catalog_unit(self):
        for snapshot in ({}, {'price_unit': ''}, {'price_unit': '   '}, {'price_unit': 3}, None, []):
            with self.subTest(snapshot=snapshot):
                order = SimpleNamespace(
                    gameplay_snapshot=snapshot,
                    service=SimpleNamespace(price_unit='小时'),
                )
                self.assertEqual(QuantitySerializer(order).data['quantity_unit'], '份')
        order = SimpleNamespace(gameplay_snapshot={'price_unit': ' 局 '})
        self.assertEqual(QuantitySerializer(order).data['quantity_unit'], '局')

    def test_report_submission_time_is_distinct_from_creation_time(self):
        created = timezone.now() - timedelta(hours=3)
        submitted = timezone.now() - timedelta(minutes=5)
        report = ProviderReport(created_at=created, submitted_at=submitted)
        row = ReportTimeSerializer(report).data
        self.assertEqual(parse_datetime(row['created_at']), created)
        self.assertEqual(parse_datetime(row['submitted_at']), submitted)
        self.assertNotEqual(row['created_at'], row['submitted_at'])
        self.assertIn('submitted_at', AdminProviderReportSerializer().fields)
        self.assertTrue(AdminProviderReportSerializer().fields['submitted_at'].read_only)

    def test_draft_and_legacy_missing_submission_time_remain_null(self):
        for status in ('DRAFT', 'PENDING', 'APPROVED', 'REJECTED'):
            with self.subTest(status=status):
                report = ProviderReport(status=status, created_at=timezone.now(), submitted_at=None)
                row = ReportTimeSerializer(report).data
                self.assertIsNone(row['submitted_at'])
                self.assertTrue(row['created_at'])
