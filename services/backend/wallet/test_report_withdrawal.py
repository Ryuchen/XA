"""陪玩撤回报单的真实状态、凭证与归属边界。"""

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from orders.tests.factories import make_completed_order, make_provider, make_user
from wallet.models import ProviderReport


class ReportWithdrawalTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.order = make_completed_order(
            make_user(), self.provider, provider_income=8000, amount=10000,
        )
        self.client.force_authenticate(self.provider)
        created = self.client.post(
            '/api/wallet/reports/', {'order_id': self.order.pk}, format='json',
        )
        self.assertEqual(created.data['code'], 0, created.data)
        self.report_id = created.data['data']['id']
        for kind, name in (
            ('ENTRY', 'entry.gif'),
            ('COMPLETION', 'completion.gif'),
            ('RESULT', 'result.gif'),
        ):
            uploaded = self.client.post(
                f'/api/wallet/reports/{self.report_id}/images/',
                {'kind': kind, 'image': self._image(name)}, format='multipart',
            )
            self.assertEqual(uploaded.data['code'], 0, uploaded.data)
        submitted = self.client.post(f'/api/wallet/reports/{self.report_id}/submit/')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        self.assertEqual(submitted.data['data']['status'], ProviderReport.Status.PENDING)

    @staticmethod
    def _image(name):
        content = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        return SimpleUploadedFile(name, content, content_type='image/gif')

    def test_withdraw_preserves_evidence_clears_review_clock_and_allows_resubmission(self):
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.report_ready_at)
        self.assertIsNotNone(self.order.consumption_ready_at)

        response = self.client.post(f'/api/wallet/reports/{self.report_id}/withdraw/')

        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['status'], ProviderReport.Status.DRAFT)
        self.assertTrue(response.data['data']['can_edit'])
        self.assertFalse(response.data['data']['can_withdraw'])
        self.assertIsNone(response.data['data']['submitted_at'])
        self.assertEqual(len(response.data['data']['result_images']), 1)
        report = ProviderReport.objects.get(pk=self.report_id)
        self.assertTrue(report.entry_image)
        self.assertTrue(report.completion_image)
        self.assertEqual(report.result_images.count(), 1)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.report_ready_at)
        self.assertIsNone(self.order.consumption_ready_at)

        resubmitted = self.client.post(f'/api/wallet/reports/{self.report_id}/submit/')
        self.assertEqual(resubmitted.data['code'], 0, resubmitted.data)
        self.assertEqual(resubmitted.data['data']['status'], ProviderReport.Status.PENDING)
        self.assertTrue(resubmitted.data['data']['can_withdraw'])
        self.assertIsNotNone(resubmitted.data['data']['submitted_at'])
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.report_ready_at)

    def test_only_owner_and_pending_report_can_withdraw(self):
        other = make_provider()
        self.client.force_authenticate(other)
        forbidden = self.client.post(f'/api/wallet/reports/{self.report_id}/withdraw/')
        self.assertNotEqual(forbidden.data['code'], 0)
        self.assertEqual(
            ProviderReport.objects.get(pk=self.report_id).status,
            ProviderReport.Status.PENDING,
        )

        self.client.force_authenticate(self.provider)
        for status in (
            ProviderReport.Status.DRAFT,
            ProviderReport.Status.REJECTED,
            ProviderReport.Status.APPROVED,
        ):
            ProviderReport.objects.filter(pk=self.report_id).update(status=status)
            response = self.client.post(f'/api/wallet/reports/{self.report_id}/withdraw/')
            self.assertEqual(response.data['code'], 400, response.data)
            self.assertEqual(ProviderReport.objects.get(pk=self.report_id).status, status)
