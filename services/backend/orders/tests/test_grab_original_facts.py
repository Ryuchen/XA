from datetime import timedelta
from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.grab_original_facts import original_amount_facts
from orders.models import (Order, OrderOriginalAttribution, OrderOriginalShareTransfer,
                           OrderParticipationSegment, OrderProvider, OrderRefund)
from orders.tests import test_original_attribution as fixture
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_order, make_provider, make_user


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class GrabOriginalFactsFlowTest(APITransactionTestCase):
    setUp = fixture.OriginalAttributionFlowTest.setUp
    login = fixture.OriginalAttributionFlowTest.login
    ok = fixture.OriginalAttributionFlowTest.ok
    recharge = fixture.OriginalAttributionFlowTest.recharge
    new_order = fixture.OriginalAttributionFlowTest.new_order
    replacement = fixture.OriginalAttributionFlowTest.replacement
    transfer = fixture.OriginalAttributionFlowTest.transfer

    def test_actual_start_transfer_and_historical_snapshot(self):
        replacement = self.replacement()
        order = self.new_order()
        first, second = self.accounts[self.provider.pk], self.accounts[replacement.pk]
        before_start = timezone.now()
        self.assertEqual(original_amount_facts([first.pk, second.pk], as_of=before_start)[first.pk].amount, 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}))
        event = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order=order)
        before_transfer = event.created_at - timedelta(microseconds=1)
        facts = original_amount_facts([first.pk, second.pk], as_of=before_transfer)
        self.assertEqual((facts[first.pk].amount, facts[second.pk].amount), (10000, 0))
        after_transfer = timezone.now()
        facts = original_amount_facts([first.pk, second.pk], as_of=after_transfer)
        self.assertEqual((facts[first.pk].amount, facts[second.pk].amount), (4000, 0))
        segment = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=second,
                                                         released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':segment.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        facts = original_amount_facts([first.pk, second.pk], as_of=timezone.now())
        self.assertEqual((facts[first.pk].amount, facts[second.pk].amount), (4000, 6000))
        from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide
        now = timezone.now()
        submissions = [SubmittedCandidate(1, (SubmittedMember(first.pk, 'GOLD'),), now),
                       SubmittedCandidate(2, (SubmittedMember(second.pk, 'GOLD'),), now)]
        self.assertEqual(decide(build_rankable_candidates(submissions, as_of=now)).winner_id, 2)
        teams = [SubmittedCandidate(3, (SubmittedMember(first.pk, 'GOLD'),
                                        SubmittedMember(second.pk, 'SILVER')), now),
                 SubmittedCandidate(4, (SubmittedMember(first.pk, 'GOLD'),), now)]
        self.assertEqual(decide(build_rankable_candidates(teams, as_of=now)).winner_id, 3)

    def test_refund_and_unknown_addon_never_become_false_exact_values(self):
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        account = self.accounts[self.provider.pk]
        OrderRefund.objects.create(order=order, amount=100, reason='隔离测试未知归属')
        result = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertIsNone(result.amount)
        self.assertEqual(result.review_order_ids, (order.pk,))
        from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, RankingEvidenceError
        now = timezone.now()
        with self.assertRaises(RankingEvidenceError) as exc:
            build_rankable_candidates([SubmittedCandidate(1, (SubmittedMember(account.pk, ''),), now)], as_of=now)
        self.assertEqual(exc.exception.review_orders, {account.pk:(order.pk,)})
        OrderRefund.objects.filter(order=order).delete()
        Order.objects.filter(pk=order.pk).update(original_amount=order.original_amount+10)
        self.assertIsNone(original_amount_facts([account.pk], as_of=timezone.now())[account.pk].amount)
        Order.objects.filter(pk=order.pk).update(original_amount=order.original_amount)
        segment = OrderParticipationSegment.objects.get(attribution__order=order)
        OrderParticipationSegment.objects.filter(pk=segment.pk).update(original_share=segment.original_share+10)
        self.assertIsNone(original_amount_facts([account.pk], as_of=timezone.now())[account.pk].amount)
        OrderParticipationSegment.objects.filter(pk=segment.pk).update(original_share=segment.original_share)
        make_order(self.boss, source_order=order, is_gift_snapshot=False)
        result = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertIsNone(result.amount)
        self.assertEqual(result.review_order_ids, (order.pk,))

    def test_real_full_refund_zeroes_participating_original_amount(self):
        from wallet.models import Transaction, Wallet
        from orders.tests.factories import make_boss_type
        boss_account = self.accounts[self.boss.pk]
        boss_account.boss_type = make_boss_type(discount_rate=80)
        boss_account.save(update_fields=['boss_type'])
        order = self.new_order()
        self.assertEqual((order.original_amount, order.amount), (10000, 8000))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        account = self.accounts[self.provider.pk]
        self.assertEqual(original_amount_facts([account.pk], as_of=timezone.now())[account.pk].amount, 10000)
        self.login(self.admin)
        quote = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason':'全额协商退款',
            'plan_token':quote['plan_token'], 'client_request_id':'ranking-full-refund'}, format='json'))
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'REFUNDED')
        before = (Transaction.objects.count(), list(Wallet.objects.values_list('pk', 'balance')))
        result = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertEqual((result.amount, result.review_order_ids), (0, ()))
        self.assertEqual((Transaction.objects.count(), list(Wallet.objects.values_list('pk', 'balance'))), before)
        credit = Transaction.objects.get(order=order, tx_type='REFUND', wallet__account_id=order.customer_account_id)
        Transaction.objects.filter(pk=credit.pk).update(balance_after=credit.balance_after + 10)
        result = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertEqual((result.amount, result.review_order_ids), (None, (order.pk,)))

    def test_legacy_or_incomplete_attribution_is_not_assumed_zero(self):
        account = self.accounts[self.provider.pk]
        now = timezone.now()
        order = make_order(self.boss, provider=self.provider, provider_account=account,
                           status='IN_SERVICE', payment_status='PAID')
        Order.objects.filter(pk=order.pk).update(in_service_at=now)
        OrderProvider.objects.create(order=order, provider=self.provider, provider_account=account)
        result = original_amount_facts([account.pk], as_of=now+timedelta(seconds=1))[account.pk]
        self.assertIsNone(result.amount); self.assertEqual(result.review_order_ids, (order.pk,))
        OrderOriginalAttribution.objects.create(order=order, original_amount=10000, status='ACTIVE')
        result = original_amount_facts([account.pk], as_of=timezone.now()+timedelta(seconds=1))[account.pk]
        self.assertIsNone(result.amount); self.assertEqual(result.review_order_ids, (order.pk,))

    def test_legacy_team_entry_evidence_before_whole_order_starts_requires_review(self):
        from wallet.models import ProviderReport
        account = self.accounts[self.provider.pk]
        order = make_order(self.boss, provider=self.provider, provider_account=account,
                           status='GRABBED', payment_status='PAID')
        ProviderReport.objects.create(order=order, provider=self.provider, provider_account=account,
            game_name='隔离双陪入队', amount=100, entry_image='reports/entry/isolated.png')
        result = original_amount_facts([account.pk], as_of=timezone.now())[account.pk]
        self.assertIsNone(result.amount)
        self.assertEqual(result.review_order_ids, (order.pk,))

    def test_thirty_day_boundary_uses_each_segments_acceptance_time(self):
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        now = timezone.now()
        segment = OrderParticipationSegment.objects.get(attribution__order=order)
        segment.accepted_at = now-timedelta(days=30)
        segment.started_at = segment.accepted_at+timedelta(minutes=1)
        segment.save(update_fields=['accepted_at', 'started_at'])
        account = self.accounts[self.provider.pk]
        self.assertEqual(original_amount_facts([account.pk], as_of=now)[account.pk].amount, 10000)
        self.assertEqual(original_amount_facts([account.pk], as_of=now+timedelta(microseconds=1))[account.pk].amount, 0)

    def test_returning_provider_is_counted_per_entry_time(self):
        replacement = self.replacement(); order = self.new_order()
        first, second = self.accounts[self.provider.pk], self.accounts[replacement.pk]
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'首次转单份额'}))
        middle = OrderParticipationSegment.objects.get(attribution__order=order, provider_account=second,
                                                         released_at__isnull=True)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':middle.pk, 'entry_image':flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':3000, 'transferred':3000, 'agreement':'原陪玩转回'}))
        segments = list(OrderParticipationSegment.objects.filter(attribution__order=order, provider_account=first)
                        .order_by('pk'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/',
            {'segment_id':segments[-1].pk, 'entry_image':flow.screenshot()}, format='multipart'))
        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(created_at=now-timedelta(days=32),
            grabbed_at=now-timedelta(days=31, minutes=5), in_service_at=now-timedelta(days=31, minutes=4))
        OrderParticipationSegment.objects.filter(pk=segments[0].pk).update(
            accepted_at=now-timedelta(days=31, minutes=5), started_at=now-timedelta(days=31, minutes=4))
        facts = original_amount_facts([first.pk, second.pk], as_of=now)
        self.assertEqual((facts[first.pk].amount, facts[second.pk].amount), (3000, 3000))


@skipUnless('store_b' in settings.DATABASES, '需要两套隔离测试数据库')
class GrabOriginalFactsStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_ids_use_only_current_store_segments(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        now = timezone.now()
        facts = []
        for store, amount in ((resolve_store(), 1000), (xinan_store(), 2000)):
            with store_context(store):
                boss = make_user(); player = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(player)
                player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
                order = make_order(boss, provider=player, provider_account=account, amount=amount,
                                   original_amount=amount, status='IN_SERVICE', payment_status='PAID')
                Order.objects.filter(pk=order.pk).update(in_service_at=now-timedelta(minutes=1))
                attr = OrderOriginalAttribution.objects.create(order=order, original_amount=amount, status='ACTIVE')
                OrderParticipationSegment.objects.create(attribution=attr, provider_account=account, slot=1,
                    original_share=amount, accepted_at=now-timedelta(minutes=2), started_at=now-timedelta(minutes=1))
                facts.append((account.pk, order.pk, original_amount_facts([account.pk], as_of=timezone.now())[account.pk]))
        self.assertEqual(facts[0][:2], facts[1][:2])
        self.assertEqual([fact[2].amount for fact in facts], [1000, 2000])
