from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order, OrderOriginalShareTransfer, OrderParticipationSegment, OrderSegmentFinancialSnapshot
from orders.tests import test_provider_transfer_ranking as fixture
from orders.tests import test_release_flow as flow
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=False, DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True,
    ORDINARY_SERVICE_FUNDING_ENABLED=True, PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_ORDINARY_FUNDING_TESTING=True, SERVICE_ORDINARY_TRANSFER_FUNDING_TESTING=True,
    ORDINARY_COMPLETION_TRANSFER_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class TeamPrincipalTransferSettlementTest(APITestCase):
    setUp = fixture.UnknownTeamTransferRankingTest.setUp
    auth = fixture.UnknownTeamTransferRankingTest.auth
    ok = fixture.UnknownTeamTransferRankingTest.ok
    create_specified = fixture.UnknownTeamTransferRankingTest.create_specified

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_ECONOMICS_CAPTURE_ENABLED=False,
                       PRESTART_SCOPE_SNAPSHOT_ENABLED=False, DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=True)
    def test_team_transfer_closes_only_after_each_report_and_wait(self):
        from engagement.confirmation import process_order
        from orders.services import settle_order
        self.service.commission_type = 'PERCENT'
        self.service.commission_rate = 20
        self.service.commission_override = True
        self.service.save(update_fields=['commission_type', 'commission_rate', 'commission_override'])
        first, second, incoming = self.providers
        order = self.create_specified([first, second])
        from orders.models import OrderOriginalAttribution, OrderPaidServiceScope, OrderSegmentFinancialCoverage, OrderWorkOriginalShare
        from engagement.models import DefaultPaymentSource
        self.assertTrue(OrderPaidServiceScope.objects.filter(order=order).exists())
        attribution = OrderOriginalAttribution.objects.get(order=order, status='ACTIVE')
        self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution=attribution).deal_amount, order.amount)
        self.assertEqual(order.funding_allocations.filter(kind='PREPAID').count(), 1)
        self.assertFalse(DefaultPaymentSource.objects.filter(snapshot__order=order).exists())
        self.assertFalse(OrderWorkOriginalShare.objects.filter(stint__order=order).exists())
        self.enterContext(override_settings(DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=False,
            DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=False, ORDINARY_SERVICE_FUNDING_ENABLED=False,
            SEGMENT_STANDARD_TRANSFER_ENABLED=False))
        for player in (first, second):
            self.auth(player)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': flow.screenshot()}, format='multipart'))
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_rate'])
        self.auth(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '双方确认转单',
            'transfers': [{'old_provider_id': self.accounts[first.pk].pk,
                'new_provider_id': self.accounts[incoming.pk].pk,
                'original_share': {'retained': 2000, 'transferred': 3000, 'agreement': '双方确认原价份额'}}]}, format='json'))
        event = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order=order)
        old, current = event.previous_segment, event.next_segment
        other = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[second.pk], released_at__isnull=True)
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.get(segment=current).commission_rate, 30)
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.get(segment=old).commission_rate, 20)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.auth(first)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/',
            {'segment_id': old.pk, 'completion_image': flow.screenshot()}, format='multipart'))
        self.auth(incoming)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id': current.pk, 'entry_image': flow.screenshot(), 'client_request_id': 'team-transfer-entry'}, format='multipart'))
        for player in (second, incoming):
            self.auth(player)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': flow.screenshot()}, format='multipart'))
        for segment, player in ((old, first), (other, second), (current, incoming)):
            self.auth(player)
            for action, values in (('open', {}), ('upload', {'image': flow.screenshot()}), ('submit', {'version': 0})):
                self.ok(self.client.post(f'/api/orders/orders/{order.pk}/segment-report/',
                    {'segment_id': segment.pk, 'action': action, 'client_request_id': f'team-{action}-{segment.pk}', **values},
                    format='multipart' if action == 'upload' else 'json'))
            self.auth(self.staff)
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/segments/{segment.pk}/report-review/',
                {'action': 'approve', 'version': 1}, format='json'))
        def settle():
            with transaction.atomic():
                return settle_order(Order.objects.select_for_update().get(pk=order.pk))
        with store_context(resolve_store()):
            self.assertEqual(settle(), [])
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
                self.assertCountEqual(settle(), [player.pk for player in (first, second, incoming)])
                self.assertEqual(settle(), [])
                self.assertTrue(process_order(order.pk))
        credits = Transaction.objects.filter(order=order, tx_type='INCOME')
        self.assertCountEqual(credits.values_list('wallet__account_id', 'amount'),
            [(self.accounts[first.pk].pk, 1600), (self.accounts[second.pk].pk, 4000),
             (self.accounts[incoming.pk].pk, 2100)])
