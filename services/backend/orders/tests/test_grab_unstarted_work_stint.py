"""Actual unstarted transfer must not block the candidate's ranking facts."""
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.grab_original_facts import original_amount_facts
from orders.grab_ranking import (SubmittedCandidate, SubmittedMember,
                                 build_rankable_candidates, decide)
from orders.tests import test_provider_transfer_ranking as fixture
from orders.tests import test_release_flow as flow


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class GrabUnstartedWorkStintTest(APITestCase):
    setUp = fixture.ProviderTransferRankingTest.setUp
    login = fixture.ProviderTransferRankingTest.login
    ok = fixture.ProviderTransferRankingTest.ok
    recharge = fixture.ProviderTransferRankingTest.recharge
    new_order = fixture.ProviderTransferRankingTest.new_order
    replacement = fixture.ProviderTransferRankingTest.replacement
    transfer = fixture.ProviderTransferRankingTest.transfer
    enter_transfer = fixture.ProviderTransferRankingTest.enter_transfer

    def test_only_unstarted_recipient_has_exact_zero(self):
        replacement = self.replacement()
        order = self.new_order()
        first, second = self.accounts[self.provider.pk].pk, self.accounts[replacement.pk].pk
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 6000, 'agreement': '客服确认本人原价份额'}))
        as_of = timezone.now()
        facts = original_amount_facts([first, second], as_of=as_of)
        self.assertEqual((facts[first].amount, facts[first].review_order_ids), (4000, ()))
        self.assertEqual((facts[second].amount, facts[second].review_order_ids), (0, ()))
        submission = SubmittedCandidate(1, (SubmittedMember(second, 'SILVER'),), as_of)
        self.assertEqual(build_rankable_candidates([submission], as_of=as_of)[0].members[0].net_original_amount, 0)
        self.enter_transfer(order, replacement, 'ranking-facts-entry')
        as_of = timezone.now()
        facts = original_amount_facts([second], as_of=as_of)
        self.assertEqual((facts[second].amount, facts[second].review_order_ids), (6000, ()))
        self.assertEqual(build_rankable_candidates([submission], as_of=as_of)[0].members[0].net_original_amount, 6000)
        same_tier = [SubmittedCandidate(1, (SubmittedMember(first, 'GOLD'),), as_of),
                     SubmittedCandidate(2, (SubmittedMember(second, 'GOLD'),), as_of)]
        self.assertEqual(decide(build_rankable_candidates(same_tier, as_of=as_of)).winner_id, 2)
        higher_tier = [same_tier[0], SubmittedCandidate(2, (SubmittedMember(second, 'SILVER'),), as_of)]
        self.assertEqual(decide(build_rankable_candidates(higher_tier, as_of=as_of)).winner_id, 1)

    def test_explicit_shares_return_period_and_real_full_refund(self):
        from orders.models import OrderTransferSeatEvent, OrderWorkOriginalTransfer, OrderWorkStint
        from wallet.models import Transaction, Wallet
        replacement = self.replacement()
        order = self.new_order()
        first, second = self.accounts[self.provider.pk].pk, self.accounts[replacement.pk].pk
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(original_amount_facts([first], as_of=timezone.now())[first].amount, 10000)
        boss_id = self.accounts[self.boss.pk].pk
        before = (Transaction.objects.filter(order=order).count(), Wallet.objects.get(account_id=boss_id).balance)
        denied = self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 5990, 'agreement': '客服确认本人原价份额'})
        self.assertNotEqual(denied.data['code'], 0)
        self.assertEqual(OrderWorkOriginalTransfer.objects.filter(previous__stint__order=order).count(), 0)
        self.assertFalse(OrderTransferSeatEvent.objects.filter(seat__order=order).exists())
        self.assertEqual(OrderWorkStint.objects.filter(order=order).count(), 1)
        self.assertEqual((Transaction.objects.filter(order=order).count(), Wallet.objects.get(account_id=boss_id).balance), before)
        denied = self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4001, 'transferred': 5999, 'agreement': '客服确认本人原价份额'})
        self.assertNotEqual(denied.data['code'], 0)
        self.assertEqual(OrderWorkOriginalTransfer.objects.filter(previous__stint__order=order).count(), 0)
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained': 4000, 'transferred': 6000, 'agreement': '客服确认本人原价份额'}))
        self.enter_transfer(order, replacement, 'original-middle')
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained': 2000, 'transferred': 4000, 'agreement': '客服确认转回原价份额'}))
        self.enter_transfer(order, self.provider, 'original-return')
        as_of = timezone.now()
        facts = original_amount_facts([first, second], as_of=as_of)
        self.assertEqual((facts[first].amount, facts[second].amount), (8000, 2000))
        last = OrderWorkOriginalTransfer.objects.filter(previous__stint__order=order).order_by('-created_at').first()
        recent = original_amount_facts([first, second], as_of=as_of, since=last.created_at)
        self.assertEqual((recent[first].amount, recent[second].amount), (4000, 0))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '售后全额退款',
            'plan_token': preview['plan_token'], 'client_request_id': 'original-share-full-refund'}, format='json'))
        facts = original_amount_facts([first, second], as_of=timezone.now())
        self.assertEqual((facts[first].amount, facts[second].amount), (0, 0))


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, PRESTART_HANDOFF_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False, ALLOWED_HOSTS=['*'],
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class GrabUnstartedWorkStintStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    login = GrabUnstartedWorkStintTest.login
    ok = GrabUnstartedWorkStintTest.ok
    recharge = GrabUnstartedWorkStintTest.recharge
    new_order = GrabUnstartedWorkStintTest.new_order
    replacement = GrabUnstartedWorkStintTest.replacement
    transfer = GrabUnstartedWorkStintTest.transfer
    enter_transfer = GrabUnstartedWorkStintTest.enter_transfer

    def test_unstarted_and_started_facts_in_both_stores(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.ProviderTransferRankingTest.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                GrabUnstartedWorkStintTest.test_only_unstarted_recipient_has_exact_zero(self)

    def test_return_and_full_refund_in_both_stores(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.ProviderTransferRankingTest.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                GrabUnstartedWorkStintTest.test_explicit_shares_return_period_and_real_full_refund(self)
