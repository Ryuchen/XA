import os
from unittest import skipUnless
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from orders.grab_original_facts import original_amount_facts
from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide
from orders.tests.test_addon_handover import AddonHandoverTests


@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_HANDOVER_INTERNAL_TESTING=True,
                   ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'])
class GrabAddonHandoverFactsTest(TestCase):
    setUp = AddonHandoverTests.setUp
    create = AddonHandoverTests.create
    agree = AddonHandoverTests.agree

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_actual_refunds_before_and_after_handover_count_once_per_person(self):
        from orders.models import AfterSale, OrderAddonRefund
        from orders.tests.test_addon_configuration import staff_client
        self.fixture.order.original_amount = 10000
        self.fixture.order.save(update_fields=['original_amount'])
        order_id, payment_id = self.fixture.order.pk, self.payment_id
        first, second = self.fixture.provider.pk, self.second.pk
        staff = staff_client(['order:view', 'order:refund', 'order:settle', 'support:edit'])
        staff_url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
        case = AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='加购协商退款')
        def refund(request_id, amount, people, clients):
            response = staff.post(staff_url, {'request_id': request_id, 'amount_raw': amount,
                'explanation': '协商退款', 'affected_provider_account_ids': people}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            proposal = response.data['data']['id']
            for client in clients:
                result = client.post(user_url + f'{proposal}/', {'accepted': True}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
            result = staff.post(staff_url + f'{proposal}/execute/', {}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            return OrderAddonRefund.objects.get(proposal_id=proposal)
        old = timezone.now()
        first_refund = refund('before-handover', 100, [first], [self.fixture.client, self.fixture.provider_client])
        after_first = timezone.now()
        self.assertEqual(original_amount_facts([first, second], as_of=old)[first].amount, 6500)
        self.assertEqual(original_amount_facts([first, second], as_of=after_first)[first].amount, 6400)
        result = staff.post(f'/api/admin/aftersales/{case.pk}/resolve/', {'resolution': '退款完成'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        proposal = self.create()
        for response in self.agree(proposal['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        after_handover = timezone.now()
        facts = original_amount_facts([first, second], as_of=after_handover)
        self.assertEqual((facts[first].amount, facts[second].amount), (5600, 5800))
        reopened = staff.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'reason': '加购共同退款', 'affected_provider_account_ids': [first, second],
            'client_request_id': 'addon-second-case'}, format='json')
        self.assertEqual(reopened.status_code, 200, reopened.data)
        second_refund = refund('after-handover', 10, [first, second],
                               [self.fixture.client, self.fixture.provider_client, self.second_client])
        self.assertEqual((first_refund.rounding_surplus, second_refund.rounding_surplus), (0, 10))
        facts = original_amount_facts([first, second], as_of=timezone.now())
        self.assertEqual((facts[first].amount, facts[second].amount), (5590, 5790))
        after_second = timezone.now()
        third_refund = refund('after-handover-again', 10, [first, second],
                              [self.fixture.client, self.fixture.provider_client, self.second_client])
        self.assertEqual(third_refund.rounding_surplus, 10)
        latest = original_amount_facts([first, second], as_of=timezone.now())
        self.assertEqual((latest[first].amount, latest[second].amount), (5580, 5780))
        middle = original_amount_facts([first, second], as_of=after_second)
        self.assertEqual((middle[first].amount, middle[second].amount), (5590, 5790))
        prior = original_amount_facts([first, second], as_of=after_handover)
        self.assertEqual((prior[first].amount, prior[second].amount), (5600, 5800))

    def test_refund_allocation_uses_each_of_same_players_separate_acceptance_dates(self):
        from orders.models import (AfterSale, OrderOriginalShareTransfer, OrderParticipationSegment,
                                   OrderSegmentFinancialSnapshot, OrderAddonRefundParticipant)
        from orders.tests.test_addon_configuration import staff_client
        self.fixture.order.original_amount = 10000
        self.fixture.order.save(update_fields=['original_amount'])
        first, second = self.fixture.provider.pk, self.second.pk
        # Preserve an earlier completed stint and create the same player's next
        # main-order stint at a later time; add-on handovers below are real APIs.
        old = self.fixture.segment
        old.accepted_at = timezone.now() - timedelta(days=31)
        old.started_at = old.accepted_at + timedelta(minutes=1)
        old.released_at = timezone.now() - timedelta(seconds=1)
        old.save(update_fields=['accepted_at', 'started_at', 'released_at'])
        newer = OrderParticipationSegment.objects.create(attribution=old.attribution, provider_account_id=first,
            slot=old.slot, original_share=0, accepted_at=old.released_at, started_at=old.released_at)
        OrderOriginalShareTransfer.objects.create(previous_segment=old, next_segment=newer,
            original_share_before=5000, retained_share=5000, transferred_share=0,
            operator=self.fixture.boss, agreement='履约记录测试', created_at=old.released_at)
        OrderSegmentFinancialSnapshot.objects.create(segment=newer, incoming_basis=0,
            commission_type='PERCENT', commission_rate=20, commission_fixed=0, income_at_entry=0)
        proposal = self.create()
        for response in self.agree(proposal['id'], [self.fixture.client, self.fixture.provider_client, self.second_client]):
            self.assertEqual(response.status_code, 200, response.data)
        proposal = self.create({'request_id': 'back-to-first', 'target_provider_account_id': first,
            'retained_basis_raw': 400, 'completed_work': '已完成一部分', 'remaining_work': '甲继续完成'})
        for response in self.agree(proposal['id'], [self.fixture.client, self.second_client, self.fixture.provider_client]):
            self.assertEqual(response.status_code, 200, response.data)
        before = original_amount_facts([first, second], as_of=timezone.now())
        self.assertEqual((before[first].amount, before[second].amount), (500, 5400))
        AfterSale.objects.create(order=self.fixture.order, account=self.fixture.boss, reason='加购共同退款')
        staff = staff_client(['order:view', 'order:refund', 'order:settle'])
        staff_url = f'/api/admin/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        user_url = f'/api/orders/orders/{self.fixture.order.pk}/addons/payments/{self.payment_id}/refund-proposals/'
        response = staff.post(staff_url, {'request_id': 'split-stints', 'amount_raw': 10,
            'explanation': '共同退款', 'affected_provider_account_ids': [first, second]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        pid = response.data['data']['id']
        for client in (self.fixture.client, self.fixture.provider_client, self.second_client):
            result = client.post(user_url + f'{pid}/', {'accepted': True}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        result = staff.post(staff_url + f'{pid}/execute/', {}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        person = OrderAddonRefundParticipant.objects.get(proposal_id=pid, account_id=first)
        newest = next(item for item in person.share_plan if item['segment_id'] == newer.pk)
        after = original_amount_facts([first, second], as_of=timezone.now())
        self.assertEqual(after[first].amount, 500 - newest['burden'])
        self.assertEqual(after[second].amount, 5390)
        self.assertLess(newest['refund_basis'], newest['burden'])

    def test_repeated_rounding_excess_does_not_reduce_other_segments_twice(self):
        self.fixture.order.original_amount = 10000
        self.fixture.order.save(update_fields=['original_amount'])
        # Reuse the real endpoint flow: payment, agreed handover and two refunds.
        AddonHandoverTests.test_repeated_small_refunds_do_not_reuse_original_burden_capacity(self)
        facts = original_amount_facts([self.fixture.provider.pk, self.second.pk], as_of=timezone.now())
        self.assertEqual((facts[self.fixture.provider.pk].amount, facts[self.second.pk].amount), (5000, 6470))

    def test_public_directory_and_ranking_show_confirmed_handover_amounts(self):
        self.fixture.order.original_amount = 10000
        self.fixture.order.save(update_fields=['original_amount'])
        proposal = self.create()
        self.assertEqual([r.status_code for r in self.agree(proposal['id'],
            [self.fixture.provider_client, self.fixture.client, self.second_client])], [200, 200, 200])
        self.fixture.order.status = 'COMPLETED'
        self.fixture.order.completed_at = timezone.now()
        self.fixture.order.save(update_fields=['status', 'completed_at'])
        # This test synthesizes the parent order's completion; supply the
        # corresponding per-person facts explicitly for its ranking fixture.
        from orders.models import OrderProviderCompletionFact
        for segment in (self.fixture.segment, self.second_segment):
            OrderProviderCompletionFact.objects.create(order=self.fixture.order, segment=segment,
                provider_account=segment.provider_account, evidence='fixture/team-completion.png',
                completed_at=self.fixture.order.completed_at)
        first, second = self.fixture.provider.pk, self.second.pk
        for account, expected in [(first, 5600), (second, 5900)]:
            result = self.fixture.client.get(f'/api/users/escorts/{account}/')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['data']['monthly_order_amount'], expected)
        result = self.fixture.client.get('/api/orders/rankings/?period=month')
        self.assertEqual(result.status_code, 200, result.data)
        rows = {row['provider_account_id']: row for row in result.data['data']['order_rank']}
        self.assertEqual((rows[first]['order_count'], rows[first]['total_amount']), (1, 5600))
        self.assertEqual((rows[second]['order_count'], rows[second]['total_amount']), (1, 5900))

    def test_ranking_counts_same_player_order_once_even_when_fully_refunded(self):
        from orders.models import OrderParticipationSegment
        order = self.fixture.order
        now = timezone.now()
        old = self.fixture.segment
        old.released_at = now - timedelta(seconds=5)
        old.save(update_fields=['released_at'])
        OrderParticipationSegment.objects.create(attribution=old.attribution, provider_account=self.fixture.provider,
            slot=old.slot, original_share=0, accepted_at=now-timedelta(seconds=4),
            started_at=now-timedelta(seconds=3), released_at=now-timedelta(seconds=1))
        order.status, order.payment_status, order.cancelled_at = 'CANCELLED', 'REFUNDED', now
        order.save(update_fields=['status', 'payment_status', 'cancelled_at'])
        result = self.fixture.client.get('/api/orders/rankings/?period=month')
        self.assertEqual(result.status_code, 200, result.data)
        rows = {row['provider_account_id']: row for row in result.data['data']['order_rank']}
        self.assertEqual(rows[self.fixture.provider.pk]['order_count'], 1)

    def test_confirmed_reassignment_counts_only_latest_snapshot_and_original_acceptance(self):
        self.fixture.order.original_amount = 10000
        self.fixture.order.save(update_fields=['original_amount'])
        first, second = self.fixture.provider.pk, self.second.pk
        before = timezone.now()
        proposal = self.create()
        self.assertEqual([r.status_code for r in self.agree(proposal['id'],
            [self.fixture.provider_client, self.fixture.client, self.second_client])], [200, 200, 200])
        now = timezone.now()
        old = original_amount_facts([first, second], as_of=before)
        self.assertEqual((old[first].amount, old[second].amount), (6500, 5000))
        current = original_amount_facts([first, second], as_of=now)
        self.assertEqual((current[first].amount, current[second].amount), (5600, 5900))
        candidates = [SubmittedCandidate(1, (SubmittedMember(first, 'GOLD'),), now),
                      SubmittedCandidate(2, (SubmittedMember(second, 'GOLD'),), now)]
        self.assertEqual(decide(build_rankable_candidates(candidates, as_of=now)).winner_id, 2)
        later = self.create({'request_id': 'handover-2', 'target_provider_account_id': first,
                             'retained_basis_raw': 400, 'completed_work': '已完成一部分',
                             'remaining_work': '剩余服务交接'})
        self.assertEqual([r.status_code for r in self.agree(later['id'],
            [self.fixture.client, self.second_client, self.fixture.provider_client])], [200, 200, 200])
        now = timezone.now()
        latest = original_amount_facts([first, second], as_of=now)
        self.assertEqual((latest[first].amount, latest[second].amount), (6100, 5400))
        self.fixture.segment.accepted_at = now - timedelta(days=31)
        self.fixture.segment.started_at = self.fixture.segment.accepted_at + timedelta(minutes=1)
        self.fixture.segment.save(update_fields=['accepted_at', 'started_at'])
        latest = original_amount_facts([first, second], as_of=now)
        self.assertEqual((latest[first].amount, latest[second].amount), (0, 5400))
