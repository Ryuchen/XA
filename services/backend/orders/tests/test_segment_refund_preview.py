from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import AfterSale, OrderParticipationSegment, OrderSegmentPersonSettlement
from orders.refunds import RefundError, build_refund_plan
from orders.tests import test_release_flow as flow
from orders.tests import test_segment_settlement_credit as fixture
from wallet.models import Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                   SEGMENT_REFUND_PREVIEW_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentRefundPreviewTest(APITransactionTestCase):
    setUp = fixture.SegmentSettlementCreditTest.setUp
    login = fixture.SegmentSettlementCreditTest.login
    ok = fixture.SegmentSettlementCreditTest.ok
    recharge = fixture.SegmentSettlementCreditTest.recharge
    new_order = fixture.SegmentSettlementCreditTest.new_order
    replacement = fixture.SegmentSettlementCreditTest.replacement
    transfer = fixture.SegmentSettlementCreditTest.transfer
    prepare = fixture.SegmentSettlementCreditTest.prepare
    act = fixture.SegmentSettlementCreditTest.act
    review = fixture.SegmentSettlementCreditTest.review
    finished_order = fixture.SegmentSettlementCreditTest.finished_order
    submit_and_approve = fixture.SegmentSettlementCreditTest.submit_and_approve
    mature = fixture.SegmentSettlementCreditTest.mature
    settle = fixture.SegmentSettlementCreditTest.settle

    def preview(self, order, amount, **extra):
        self.login(self.admin)
        return self.client.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                                {'refund_amount':amount, **extra}, format='json')

    def test_first_refund_allocates_once_per_person_and_rounds_each_up(self):
        order, replacement, old, current = self.finished_order()
        before = Transaction.objects.filter(order=order).count()
        result = self.ok(self.preview(order, 510))
        rows = {row['account_id']:row for row in result['burdens']}
        self.assertEqual((rows[self.accounts[self.provider.pk].pk]['burden'],
                          rows[self.accounts[replacement.pk].pk]['burden'], result['rounding_to_store']), (210, 310, 10))
        self.assertEqual([row['pending_income'] for row in result['burdens']], [210, 310])
        self.assertTrue(result['estimated_sufficient'])
        self.assertFalse(result['executable'])
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)
        self.assertFalse(order.refund_records.exists())
        with self.assertRaisesMessage(RefundError, '本单逐段退款分摊及资金处理待核对'):
            build_refund_plan(order, refund_amount=510)
        # A malformed historical parent timestamp cannot bypass the guard
        # when the actual participation records prove a service transfer.
        order.in_service_at = None; order.save(update_fields=['in_service_at'])
        with self.assertRaisesMessage(RefundError, '本单逐段退款分摊及资金处理待核对'):
            build_refund_plan(order, refund_amount=510)

    def test_existing_affected_case_preserves_paid_other_person(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.mature(order)
        case = AfterSale.objects.create(order=order, account=order.customer_account, reason='仅核对接替陪玩',
            affected_provider_account_ids=[self.accounts[replacement.pk].pk])
        AfterSale.objects.filter(pk=case.pk).update(created_at=timezone.now()-timedelta(hours=90))
        self.assertEqual(self.settle(order), [self.provider.pk])
        result = self.ok(self.preview(order, 510))
        self.assertEqual(result['scope'], [self.accounts[replacement.pk].pk])
        self.assertEqual((result['burdens'][0]['burden'], result['burdens'][0]['pending_income'],
                          result['burdens'][0]['already_settled']), (510, 510, False))
        denied = self.preview(order, 510, affected_provider_account_ids=[self.accounts[self.provider.pk].pk])
        self.assertEqual(denied.data['errorCode'], 'SEGMENT_REFUND_SCOPE_INVALID')
        self.assertEqual(OrderSegmentPersonSettlement.objects.filter(manifest__order=order).count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)

    def test_returns_are_grouped_before_rounding_and_scope_is_authorized(self):
        order, replacement, old = self.prepare()
        current = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':current.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':2000, 'transferred':4000, 'agreement':'双方确认转回'}))
        returned = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[self.provider.pk], released_at__isnull=True)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':returned.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        result = self.ok(self.preview(order, 510))
        rows = {row['account_id']:row for row in result['burdens']}
        self.assertEqual((rows[self.accounts[self.provider.pk].pk]['original_share'],
                          rows[self.accounts[self.provider.pk].pk]['burden'],
                          rows[self.accounts[replacement.pk].pk]['burden']), (8000, 410, 110))
        self.assertEqual(result['rounding_to_store'], 10)
        self.login(self.provider)
        denied = self.client.post(f'/api/admin/orders/{order.pk}/segment-refund-preview/',
                                  {'refund_amount':510}, format='json')
        self.assertEqual(denied.status_code, 403)

    def test_expired_window_fractional_coin_and_invalid_scope_rejected(self):
        order, replacement, old, current = self.finished_order()
        self.assertEqual(self.preview(order, 1).data['errorCode'], 'SEGMENT_REFUND_AMOUNT_INVALID')
        self.assertEqual(self.preview(order, 10, affected_provider_account_ids=[999999]).data['errorCode'],
                         'SEGMENT_REFUND_SCOPE_INVALID')
        self.mature(order)
        self.assertEqual(self.preview(order, 10).data['errorCode'], 'SEGMENT_REFUND_EXPIRED')

    def test_preview_shows_shortfall_after_pending_wallet_and_deposit(self):
        from wallet.services import get_wallet
        order, replacement, old, current = self.finished_order()
        wallet = get_wallet(account=self.accounts[replacement.pk], user=replacement)
        wallet.balance = 300; wallet.frozen_amount = 5000
        wallet.save(update_fields=['balance', 'frozen_amount'])
        profile = replacement.escort_profile
        profile.deposit_paid = 700; profile.save(update_fields=['deposit_paid'])
        result = self.ok(self.preview(order, 6000,
            affected_provider_account_ids=[self.accounts[replacement.pk].pk]))
        row = result['burdens'][0]
        self.assertEqual((row['pending_income'], row['wallet_amount'], row['deposit_amount'], row['shortfall']),
                         (4800, 300, 700, 200))
        self.assertFalse(result['estimated_sufficient'])
        self.assertFalse(result['executable'])
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount, profile.deposit_paid), (300, 5000, 700))

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=True)
    def test_source_plan_reads_original_principal_without_changing_funding_lots(self):
        order, replacement, old, current = self.finished_order()
        self.assertIsNotNone(order.funding_terms)
        before = list(order.funding_allocations.order_by('pk').values_list('amount', 'refunded_amount'))
        result = self.ok(self.preview(order, 510))
        self.assertEqual(result['payment_sources'], [{'kind':'PREPAID', 'amount':510}])
        self.assertEqual(list(order.funding_allocations.order_by('pk').values_list('amount', 'refunded_amount')), before)

    @override_settings(BOSS_FUNDING_SELECTION_ENABLED=True)
    def test_source_plan_returns_principal_before_gift(self):
        from engagement.models import PointFundingLot
        from orders.models import Order
        from wallet.services import get_wallet
        replacement = self.replacement()
        self.ok(self.recharge())
        wallet = get_wallet(account=self.accounts[self.boss.pk], user=self.boss)
        wallet.balance += 1000; wallet.save(update_fields=['balance'])
        PointFundingLot.objects.create(account=self.accounts[self.boss.pk], source_key='segment-preview-gift-fixture',
                                       kind='GIFT', remaining=1000)
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'gift_balance_amount':1000,
            'client_request_id':'segment-source-gift'}, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认份额'}))
        current = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':current.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        result = self.ok(self.preview(order, 9500))
        self.assertEqual(result['payment_sources'], [{'kind':'PREPAID', 'amount':9000}, {'kind':'GIFT', 'amount':500}])
