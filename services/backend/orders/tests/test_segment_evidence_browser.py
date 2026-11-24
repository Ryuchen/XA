import os
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import OrderParticipationSegment, OrderSegmentReport
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.services import get_wallet
from wallet.models import Transaction


@skipUnless(os.environ.get('XA_SEGMENT_EVIDENCE_BROWSER') == '1', 'Requires isolated ASGI and provider Vite')
@override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class SegmentEvidenceBrowserTest(LiveServerTestCase):
    def test_returned_provider_sees_distinct_entry_and_completion_evidence(self):
        boss = make_user(); buyer = get_or_create_account_for_legacy_user(boss)
        wallet = get_wallet(account=buyer, user=boss); wallet.balance = 20000; wallet.save(update_fields=['balance'])
        players = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
        accounts = [get_or_create_account_for_legacy_user(player) for player in players]
        for player, account in zip(players, accounts):
            player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000)
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit', 'order:refund'])
        operator = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(buyer)[0])
        with patch('orders.views._schedule_auto_cancel'):
            created = client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[0].pk,
                'client_request_id':'segment-evidence-browser'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        def auth(account):
            client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        auth(accounts[0])
        started = client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        def transfer(old, new, retained, transferred):
            auth(operator)
            result = client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'隔离验证', 'transfers':[{
                'old_provider_id':old.pk, 'new_provider_id':new.pk,
                'original_share':{'retained':retained, 'transferred':transferred, 'agreement':'双方确认份额'}}]}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        def enter(account):
            segment = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
                                                               provider_account=account, released_at__isnull=True)
            auth(account)
            result = client.post(f'/api/orders/orders/{order_id}/participation-start/',
                {'segment_id':segment.pk, 'entry_image':screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
        transfer(accounts[0], accounts[1], 4000, 6000); enter(accounts[1])
        transfer(accounts[1], accounts[0], 2000, 4000); enter(accounts[0])
        completed = client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image':screenshot()}, format='multipart')
        self.assertEqual(completed.data['code'], 0, completed.data)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
        previous = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
            provider_account=accounts[0], released_at__isnull=False)
        current = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
            provider_account=accounts[0], released_at__isnull=True)
        auth(accounts[0])
        evidence = client.post(f'/api/orders/orders/{order_id}/participation-evidence/',
            {'segment_id': previous.pk, 'completion_image': screenshot()}, format='multipart')
        self.assertEqual(evidence.data['code'], 0, evidence.data)
        if settings.SEGMENT_REPORT_ENABLED:
            for segment in (previous, current):
                opened = client.post(f'/api/orders/orders/{order_id}/segment-report/',
                    {'segment_id': segment.pk, 'action': 'open',
                     'client_request_id': f'browser-open-{segment.pk}'}, format='json')
                self.assertEqual(opened.data['code'], 0, opened.data)
                uploaded = client.post(f'/api/orders/orders/{order_id}/segment-report/',
                    {'segment_id': segment.pk, 'action': 'upload', 'image': screenshot(),
                     'client_request_id': f'browser-upload-{segment.pk}'}, format='multipart')
                self.assertEqual(uploaded.data['code'], 0, uploaded.data)
                submitted = client.post(f'/api/orders/orders/{order_id}/segment-report/',
                    {'segment_id': segment.pk, 'action': 'submit', 'version': 0,
                     'client_request_id': f'browser-submit-{segment.pk}'}, format='json')
                self.assertEqual(submitted.data['code'], 0, submitted.data)
            auth(operator)
            rejected = client.post(f'/api/admin/orders/{order_id}/segments/{previous.pk}/report-review/',
                {'action': 'reject', 'version': 1, 'remark': '请补充战绩'}, format='json')
            self.assertEqual(rejected.data['code'], 0, rejected.data)
            approved = client.post(f'/api/admin/orders/{order_id}/segments/{current.pk}/report-review/',
                {'action': 'approve', 'version': 1}, format='json')
            self.assertEqual(approved.data['code'], 0, approved.data)
        if settings.SEGMENT_REFUND_PREVIEW_ENABLED:
            auth(operator)
            preview = client.post(f'/api/admin/orders/{order_id}/segment-refund-preview/',
                {'refund_amount': 510}, format='json')
            self.assertEqual(preview.data['code'], 0, preview.data)
            narrowed = client.post(f'/api/admin/orders/{order_id}/segment-refund-preview/',
                {'refund_amount': 510, 'affected_provider_account_ids': [accounts[0].pk]}, format='json')
            self.assertEqual(narrowed.data['code'], 0, narrowed.data)
        if settings.SEGMENT_SETTLEMENT_ENABLED:
            from datetime import timedelta
            from django.utils import timezone
            from engagement.confirmation import process_order
            from orders.models import Order, OrderSegmentPersonSettlement, OrderSegmentReportSubmission
            previous = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
                provider_account=accounts[0], released_at__isnull=False)
            auth(accounts[0])
            resubmitted = client.post(f'/api/orders/orders/{order_id}/segment-report/',
                {'segment_id':previous.pk, 'action':'submit', 'version':1,
                 'client_request_id':'browser-resubmit-after-reject'}, format='json')
            self.assertEqual(resubmitted.data['code'], 0, resubmitted.data)
            auth(operator)
            approved = client.post(f'/api/admin/orders/{order_id}/segments/{previous.pk}/report-review/',
                {'action':'approve', 'version':2}, format='json')
            self.assertEqual(approved.data['code'], 0, approved.data)
            now = timezone.now()
            Order.objects.filter(pk=order_id).update(completed_at=now-timedelta(hours=100))
            OrderSegmentReportSubmission.objects.filter(report__segment__attribution__order_id=order_id,
                status='APPROVED').update(submitted_at=now-timedelta(hours=99), reviewed_at=now-timedelta(hours=98))
            if settings.SEGMENT_REFUND_PREVIEW_ENABLED:
                from orders.models import AfterSale
                case = AfterSale.objects.create(order_id=order_id, account=buyer, reason='核对接替陪玩',
                    affected_provider_account_ids=[accounts[1].pk])
                AfterSale.objects.filter(pk=case.pk).update(created_at=now-timedelta(hours=90))
            self.assertFalse(process_order(order_id))  # Other person's report remains unsubmitted.
            self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order_id=order_id,
                account=accounts[0]).count(), 1)
            if settings.SEGMENT_REFUND_PREVIEW_ENABLED:
                auth(operator)
                locked = client.post(f'/api/admin/orders/{order_id}/segment-refund-preview/',
                    {'refund_amount': 510}, format='json')
                self.assertEqual(locked.data['code'], 0, locked.data)
                self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        previous = OrderParticipationSegment.objects.get(attribution__order_id=order_id,
            provider_account=accounts[0], released_at__isnull=False)
        self.assertTrue(previous.completion_image)
        if settings.SEGMENT_REPORT_ENABLED and not settings.SEGMENT_SETTLEMENT_ENABLED:
            self.assertEqual(list(OrderSegmentReport.objects.filter(segment__attribution__order_id=order_id,
                segment__provider_account=accounts[0]).order_by('segment_id').values_list('status', flat=True)), ['REJECTED', 'APPROVED'])
        if not settings.SEGMENT_SETTLEMENT_ENABLED:
            self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
