"""Isolated proof of the existing team attribution without service transfer."""
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.grab_original_facts import original_amount_facts
from orders.models import (OrderOriginalAttribution, OrderParticipationSegment,
                           OrderProviderCompletionFact, OrderWorkOriginalShare, OrderWorkStint)
from orders.tests import test_provider_transfer_ranking as fixture
from orders.tests import test_release_flow as flow
from wallet.models import Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamOfficialAttributionTest(APITestCase):
    setUp = fixture.UnknownTeamTransferRankingTest.setUp
    auth = fixture.UnknownTeamTransferRankingTest.auth
    ok = fixture.UnknownTeamTransferRankingTest.ok
    create_specified = fixture.UnknownTeamTransferRankingTest.create_specified

    def test_odd_price_uses_existing_original_attribution_without_transfer(self):
        self.service.price = 505
        self.service.commission_type = 'PERCENT'
        self.service.commission_rate = 20
        self.service.save(update_fields=['price', 'commission_type', 'commission_rate'])
        first, second = self.providers[:2]
        order = self.create_specified([first, second])
        basis = OrderOriginalAttribution.objects.get(order=order)
        segments = list(OrderParticipationSegment.objects.filter(attribution=basis).order_by('pk'))
        self.assertEqual((basis.original_amount, basis.rounding_surplus, basis.status), (1010, 10, 'ACTIVE'))
        self.assertEqual([row.original_share for row in segments], [510, 510])
        ids = [self.accounts[user.pk].pk for user in (first, second)]
        self.auth(first)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        facts = original_amount_facts(ids, as_of=timezone.now())
        self.assertEqual([facts[pk].amount for pk in ids], [510, 0])
        self.auth(second)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
            {'entry_image': flow.screenshot()}, format='multipart'))
        facts = original_amount_facts(ids, as_of=timezone.now())
        self.assertEqual([facts[pk].amount for pk in ids], [510, 510])
        for account_id in ids:
            segment = OrderParticipationSegment.objects.get(attribution=basis, provider_account_id=account_id)
            stint = OrderWorkStint.objects.get(order=order, account_id=account_id)
            self.assertEqual(segment.started_at, stint.started_at)
        self.assertFalse(OrderWorkOriginalShare.objects.filter(stint__order=order).exists())
        self.auth(first)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
            {'completion_image': flow.screenshot()}, format='multipart'))
        fact = OrderProviderCompletionFact.objects.get(order=order, provider_account_id=ids[0])
        self.assertEqual(fact.completed_at, OrderWorkStint.objects.get(order=order, account_id=ids[0]).completed_at)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
        self.assertEqual(Transaction.objects.filter(order=order).count(), 1)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class TeamOfficialAttributionStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    auth = TeamOfficialAttributionTest.auth
    ok = TeamOfficialAttributionTest.ok
    create_specified = TeamOfficialAttributionTest.create_specified

    def test_odd_price_and_actual_entry_in_both_stores(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.UnknownTeamTransferRankingTest.setup_fixture.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                TeamOfficialAttributionTest.test_odd_price_uses_existing_original_attribution_without_transfer(self)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamOfficialTransferAlignmentTest(APITestCase):
    setUp = fixture.UnknownTeamTransferRankingTest.setUp
    auth = fixture.UnknownTeamTransferRankingTest.auth
    ok = fixture.UnknownTeamTransferRankingTest.ok
    create_specified = fixture.UnknownTeamTransferRankingTest.create_specified

    def test_real_transfer_entry_keeps_one_price_source_and_matching_times(self):
        from orders.models import OrderOriginalShareTransfer, OrderTransferSeatEvent
        first, second, incoming = self.providers
        order = self.create_specified([first, second])
        for player in (first, second):
            self.auth(player)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': flow.screenshot()}, format='multipart'))
        self.auth(self.staff)
        before = Transaction.objects.filter(order=order).count()
        invalid = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '客服协调转单',
            'transfers': [{'old_provider_id': self.accounts[first.pk].pk,
                'new_provider_id': self.accounts[incoming.pk].pk,
                'original_share': {'retained': 2000, 'transferred': 2990,
                                   'agreement': '双方确认原价份额'}}]}, format='json')
        self.assertNotEqual(invalid.data['code'], 0)
        self.assertFalse(OrderTransferSeatEvent.objects.filter(seat__order=order).exists())
        self.assertEqual(OrderWorkStint.objects.filter(order=order).count(), 2)
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)
        response = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '客服协调转单',
            'transfers': [{'old_provider_id': self.accounts[first.pk].pk,
                'new_provider_id': self.accounts[incoming.pk].pk,
                'original_share': {'retained': 2000, 'transferred': 3000,
                                                         'agreement': '双方确认原价份额'}}]}, format='json')
        self.ok(response)
        transfer = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order=order)
        previous = OrderWorkStint.objects.get(order=order, account=self.accounts[first.pk])
        new = OrderWorkStint.objects.get(order=order, account=self.accounts[incoming.pk])
        self.assertEqual((previous.released_at, transfer.created_at, transfer.next_segment.accepted_at),
                         (transfer.created_at, transfer.created_at, transfer.created_at))
        self.assertEqual(new.incoming_id, new.incoming.pk)
        self.assertIsNone(new.started_at)
        self.assertFalse(OrderWorkOriginalShare.objects.filter(stint__order=order).exists())
        self.auth(first)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/', {
            'segment_id': transfer.previous_segment_id, 'completion_image': flow.screenshot(),
            'client_request_id': 'formal-former-evidence'}, format='multipart'))
        self.auth(incoming)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertIsNone(detail['my_pending_work_stint'])
        self.assertIn('START_PARTICIPATION', detail['allowed_actions'])
        # A mismatched persisted interval is not silently repaired or used as
        # authorization for an original-price segment.
        new.started_at = timezone.now()
        new.save(update_fields=['started_at'])
        mismatch = self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
            'segment_id': transfer.next_segment_id, 'entry_image': flow.screenshot(),
            'client_request_id': 'formal-stint-mismatch'}, format='multipart')
        self.assertNotEqual(mismatch.data['code'], 0)
        transfer.next_segment.refresh_from_db()
        self.assertIsNone(transfer.next_segment.started_at)
        new.started_at = None
        new.save(update_fields=['started_at'])
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
            'segment_id': transfer.next_segment_id, 'entry_image': flow.screenshot(),
            'client_request_id': 'formal-stint-entry'}, format='multipart'))
        new.refresh_from_db(); transfer.next_segment.refresh_from_db()
        self.assertEqual(new.started_at, transfer.next_segment.started_at)
        self.assertEqual(new.entry_image.name, transfer.next_segment.entry_image.name)
        facts = original_amount_facts([self.accounts[player.pk].pk for player in self.providers], as_of=timezone.now())
        self.assertEqual([facts[self.accounts[player.pk].pk].amount for player in self.providers], [2000, 5000, 3000])
        self.auth(self.staff)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '客服确认转回',
            'transfers': [{'old_provider_id': self.accounts[incoming.pk].pk,
                'new_provider_id': self.accounts[first.pk].pk,
                'original_share': {'retained': 1000, 'transferred': 2000,
                                   'agreement': '双方确认转回原价份额'}}]}, format='json'))
        returned = OrderOriginalShareTransfer.objects.get(previous_segment=transfer.next_segment)
        new.refresh_from_db()
        self.assertEqual(new.released_at, returned.created_at)
        self.auth(first)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
            'segment_id': returned.next_segment_id, 'entry_image': flow.screenshot(),
            'client_request_id': 'formal-stint-return'}, format='multipart'))
        work_return = OrderWorkStint.objects.get(order=order, account=self.accounts[first.pk], released_at__isnull=True)
        returned.next_segment.refresh_from_db()
        self.assertEqual(work_return.started_at, returned.next_segment.started_at)
        facts = original_amount_facts([self.accounts[player.pk].pk for player in self.providers], as_of=timezone.now())
        self.assertEqual([facts[self.accounts[player.pk].pk].amount for player in self.providers], [4000, 5000, 1000])
        self.assertFalse(OrderWorkOriginalShare.objects.filter(stint__order=order).exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class TeamOfficialTransferAlignmentStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    auth = TeamOfficialTransferAlignmentTest.auth
    ok = TeamOfficialTransferAlignmentTest.ok
    create_specified = TeamOfficialTransferAlignmentTest.create_specified

    def test_real_transfer_alignment_in_each_store(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.UnknownTeamTransferRankingTest.setup_fixture.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                TeamOfficialTransferAlignmentTest.test_real_transfer_entry_keeps_one_price_source_and_matching_times(self)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_ECONOMICS_CAPTURE_ENABLED=False,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=False, DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=True,
    DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=False, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamPrincipalTransferAlignmentTest(APITestCase):
    setUp = TeamOfficialTransferAlignmentTest.setUp
    auth = TeamOfficialTransferAlignmentTest.auth
    ok = TeamOfficialTransferAlignmentTest.ok
    create_specified = TeamOfficialTransferAlignmentTest.create_specified

    def test_default_principal_segment_path_uses_same_work_stints(self):
        TeamOfficialTransferAlignmentTest.test_real_transfer_entry_keeps_one_price_source_and_matching_times(self)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_ECONOMICS_CAPTURE_ENABLED=False,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=False, DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=True,
    DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=False, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ALLOWED_HOSTS=['*'])
class TeamPrincipalTransferAlignmentStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    auth = TeamOfficialTransferAlignmentTest.auth
    ok = TeamOfficialTransferAlignmentTest.ok
    create_specified = TeamOfficialTransferAlignmentTest.create_specified

    def test_default_principal_segment_path_in_both_stores(self):
        if 'store_b' not in settings.DATABASES:
            self.skipTest('requires two isolated stores')
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        for store in (resolve_store(), xinan_store()):
            with self.subTest(store=store.slug), store_context(store):
                fixture.UnknownTeamTransferRankingTest.setup_fixture.setUp(self)
                self.client.defaults['HTTP_HOST'] = 'xa.esports.club' if store.slug == 'xinan' else 'sl.esports.club'
                TeamOfficialTransferAlignmentTest.test_real_transfer_entry_keeps_one_price_source_and_matching_times(self)


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_ECONOMICS_CAPTURE_ENABLED=False,
    PRESTART_SCOPE_SNAPSHOT_ENABLED=False, DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=True,
    DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True, ORDINARY_SERVICE_FUNDING_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=False, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamPrincipalSwitchOffTest(APITestCase):
    setUp = TeamOfficialTransferAlignmentTest.setUp
    auth = TeamOfficialTransferAlignmentTest.auth
    ok = TeamOfficialTransferAlignmentTest.ok
    create_specified = TeamOfficialTransferAlignmentTest.create_specified

    def test_paid_order_keeps_stint_and_segment_after_new_order_switch_off(self):
        from orders.models import OrderOriginalShareTransfer
        first, second, incoming = self.providers
        order = self.create_specified([first, second])
        attribution = OrderOriginalAttribution.objects.get(order=order, status='ACTIVE')
        sources = list(order.funding_allocations.values_list('kind', 'amount'))
        self.assertEqual({kind for kind, _ in sources}, {'PREPAID'})
        self.assertEqual(sum(amount for _, amount in sources), order.amount)
        with override_settings(DEFAULT_PRINCIPAL_SEGMENTS_ENABLED=False):
            from orders.principal_segments import enabled_for_order
            self.assertTrue(enabled_for_order(order), {'funding_terms': order.funding_terms,
                'payment_status': order.payment_status,
                'allocations': list(order.funding_allocations.values('kind', 'amount', 'refunded_amount'))})
            for player in (first, second):
                self.auth(player)
                self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                    {'entry_image': flow.screenshot()}, format='multipart'))
                segment = OrderParticipationSegment.objects.get(attribution=attribution,
                    provider_account=self.accounts[player.pk])
                work = OrderWorkStint.objects.get(order=order, account=self.accounts[player.pk])
                self.assertEqual(work.started_at, segment.started_at)
            self.auth(self.staff)
            self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason': '原订单约定继续',
                'transfers': [{'old_provider_id': self.accounts[first.pk].pk,
                    'new_provider_id': self.accounts[incoming.pk].pk,
                    'original_share': {'retained': 2000, 'transferred': 3000,
                                       'agreement': '双方确认原价份额'}}]}, format='json'))
            transfer = OrderOriginalShareTransfer.objects.get(previous_segment__attribution=attribution)
            self.auth(first)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-evidence/', {
                'segment_id': transfer.previous_segment_id, 'completion_image': flow.screenshot()}, format='multipart'))
            self.auth(incoming)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/participation-start/', {
                'segment_id': transfer.next_segment_id, 'entry_image': flow.screenshot()}, format='multipart'))
            transfer.next_segment.refresh_from_db()
            work = OrderWorkStint.objects.get(order=order, account=self.accounts[incoming.pk])
            self.assertEqual(work.started_at, transfer.next_segment.started_at)
            for player in (second, incoming):
                self.auth(player)
                self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                    {'completion_image': flow.screenshot()}, format='multipart'))
            order.refresh_from_db()
            self.assertEqual(order.status, 'COMPLETED')
            self.assertFalse(OrderWorkOriginalShare.objects.filter(stint__order=order).exists())
            self.auth(self.boss)
            new = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                'provider_ids': [self.accounts[first.pk].pk, self.accounts[second.pk].pk],
                'client_request_id': 'new-order-after-segment-switch-off'}, format='json'))
            self.assertFalse(OrderOriginalAttribution.objects.filter(order_id=new['id']).exists())
