from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless
from tempfile import TemporaryDirectory

from django.test import override_settings
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from orders.models import Order, OrderProvider, OrderRoomConfirmation, OrderStatusLog
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from orders.settlement import SettlementError
from orders.tests.test_room_completion_review import RoomCompletionReviewTests
from orders.tests.test_order_rooms import OrderRoomTests
from orders.tests.test_release_flow import ReleaseFlowTest, screenshot
from site_messages.models import RoomConfirmationNotice
from wallet.models import ProviderReport, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True)
class RoomBossConfirmationTests(APITestCase):
    def setUp(self):
        RoomCompletionReviewTests.setUp(self)
        result = self.provider_client.post(self.url, self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.request_id = result.data['data']['id']
        review = self.staff.post(f'/api/admin/room-completion-requests/{self.request_id}/review/',
            {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'review'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        self.path = f'/api/orders/orders/{self.order.pk}/room/completion-requests/{self.request_id}/confirm/'
        self.payload = {'client_request_id': 'boss-confirm'}

    def test_owner_confirms_once_and_creates_only_draft_report(self):
        self.assertEqual(self.provider_client.post(self.path, self.payload, format='json').status_code, 404)
        self.assertEqual(self.other_client.post(self.path, self.payload, format='json').status_code, 404)
        result = self.boss_client.post(self.path, self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['confirmation']['status'], 'CONFIRMED')
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 200)
        self.assertEqual(self.boss_client.post(self.path, {'client_request_id': 'other'}, format='json').status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertIsNotNone(self.order.completed_at)
        self.assertIsNone(self.order.settled_at)
        self.assertEqual(ProviderReport.objects.get(order=self.order).status, ProviderReport.Status.DRAFT)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        self.assertEqual(OrderStatusLog.objects.filter(order=self.order, to_status='COMPLETED').count(), 1)
        wait = OrderRoomConfirmation.objects.get(request_id=self.request_id)
        self.assertEqual((wait.confirmed_by_id, wait.confirmation_request_id), (self.boss_account.pk, 'boss-confirm'))

    def test_objection_pauses_confirmation_until_resolved(self):
        response = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason': '需核对', 'client_request_id': 'objection'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 409)
        result = self.staff.post(f"/api/admin/room-objections/{response.data['data']['id']}/resolve/",
            {'resolution': '核对完成', 'client_request_id': 'resolve', 'confirmation_ready': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 200)

    def test_stale_participant_or_missing_clock_blocks_confirmation(self):
        row = self.order.providers.get(provider_account=self.provider_account)
        row.released_at = timezone.now(); row.save(update_fields=['released_at'])
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 409)
        row.released_at = None; row.save(update_fields=['released_at'])
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=None)
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 409)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=self.request_id).status, 'RUNNING')

    def test_failed_completion_rolls_back_confirmation_and_order(self):
        with patch('orders.services.complete_fulfillment', side_effect=SettlementError('需核对')):
            result = self.boss_client.post(self.path, self.payload, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=self.request_id).status, 'RUNNING')
        self.assertFalse(OrderStatusLog.objects.filter(order=self.order, to_status='COMPLETED').exists())
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_notification_intent_failure_rolls_back_confirmation_and_order(self):
        # SQLite verifies the confirmation transaction here. The production
        # lifecycle lock used to enable this branch is covered on MySQL.
        with patch('site_messages.room_confirmation_notice.enabled', return_value=True):
            self.assert_notification_intent_failure_rolls_back()

    def assert_notification_intent_failure_rolls_back(self):
        with patch.object(RoomConfirmationNotice.objects, 'get_or_create', side_effect=RuntimeError('notify failed')):
            with self.assertRaises(RuntimeError):
                self.boss_client.post(self.path, self.payload, format='json')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=self.request_id).status, 'RUNNING')
        self.assertFalse(ProviderReport.objects.filter(order=self.order).exists())
        self.assertFalse(RoomConfirmationNotice.objects.filter(confirmation__request_id=self.request_id).exists())
        result = self.boss_client.post(self.path, self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertCountEqual(RoomConfirmationNotice.objects.filter(confirmation__request_id=self.request_id)
                              .values_list('account_id', flat=True), [self.boss_account.pk, self.provider_account.pk])

    def test_boss_confirms_double_only_after_both_completion_screenshots(self):
        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['fulfillment_provider_count'])
        order = OrderRoomTests.order(self, escort_mode='DOUBLE', requires_boss_selection=True)
        OrderRoomTests.assign(self, order, [(self.provider, self.provider_account), (self.other, self.other_account)])
        from orders.state_machine import transition
        transition(order.pk, Order.Status.IN_SERVICE)
        request_path = f'/api/orders/orders/{order.pk}/room/completion-requests/'
        payload = {'description': '已完成', 'client_request_id': 'team-request'}
        with TemporaryDirectory(prefix='xa-room-boss-team-') as media, override_settings(MEDIA_ROOT=media):
            complete_path = f'/api/orders/orders/{order.pk}/complete/'
            result = self.provider_client.post(complete_path, {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            result = self.provider_client.post(request_path, payload, format='json')
            self.assertEqual(result.status_code, 409, result.data)
            result = self.other_client.post(complete_path, {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            result = self.provider_client.post(request_path, payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            pk = result.data['data']['id']
            result = self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
                {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'team-review'}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            result = self.boss_client.post(f'{request_path}{pk}/confirm/',
                {'client_request_id': 'team-confirm'}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'COMPLETED')
            self.assertEqual(ProviderReport.objects.filter(order=order, status='DRAFT').count(), 2)
            self.assertFalse(Transaction.objects.filter(order=order).exists())

    def test_boss_can_confirm_after_deadline_before_automatic_task(self):
        wait = OrderRoomConfirmation.objects.get(request_id=self.request_id)
        with patch('django.utils.timezone.now', return_value=wait.deadline+timedelta(microseconds=1)):
            result = self.boss_client.post(self.path, self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(pk=wait.pk).status, 'CONFIRMED')

    @override_settings(ROOM_BOSS_CONFIRMATION_ENABLED=False)
    def test_incomplete_flow_is_disabled_by_default(self):
        self.assertEqual(self.boss_client.post(self.path, self.payload, format='json').status_code, 503)


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomBossConfirmationMoneyFlowTest(APITestCase):
    setUp = ReleaseFlowTest.setUp
    login = ReleaseFlowTest.login
    ok = ReleaseFlowTest.ok
    recharge = ReleaseFlowTest.recharge
    new_order = ReleaseFlowTest.new_order
    settle_after_window = ReleaseFlowTest.settle_after_window

    def test_real_order_waits_for_boss_then_report_approval_and_release(self):
        from wallet.models import SystemConfig, Wallet
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': '{"minutes":30,"version":1}'})
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'IN_SERVICE')
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.assertEqual(report.status, 'DRAFT')
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        application = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
            {'description': '已完成', 'client_request_id': 'room-request'}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/room-completion-requests/{application['id']}/review/",
            {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'review'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f"/api/orders/orders/{order.pk}/room/completion-requests/{application['id']}/confirm/",
            {'client_request_id': 'boss-confirm'}, format='json'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True)
class RoomBossConfirmationStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_boss_confirmation_cannot_cross_store_even_with_same_order_id(self):
        from orders.state_machine import transition
        from wallet.models import SystemConfig
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, player = make_user(), make_provider(deposit_mode='EARNINGS')
                boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
                order = make_order(boss, service=make_service(room_enabled=True), customer_account=boss_account,
                                   payment_status='PAID', settlement_mode='REPORT_APPROVAL')
                def assign(current):
                    current.provider, current.provider_account = player, player_account
                    OrderProvider.objects.create(order=current, provider=player, provider_account=player_account)
                transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
                transition(order.pk, Order.Status.IN_SERVICE)
                SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
                    defaults={'value': '{"minutes":30,"version":1}'})
                staff = make_console_user(['order:view', 'support:edit'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
                player_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(player_account)[0])
                boss_token = issue_account_tokens(boss_account)[0]
                boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+boss_token)
                result = player_client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                    {'description': '已完成', 'client_request_id': 'request'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                request_id = result.data['data']['id']
                result = staff_client.post(f'/api/admin/room-completion-requests/{request_id}/review/',
                    {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'review'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                rows.append((store, host, order.pk, request_id, boss_token, boss_client))
        self.assertEqual((rows[0][2], rows[0][3]), (rows[1][2], rows[1][3]))
        path = f'/api/orders/orders/{rows[1][2]}/room/completion-requests/{rows[1][3]}/confirm/'
        cross = APIClient(HTTP_HOST=rows[0][1], HTTP_AUTHORIZATION='Bearer '+rows[1][4])
        self.assertNotEqual(cross.post(path, {'client_request_id': 'cross'}, format='json').status_code, 200)
        response = rows[1][5].post(path, {'client_request_id': 'own'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for index, (store, _, order_id, _, _, _) in enumerate(rows):
            with store_context(store):
                self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED' if index else 'IN_SERVICE')
                self.assertEqual(OrderRoomConfirmation.objects.get().status, 'CONFIRMED' if index else 'RUNNING')
