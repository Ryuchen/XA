from unittest.mock import patch
from unittest import skipUnless
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import Order, OrderOriginalAttribution, OrderParticipationSegment
from orders.tests import test_prestart_handoff as fixture
from orders.tests import test_release_flow as flow
from orders.tests import test_staff_team_dispatch as team_fixture
from orders.tests import test_game_commission_payment as game_fixture
from wallet.models import Transaction, Wallet


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, PRESTART_HANDOFF_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OriginalAttributionFlowTest(APITransactionTestCase):
    setUp = fixture.PrestartHandoffTest.setUp
    login = fixture.PrestartHandoffTest.login
    ok = fixture.PrestartHandoffTest.ok
    recharge = fixture.PrestartHandoffTest.recharge
    new_order = fixture.PrestartHandoffTest.new_order
    replacement = fixture.PrestartHandoffTest.replacement
    transfer = fixture.PrestartHandoffTest.transfer
    book = fixture.PrestartHandoffTest.book

    def test_assignment_and_actual_start_are_separate(self):
        order = self.new_order()
        attribution = OrderOriginalAttribution.objects.get(order=order)
        segment = attribution.segments.get()
        self.assertEqual((attribution.status, attribution.original_amount, segment.original_share), ('ACTIVE', 10000, 10000))
        self.assertEqual(segment.accepted_at, order.grabbed_at)
        self.assertIsNone(segment.started_at)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        segment.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(segment.started_at, order.in_service_at)
        self.assertEqual(segment.start_source, 'ORDER_START')

    def test_prestart_reassignment_keeps_history_without_fake_fulfillment(self):
        replacement = self.replacement(); order = self.new_order()
        self.ok(self.transfer(order.pk, self.provider, replacement))
        attribution = OrderOriginalAttribution.objects.get(order=order)
        old, new = list(attribution.segments.order_by('pk'))
        self.assertEqual((old.slot, new.slot, old.original_share, new.original_share), (1, 1, 10000, 10000))
        self.assertIsNotNone(old.released_at)
        self.assertIsNone(old.started_at); self.assertIsNone(new.started_at)
        self.login(replacement)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        old.refresh_from_db(); new.refresh_from_db()
        self.assertIsNone(old.started_at); self.assertIsNotNone(new.started_at)
        self.assertEqual(attribution.status, 'ACTIVE')

    def test_prestart_return_creates_distinct_nonstarted_segments(self):
        replacement = self.replacement(); order = self.new_order()
        self.ok(self.transfer(order.pk, self.provider, replacement))
        self.ok(self.transfer(order.pk, replacement, self.provider))
        attribution = OrderOriginalAttribution.objects.get(order=order)
        segments = list(attribution.segments.order_by('pk'))
        self.assertEqual([item.provider_account_id for item in segments], [self.accounts[self.provider.pk].pk,
            self.accounts[replacement.pk].pk, self.accounts[self.provider.pk].pk])
        self.assertTrue(all(item.started_at is None for item in segments))
        self.assertTrue(all(item.released_at for item in segments[:2]))
        self.assertIsNone(segments[2].released_at)
        self.assertEqual(attribution.status, 'ACTIVE')

    def test_in_service_transfer_requires_and_audits_agreed_original_shares(self):
        from orders.models import OrderOriginalShareTransfer
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        original_tx_count = Transaction.objects.filter(order=order).count()
        missing = self.transfer(order.pk, self.provider, replacement)
        self.assertIn('双方协商', missing.data['msg'])
        bad = self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':5990, 'agreement':'双方确认原价份额'})
        self.assertIn('合计', bad.data['msg'])
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertEqual(Transaction.objects.filter(order=order).count(), original_tx_count)
        self.assertFalse(OrderOriginalShareTransfer.objects.exists())
        self.ok(self.transfer(order.pk, self.provider, replacement, original_share={
            'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}))
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual(attribution.status, 'ACTIVE')
        old, new = list(attribution.segments.order_by('pk'))
        self.assertEqual((old.original_share, new.original_share), (4000, 6000))
        self.assertIsNotNone(old.started_at); self.assertIsNotNone(old.released_at)
        self.assertIsNone(new.started_at); self.assertIsNone(new.released_at)
        event = OrderOriginalShareTransfer.objects.get(previous_segment=old, next_segment=new)
        self.assertEqual((event.original_share_before, event.retained_share, event.transferred_share), (10000, 4000, 6000))
        self.assertEqual(event.agreement, '双方确认原价份额')
        self.assertEqual(event.operator_id, self.accounts[self.admin.pk].pk)
        self.login(self.admin)
        detail = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/'))['original_attribution']
        self.assertEqual((detail['status'], detail['transfers'][0]['agreement']), ('ACTIVE', '双方确认原价份额'))
        # B has not started the new segment, so B cannot retain any original share.
        denied = self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':1000, 'transferred':5000, 'agreement':'重复核对'})
        self.assertIn('未开始履约', denied.data['msg'])
        self.ok(self.transfer(order.pk, replacement, self.provider, original_share={
            'retained':0, 'transferred':6000, 'agreement':'重新协调转回'}))
        segments = list(attribution.segments.order_by('pk'))
        self.assertEqual([(item.provider_account_id, item.original_share) for item in segments], [
            (self.accounts[self.provider.pk].pk, 4000), (self.accounts[replacement.pk].pk, 0),
            (self.accounts[self.provider.pk].pk, 6000)])
        self.assertEqual(OrderOriginalShareTransfer.objects.count(), 2)

    def test_refund_marks_attribution_for_review_without_changing_funds(self):
        order = self.new_order()
        before = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason':'客服协调取消',
            'plan_token':preview['plan_token'], 'client_request_id':'original-attribution-refund'}, format='json'))
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual(attribution.status, 'REVIEW')
        self.assertIn('退款', attribution.review_reason)
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, before + order.amount)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND').count(), 1)

    def test_original_transfer_audit_failure_rolls_back_everything(self):
        from orders.models import OrderOriginalShareTransfer
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        before = Transaction.objects.filter(order=order).count()
        with patch('orders.models.OrderOriginalShareTransfer.objects.create', side_effect=DatabaseError('audit failure')):
            with self.assertRaises(DatabaseError):
                self.transfer(order.pk, self.provider, replacement, original_share={
                    'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'})
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual((attribution.status, attribution.segments.count()), ('ACTIVE', 1))
        old = attribution.segments.get()
        self.assertEqual(old.original_share, 10000); self.assertIsNone(old.released_at)
        self.assertFalse(OrderOriginalShareTransfer.objects.exists())
        self.assertEqual(Transaction.objects.filter(order=order).count(), before)

    def test_database_rejects_unbalanced_or_identical_transfer_segments(self):
        from django.db import IntegrityError, transaction
        from orders.models import OrderOriginalShareTransfer
        replacement = self.replacement(); order = self.new_order()
        attribution = OrderOriginalAttribution.objects.get(order=order)
        previous = attribution.segments.get()
        following = OrderParticipationSegment.objects.create(attribution=attribution,
            provider_account_id=self.accounts[replacement.pk].pk, slot=1, original_share=6000,
            accepted_at=timezone.now())
        base = {'previous_segment':previous, 'next_segment':following, 'original_share_before':10000,
                'retained_share':4000, 'transferred_share':5990, 'operator':self.accounts[self.admin.pk],
                'agreement':'核对测试', 'created_at':timezone.now()}
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderOriginalShareTransfer.objects.create(**base)
        base.update(next_segment=previous, transferred_share=6000)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderOriginalShareTransfer.objects.create(**base)

    def test_unpermitted_staff_cannot_record_agreement_or_transfer(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import OrderOriginalShareTransfer
        from orders.tests.factories import make_console_user
        replacement = self.replacement(); order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        staff = make_console_user(['order:view']); account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.accounts[staff.pk] = account; self.login(staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'无权限尝试', 'transfers':[{
            'old_provider_id':self.accounts[self.provider.pk].pk, 'new_provider_id':self.accounts[replacement.pk].pk,
            'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json')
        self.assertEqual(result.status_code, 403)
        self.assertFalse(OrderOriginalShareTransfer.objects.exists())
        order.refresh_from_db(); self.assertEqual(order.provider_account_id, self.accounts[self.provider.pk].pk)

    def test_historical_order_is_not_backfilled_on_start(self):
        with override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False):
            order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        self.assertFalse(OrderOriginalAttribution.objects.filter(order=order).exists())

    def test_disabled_flag_does_not_query_new_tables(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False), CaptureQueriesContext(connection) as captured:
            order = self.new_order()
            self.login(self.provider)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        sql = ' '.join(query['sql'].lower() for query in captured.captured_queries)
        self.assertNotIn('orders_orderoriginalattribution', sql)
        self.assertNotIn('orders_orderparticipationsegment', sql)

    def test_reservation_conversion_records_assignment(self):
        self.ok(self.recharge())
        start = (timezone.now()+timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        _, order_id = self.book(self.provider, start, 'original-attribution-reservation')
        attribution = OrderOriginalAttribution.objects.get(order_id=order_id)
        segment = attribution.segments.get()
        self.assertEqual(attribution.status, 'ACTIVE')
        self.assertEqual(segment.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertIsNone(segment.started_at)

    def test_failed_segment_write_rolls_back_payment_and_order(self):
        self.ok(self.recharge()); self.login(self.boss)
        balance = Wallet.objects.get(account=self.accounts[self.boss.pk]).balance
        with patch('orders.models.OrderParticipationSegment.objects.create', side_effect=DatabaseError('segment failure')):
            with self.assertRaises(DatabaseError):
                self.client.post('/api/orders/create/', {'service_id':self.service.pk,
                    'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'segment-write-failure'}, format='json')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.accounts[self.boss.pk]).balance, balance)
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OriginalAttributionTeamTest(APITransactionTestCase):
    setUp = team_fixture.StaffTeamDispatchTest.setUp
    auth = team_fixture.StaffTeamDispatchTest.auth
    create = team_fixture.StaffTeamDispatchTest.create
    assign = team_fixture.StaffTeamDispatchTest.assign

    def test_team_first_and_second_start_are_recorded_independently(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create()
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual(attribution.status, 'PENDING')
        self.assertEqual(self.assign(order).data['code'], 0)
        segments = list(attribution.segments.order_by('slot'))
        self.assertEqual([item.original_share for item in segments], [5000, 5000])
        self.assertEqual(attribution.segments.filter(started_at__isnull=True).count(), 2)
        self.auth(self.providers[0])
        result = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        segments[0].refresh_from_db(); segments[1].refresh_from_db()
        self.assertIsNotNone(segments[0].started_at); self.assertIsNone(segments[1].started_at)
        self.auth(self.providers[1])
        result = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        segments[0].refresh_from_db(); segments[1].refresh_from_db()
        self.assertLessEqual(segments[0].started_at, segments[1].started_at)
        self.assertEqual((segments[0].start_source, segments[1].start_source), ('ENTRY_IMAGE', 'ENTRY_IMAGE'))

    def test_non_integral_store_coin_original_is_marked_review(self):
        order = self.create()
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual(attribution.status, 'REVIEW')
        self.assertIn('整数店币', attribution.review_reason)

    def test_odd_total_team_original_records_rounding_surplus(self):
        # Synthetic package fixture: final order original is 101 whole coins.
        self.service.price = 505; self.service.commission_fixed = 0
        self.service.save(update_fields=['price', 'commission_fixed'])
        order = self.create(); self.assertEqual(order.original_amount, 1010)
        self.assertEqual(self.assign(order).data['code'], 0)
        attribution = OrderOriginalAttribution.objects.get(order=order)
        self.assertEqual(attribution.rounding_surplus, 10)
        self.assertEqual(list(attribution.segments.order_by('slot').values_list('original_share', flat=True)), [510, 510])

    @override_settings(PRESTART_HANDOFF_ENABLED=True)
    def test_partial_team_entry_and_replacement_preserve_started_teammate(self):
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        self.auth(self.providers[0])
        result = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[1].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        attribution = OrderOriginalAttribution.objects.get(order=order)
        segments = list(attribution.segments.order_by('pk'))
        self.assertEqual(attribution.status, 'ACTIVE')
        self.assertIsNotNone(segments[0].started_at)
        self.assertIsNone(segments[1].started_at); self.assertIsNotNone(segments[1].released_at)
        self.assertIsNone(segments[2].started_at); self.assertEqual(segments[1].slot, segments[2].slot)

    def test_in_service_team_transfer_changes_only_affected_slot(self):
        from orders.models import OrderOriginalShareTransfer
        self.service.price = 5000; self.service.save(update_fields=['price'])
        order = self.create(); self.assertEqual(self.assign(order).data['code'], 0)
        for provider in self.providers[:2]:
            self.auth(provider)
            started = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':team_fixture.screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
        self.auth(self.staff)
        result = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'reason':'更换陪玩', 'transfers':[{
            'old_provider_id':self.accounts[self.providers[0].pk].pk,
            'new_provider_id':self.accounts[self.providers[2].pk].pk,
            'split_type':'PERCENT', 'split_value':50, 'penalty_amount':0,
            'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认分配'}}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        attribution = OrderOriginalAttribution.objects.get(order=order)
        segments = list(attribution.segments.order_by('pk'))
        self.assertEqual([(segment.slot, segment.original_share) for segment in segments], [(1, 2000), (2, 5000), (1, 3000)])
        self.assertIsNotNone(segments[1].started_at); self.assertIsNone(segments[1].released_at)
        self.assertIsNone(segments[2].started_at)
        event = OrderOriginalShareTransfer.objects.get(previous_segment=segments[0])
        self.assertEqual((event.original_share_before, event.retained_share, event.transferred_share), (5000, 2000, 3000))


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class OriginalAttributionStaffDispatchTest(APITransactionTestCase):
    setUp = game_fixture.GameCommissionPaymentTest.setUp
    link = game_fixture.GameCommissionPaymentTest.link
    auth = game_fixture.GameCommissionPaymentTest.auth

    def test_quick_dispatch_records_original_share(self):
        from orders.tests.factories import make_console_user
        staff = make_console_user(['order:view', 'order:dispatch']); account = self.link(staff)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        self.auth(account)
        response = self.client.post('/api/admin/orders/dispatch/', {'customer_id':self.boss_account.pk,
            'service_id':self.service.pk, 'provider_id':self.accounts[0].pk,
            'client_request_id':'staff-original-attribution'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        attribution = OrderOriginalAttribution.objects.get(order_id=response.data['data']['id'])
        self.assertEqual((attribution.status, attribution.segments.get().original_share), ('ACTIVE', 5000))


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OriginalAttributionStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_equal_order_and_account_ids_do_not_share_participation(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        facts = []
        for store, host, price in ((resolve_store(), 'sl.esports.club', 1000), (xinan_store(), 'xa.esports.club', 2000)):
            with store_context(store):
                boss = make_user(); account = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=account, user=boss); wallet.balance = 5000; wallet.save(update_fields=['balance'])
                provider = make_provider(deposit_mode='EARNINGS'); player = get_or_create_account_for_legacy_user(provider)
                provider.escort_profile.account = player; provider.escort_profile.save(update_fields=['account'])
                service = make_service(price=price)
                client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                response = client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':player.pk,
                    'client_request_id':'attribution-store-order'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                attribution = OrderOriginalAttribution.objects.get(order_id=response.data['data']['id'])
                facts.append((attribution.order_id, attribution.segments.get().provider_account_id,
                              attribution.segments.get().original_share))
        self.assertEqual(facts[0][:2], facts[1][:2])
        self.assertEqual([row[2] for row in facts], [1000, 2000])

    def test_transfer_agreement_is_confined_to_the_order_store(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderOriginalShareTransfer
        from orders.tests.factories import make_console_user, make_provider, make_service, make_user
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        values = []
        for store, host, transfer in ((resolve_store(), 'sl.esports.club', True), (xinan_store(), 'xa.esports.club', False)):
            with store_context(store):
                boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
                wallet = get_wallet(account=boss_account, user=boss); wallet.balance = 3000; wallet.save(update_fields=['balance'])
                players = [make_provider(deposit_mode='EARNINGS') for _ in range(2)]
                accounts = [get_or_create_account_for_legacy_user(player) for player in players]
                for player, account in zip(players, accounts):
                    player.escort_profile.account = account; player.escort_profile.save(update_fields=['account'])
                staff = make_console_user(['order:view','order:dispatch']); operator = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = operator; staff.admin_membership.save(update_fields=['account'])
                service = make_service(price=1000)
                boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
                response = boss_client.post('/api/orders/create/', {'service_id':service.pk, 'provider_id':accounts[0].pk,
                    'client_request_id':'original-transfer-store'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order_id = response.data['data']['id']
                if transfer:
                    player_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
                    started = player_client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':flow.screenshot()}, format='multipart')
                    self.assertEqual(started.data['code'], 0, started.data)
                    admin_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator)[0])
                    response = admin_client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商', 'transfers':[{
                        'old_provider_id':accounts[0].pk, 'new_provider_id':accounts[1].pk,
                        'original_share':{'retained':400, 'transferred':600, 'agreement':'双方确认原价份额'}}]}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                attribution = OrderOriginalAttribution.objects.get(order_id=order_id)
                values.append((order_id, accounts[0].pk, attribution.segments.count(),
                    OrderOriginalShareTransfer.objects.filter(previous_segment__attribution=attribution).count()))
        self.assertEqual(values[0][:2], values[1][:2])
        self.assertEqual([row[2:] for row in values], [(2, 1), (1, 0)])
