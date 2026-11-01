from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from console.serializers import AdminProviderReportSerializer
from orders.models import AfterSale, OrderProvider
from orders.tests.factories import (
    make_completed_order, make_escort_level, make_provider, make_superuser, make_user,
)
from wallet.models import COMMISSION_RATE_KEY, ProviderReport, SystemConfig
from wallet.serializers import ReportSerializer


class ReportFinancialSnapshotTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.admin = make_superuser()

    def report(self, **kwargs):
        return ProviderReport.objects.create(
            provider=self.provider, game_name='联调报单', amount=10000, **kwargs,
        )

    def test_historical_declared_rate_preview_matches_approval(self):
        level = make_escort_level(commission_rate=12)
        self.provider.escort_profile.level = level
        self.provider.escort_profile.save(update_fields=['level'])
        report = self.report(commission_rate=35)
        preview = AdminProviderReportSerializer(report).data
        self.assertEqual(preview['suggested_commission_rate'], 35)
        self.assertEqual(preview['commission_source_label'], '报单填写')
        self.assertEqual(preview['expected_income'], 6500)
        self.assertEqual(ReportSerializer(report).data['expected_income'], 6500)
        self.client.force_authenticate(self.admin)
        result = self.client.post(f'/api/admin/reports/{report.pk}/approve/').data
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(result['data']['payout_amount'], preview['expected_income'])

    def test_fixed_order_share_is_visible_as_fixed_on_both_ends(self):
        order = make_completed_order(make_user(), self.provider, amount=10000)
        share = OrderProvider.objects.create(
            order=order, provider=self.provider, settlement_base=5000,
            commission_type='FIXED', commission_rate=0, commission_fixed=300,
            provider_income=4700,
        )
        report = self.report(order=order)
        for serializer in (AdminProviderReportSerializer, ReportSerializer):
            row = serializer(report).data
            self.assertEqual(row['commission_type'], 'FIXED')
            self.assertEqual(row['commission_fixed'], 300)
            self.assertEqual(row['expected_income'], share.provider_income)
        self.client.force_authenticate(self.admin)
        result = self.client.post(f'/api/admin/reports/{report.pk}/approve/').data
        self.assertEqual(result['code'], 0, result)
        self.assertEqual(result['data']['commission_type'], 'FIXED')
        self.assertEqual(result['data']['payout_amount'], 4700)

    def test_approved_history_uses_saved_zero_rate_and_payout(self):
        SystemConfig.objects.create(key=COMMISSION_RATE_KEY, value='30')
        report = self.report(status='APPROVED', commission_rate=0, payout_amount=10000)
        row = AdminProviderReportSerializer(report).data
        self.assertEqual(row['suggested_commission_rate'], 0)
        self.assertEqual(row['expected_income'], 10000)
        self.assertEqual(row['commission_source_label'], '审核快照')

    def test_approved_linked_payout_does_not_recompute_historical_income(self):
        order = make_completed_order(make_user(), self.provider, amount=10000,
                                     provider_income=8000)
        report = self.report(order=order, status='APPROVED', commission_rate=30,
                             payout_amount=7000)
        for serializer in (AdminProviderReportSerializer, ReportSerializer):
            self.assertEqual(serializer(report).data['expected_income'], 7000)

    def test_prefetched_report_shares_do_not_query_for_each_financial_field(self):
        order = make_completed_order(make_user(), self.provider, amount=10000)
        OrderProvider.objects.create(order=order, provider=self.provider,
                                     settlement_base=10000, provider_income=8000)
        report = self.report(order=order)
        report = ProviderReport.objects.select_related('order', 'provider__escort_profile', 'auditor').prefetch_related(
            'order__providers', 'order__aftersales', 'result_images',
        ).get(pk=report.pk)
        from wallet.settlement_display import prepare_order_settlement_display
        prepare_order_settlement_display([report.order])
        with self.assertNumQueries(0):
            self.assertEqual(AdminProviderReportSerializer(report).data['expected_income'], 8000)

    def test_report_list_batches_aftersale_status_for_multiple_orders(self):
        self.client.force_authenticate(self.admin)

        def create_report():
            boss = make_user()
            order = make_completed_order(boss, self.provider, amount=10000)
            OrderProvider.objects.create(order=order, provider=self.provider,
                                         settlement_base=10000, provider_income=8000)
            AfterSale.objects.create(order=order, account=get_or_create_account_for_legacy_user(boss), reason='隔离测试')
            return self.report(order=order, status=ProviderReport.Status.PENDING)

        create_report()
        self.client.get('/api/admin/reports/')  # Populate authentication-related caches.
        with CaptureQueriesContext(connection) as single_queries:
            single = self.client.get('/api/admin/reports/').data
        self.assertEqual(single['code'], 0, single)
        self.assertEqual(single['data']['list'][0]['aftersale_status'], 'OPEN')
        for _ in range(7):
            create_report()
        with CaptureQueriesContext(connection) as multiple_queries:
            multiple = self.client.get('/api/admin/reports/').data
        self.assertEqual(multiple['code'], 0, multiple)
        self.assertEqual(len(multiple['data']['list']), 8)
        self.assertTrue(all(row['aftersale_status'] == 'OPEN' for row in multiple['data']['list']))
        self.assertLessEqual(len(multiple_queries), len(single_queries) + 1,
                             (len(single_queries), len(multiple_queries)))
