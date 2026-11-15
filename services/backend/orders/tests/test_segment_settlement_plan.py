from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import AfterSale, OrderParticipationSegment, OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis
from orders.segment_settlement_plan import build_segment_settlement_plan
from orders.settlement import SettlementError
from orders.tests import test_release_flow as flow
from orders.tests import test_segment_reports as reports_fixture
from wallet.models import Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                   PRESTART_SCOPE_SNAPSHOT_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class SegmentSettlementPlanTest(APITransactionTestCase):
    setUp = reports_fixture.SegmentReportsTest.setUp
    login = reports_fixture.SegmentReportsTest.login
    ok = reports_fixture.SegmentReportsTest.ok
    recharge = reports_fixture.SegmentReportsTest.recharge
    new_order = reports_fixture.SegmentReportsTest.new_order
    replacement = reports_fixture.SegmentReportsTest.replacement
    transfer = reports_fixture.SegmentReportsTest.transfer
    prepare = reports_fixture.SegmentReportsTest.prepare
    act = reports_fixture.SegmentReportsTest.act
    review = reports_fixture.SegmentReportsTest.review

    def finished_order(self):
        order, replacement, old = self.prepare()
        self.login(replacement)
        current = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':current.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db()
        return order, replacement, old, current

    def submit_and_approve(self, order, segment, user):
        self.login(user)
        self.ok(self.act(order, segment, 'open', f'plan-open-{segment.pk}'))
        self.ok(self.act(order, segment, 'upload', f'plan-upload-{segment.pk}', image=flow.screenshot()))
        self.ok(self.act(order, segment, 'submit', f'plan-submit-{segment.pk}', version=0))
        self.ok(self.review(order, segment, 'approve', 1))

    def test_preview_calculates_segment_shares_and_independent_readiness_without_credit(self):
        order, replacement, old, current = self.finished_order()
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, current, replacement)
        future = timezone.now() + timedelta(hours=100)
        plan = build_segment_settlement_plan(order, now=future)
        self.assertEqual([(line.basis, line.fee, line.income) for line in plan.segments],
                         [(4000, 800, 3200), (6000, 1200, 4800)])
        self.assertEqual((plan.store_income, plan.inviter_income), (2000, 0))
        self.assertTrue(all(not person.blockers for person in plan.persons))
        AfterSale.objects.create(order=order, account=order.customer_account, reason='核对',
            affected_provider_account_ids=[self.accounts[self.provider.pk].pk])
        plan = build_segment_settlement_plan(order, now=future)
        statuses = {person.account_id: person.blockers for person in plan.persons}
        self.assertIn('本人相关售后尚未结束', statuses[self.accounts[self.provider.pk].pk])
        self.assertFalse(statuses[self.accounts[replacement.pk].pk])
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/segment-settlement-plan/'))
        self.assertEqual([line['income'] for line in preview['segments']], [3200, 4800])
        self.login(self.provider)
        self.assertEqual(self.client.get(f'/api/admin/orders/{order.pk}/segment-settlement-plan/').status_code, 403)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_changed_or_missing_financial_facts_reject_the_plan(self):
        order, replacement, old, current = self.finished_order()
        allocation = OrderSegmentTransferBasis.objects.get(transfer__previous_segment=old)
        allocation.remainder_numerator += 1; allocation.save(update_fields=['remainder_numerator'])
        with self.assertRaisesMessage(SettlementError, '尾差不一致'):
            build_segment_settlement_plan(order)
        allocation.remainder_numerator -= 1; allocation.save(update_fields=['remainder_numerator'])
        OrderSegmentFinancialSnapshot.objects.filter(segment=current).delete()
        with self.assertRaisesMessage(SettlementError, '快照缺失'):
            build_segment_settlement_plan(order)

    def test_existing_settlement_is_never_presented_as_a_new_unpaid_plan(self):
        order, replacement, old, current = self.finished_order()
        order.providers.filter(provider_account=self.accounts[self.provider.pk]).update(settled_at=timezone.now())
        with self.assertRaisesMessage(SettlementError, '已有收益入账或结算记录'):
            build_segment_settlement_plan(order)

    def test_referral_uses_main_paid_amount_and_payment_snapshot_rate(self):
        order, replacement, old, current = self.finished_order()
        order.inviter = self.provider
        order.inviter_account = self.accounts[self.provider.pk]
        order.inviter_commission_rate_snapshot = 10
        order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot'])
        plan = build_segment_settlement_plan(order)
        self.assertEqual((plan.inviter_income, plan.store_income), (1000, 1000))
        self.assertEqual(sum(item.income for item in plan.segments) + plan.inviter_income + plan.store_income,
                         plan.deal_amount)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_returned_person_is_grouped_without_losing_distinct_segments(self):
        order, replacement, old = self.prepare()
        middle = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':middle.pk, 'entry_image':flow.screenshot()}, format='multipart'))
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
        plan = build_segment_settlement_plan(order)
        self.assertEqual([(x.basis, x.income) for x in plan.segments], [(4000, 3200), (2000, 1600), (4000, 3200)])
        people = {p.account_id: p for p in plan.persons}
        first = people[self.accounts[self.provider.pk].pk]
        self.assertEqual((first.segment_ids, first.income), ((old.pk, returned.pk), 6400))
        self.assertIn('本人履约报单未全部通过审核', first.blockers)
        self.submit_and_approve(order, old, self.provider)
        self.submit_and_approve(order, returned, self.provider)
        from orders.models import OrderSegmentReportSubmission
        submissions = list(OrderSegmentReportSubmission.objects.filter(report__segment_id__in=[old.pk, returned.pk]))
        plan = build_segment_settlement_plan(order, now=timezone.now() + timedelta(hours=100))
        people = {p.account_id: p for p in plan.persons}
        first = people[self.accounts[self.provider.pk].pk]
        self.assertEqual(first.freeze_until, max(s.submitted_at for s in submissions) + timedelta(hours=72))
        self.assertFalse(first.blockers)
        self.assertIn('本人履约报单未全部通过审核', people[self.accounts[replacement.pk].pk].blockers)
        latest = next(s for s in submissions if s.report.segment_id == returned.pk)
        latest.completion_image.name = 'reports/participation/wrong.png'
        latest.save(update_fields=['completion_image'])
        plan = build_segment_settlement_plan(order, now=timezone.now() + timedelta(hours=100))
        self.assertIn('本人履约报单未全部通过审核',
                      next(p.blockers for p in plan.persons if p.account_id == self.accounts[self.provider.pk].pk))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_changed_fixed_rules_and_returning_person_use_separate_snapshots(self):
        self.service.price = 9000; self.service.commission_override = True
        self.service.commission_type = 'FIXED'; self.service.commission_fixed = 2000
        self.service.save(update_fields=['price', 'commission_override', 'commission_type', 'commission_fixed'])
        replacement = self.replacement(); order = self.new_order()
        order.service_clock_version = 1; order.service_freeze_hours = 72; order.service_aftersale_hours = 72
        order.save(update_fields=['service_clock_version', 'service_freeze_hours', 'service_aftersale_hours'])
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.service.commission_fixed = 2030; self.service.save(update_fields=['commission_fixed'])
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':3000, 'transferred':6000, 'agreement':'双方确认份额'}))
        incoming = OrderParticipationSegment.objects.get(attribution__order=order,
            provider_account=self.accounts[replacement.pk], released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        plan = build_segment_settlement_plan(order)
        self.assertEqual([(line.basis, line.fee, line.income) for line in plan.segments],
                         [(3000, 666, 2334), (6000, 1354, 4646)])
        self.assertEqual(plan.store_income, 2020)
        self.assertTrue(all('整单尚未完成' in person.blockers for person in plan.persons))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
