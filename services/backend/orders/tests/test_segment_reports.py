from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import (Order, OrderParticipationSegment, OrderSegmentReport,
                           OrderSegmentReportSubmission)
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport, Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentReportsTest(APITransactionTestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer

    def prepare(self):
        replacement = self.replacement(); order = self.new_order()
        order.service_clock_version = 1; order.service_freeze_hours = 72; order.service_deposit_percent = 60
        order.save(update_fields=['service_clock_version', 'service_freeze_hours', 'service_deposit_percent'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        old = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=False)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id':old.pk, 'completion_image':flow.screenshot()}, format='multipart'))
        return order, replacement, old

    def act(self, order, segment, action, key, **data):
        payload = {'segment_id':segment.pk, 'action':action, 'client_request_id':key, **data}
        return self.client.post(f'/api/orders/orders/{order.pk}/segment-report/', payload,
                                format='multipart' if action == 'upload' else 'json')

    def current(self, order, segment):
        data = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        return next(row for row in data['my_participation_segments'] if row['id'] == segment.pk)

    def review(self, order, segment, action, version, **data):
        self.login(self.admin)
        return self.client.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                                {'action':action, 'version':version, **data}, format='json')

    def test_reject_resubmit_keeps_prior_evidence_and_resets_freeze_start(self):
        order, replacement, old = self.prepare()
        self.ok(self.act(order, old, 'open', 'open-old'))
        self.ok(self.act(order, old, 'upload', 'upload-old', image=flow.screenshot()))
        self.ok(self.act(order, old, 'submit', 'submit-old-1', version=0))
        report = OrderSegmentReport.objects.get(segment=old)
        first = report.submissions.get(version=1)
        before = (first.entry_image.name, first.completion_image.name, first.result_images.get().image.name)
        self.assertEqual((report.status, first.status), ('PENDING', 'PENDING'))
        info = self.current(order, old)['segment_report']['submission']
        self.assertEqual(info['freeze_until'], first.submitted_at + timedelta(hours=72))
        self.assertTrue(self.ok(self.act(order, old, 'submit', 'submit-old-1', version=0))['request_replayed'])
        self.assertEqual(report.submissions.count(), 1)
        self.assertNotEqual(self.review(order, old, 'reject', 1, remark='x').data['code'], 0)
        self.ok(self.review(order, old, 'reject', 1, remark='请补充战绩'))
        first.refresh_from_db(); self.assertEqual(first.status, 'REJECTED')
        self.login(self.provider)
        self.assertIsNone(self.current(order, old)['segment_report']['submission']['freeze_until'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id':old.pk, 'completion_image':flow.screenshot()}, format='multipart'))
        self.ok(self.act(order, old, 'submit', 'submit-old-2', version=1))
        second = report.submissions.get(version=2)
        first.refresh_from_db()
        self.assertEqual((first.entry_image.name, first.completion_image.name, first.result_images.get().image.name), before)
        self.assertNotEqual(second.completion_image.name, first.completion_image.name)
        self.assertGreaterEqual(second.submitted_at, first.submitted_at)
        info = self.current(order, old)['segment_report']['submission']
        self.assertEqual(info['freeze_until'], second.submitted_at + timedelta(hours=72))
        stale = self.review(order, old, 'approve', 1)
        self.assertEqual(stale.status_code, 409)
        self.ok(self.review(order, old, 'approve', 2))
        report.refresh_from_db(); self.assertEqual(report.status, 'APPROVED')
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_owner_and_review_permissions_and_evidence_requirements(self):
        order, replacement, old = self.prepare()
        self.login(replacement)
        denied = self.act(order, old, 'open', 'foreign-open')
        self.assertEqual(denied.status_code, 404)
        self.login(self.provider)
        self.ok(self.act(order, old, 'open', 'open-owner'))
        missing = self.act(order, old, 'submit', 'missing-image', version=0)
        self.assertNotEqual(missing.data['code'], 0)
        self.ok(self.act(order, old, 'upload', 'upload-owner', image=flow.screenshot()))
        self.ok(self.act(order, old, 'submit', 'submit-owner', version=0))
        self.login(replacement)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertNotIn(old.pk, [row['id'] for row in detail['my_participation_segments']])
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user
        staff = make_console_user(['order:view', 'report:view'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        denied = self.client.post(f'/api/admin/orders/{order.pk}/segments/{old.pk}/report-review/',
                                  {'action':'approve', 'version':1}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(OrderSegmentReport.objects.get(segment=old).status, 'PENDING')

    def test_withdraw_and_replay_preserve_first_submission_version(self):
        order, replacement, old = self.prepare()
        self.ok(self.act(order, old, 'open', 'open-withdraw'))
        self.ok(self.act(order, old, 'upload', 'upload-withdraw', image=flow.screenshot()))
        self.ok(self.act(order, old, 'submit', 'submit-withdraw', version=0))
        self.ok(self.act(order, old, 'withdraw', 'withdraw-version-1', version=1))
        report = OrderSegmentReport.objects.get(segment=old)
        self.assertEqual((report.status, report.submissions.get(version=1).status), ('DRAFT','WITHDRAWN'))
        self.login(self.admin)
        detail = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        history = next(row for row in detail['original_attribution']['segments'] if row['id'] == old.pk)['segment_report']
        self.assertEqual(history['history'][0]['status'], 'WITHDRAWN')
        self.assertEqual(history['draft_images'], [])
        self.login(self.provider)
        self.ok(self.act(order, old, 'submit', 'submit-after-withdraw', version=1))
        report.refresh_from_db(); self.assertEqual((report.status, report.version), ('PENDING',2))
        self.assertEqual(report.submissions.count(), 2)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_returned_provider_segments_share_last_valid_submission_clock(self):
        order, replacement, old = self.prepare()
        self.login(replacement)
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':2000, 'transferred':4000, 'agreement':'双方确认份额'}))
        self.login(self.provider)
        returned = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=True)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':returned.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart'))
        for segment in (old, returned):
            self.ok(self.act(order, segment, 'open', f'clock-open-{segment.pk}'))
            self.ok(self.act(order, segment, 'upload', f'clock-upload-{segment.pk}', image=flow.screenshot()))
        self.ok(self.act(order, old, 'submit', 'clock-submit-first', version=0))
        self.assertIsNone(self.current(order, old)['segment_report']['submission']['freeze_until'])
        self.ok(self.act(order, returned, 'submit', 'clock-submit-last', version=0))
        last = OrderSegmentReportSubmission.objects.get(report__segment=returned, version=1)
        for segment in (old, returned):
            self.assertEqual(self.current(order, segment)['segment_report']['submission']['freeze_until'],
                             last.submitted_at + timedelta(hours=72))
        self.ok(self.act(order, returned, 'withdraw', 'clock-withdraw-last', version=1))
        self.assertIsNone(self.current(order, old)['segment_report']['submission']['freeze_until'])
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
