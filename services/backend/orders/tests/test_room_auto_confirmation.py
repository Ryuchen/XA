from datetime import timedelta
from unittest.mock import patch
from unittest import skipUnless

from django.test import override_settings
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.models import Order, OrderProvider, OrderRoomConfirmation, OrderRoomCompletionRequest, OrderStatusLog
from orders.tests.factories import make_order, make_user, make_provider, make_service
from club_accounts.services import get_or_create_account_for_legacy_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from orders.room_auto_confirmation import process_due, process_one
from orders.settlement import SettlementError
from orders.tests.test_room_boss_confirmation import RoomBossConfirmationTests
from orders.tests.test_release_flow import ReleaseFlowTest, screenshot
from orders.tests.test_order_rooms import OrderRoomTests
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', ROOM_COMPLETION_REVIEW_ENABLED=True,
                   ROOM_BOSS_CONFIRMATION_ENABLED=True, ROOM_AUTO_CONFIRMATION_ENABLED=True)
class RoomAutoConfirmationTests(APITestCase):
    setUp = RoomBossConfirmationTests.setUp

    def expire(self):
        wait = OrderRoomConfirmation.objects.get(request_id=self.request_id)
        OrderRoomConfirmation.objects.filter(pk=wait.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        return wait.pk

    def test_due_task_confirms_once_without_crediting_wallet(self):
        wait_id = self.expire()
        self.assertEqual(process_one(wait_id), 'confirmed')
        self.assertEqual(process_one(wait_id), 'skip')
        self.order.refresh_from_db()
        wait = OrderRoomConfirmation.objects.get(pk=wait_id)
        self.assertEqual((self.order.status, wait.status, wait.confirmed_by_id), ('COMPLETED', 'AUTO', None))
        self.assertIsNone(self.order.settled_at)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        self.assertEqual(OrderStatusLog.objects.filter(order=self.order, to_status='COMPLETED').count(), 1)
        from site_messages.models import Message
        self.assertEqual(Message.objects.filter(recipient_account=self.boss_account, title='房间订单已自动完成').count(), 1)

    def test_late_objection_pauses_then_resumes_zero_remaining(self):
        wait_id = self.expire()
        result = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason': '仍有异议', 'client_request_id': 'late-objection'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(process_one(wait_id), 'skip')
        result = self.staff.post(f"/api/admin/room-objections/{result.data['data']['id']}/resolve/",
            {'resolution': '仍满足结单', 'client_request_id': 'resolve', 'confirmation_ready': True}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(process_one(wait_id), 'confirmed')
        result = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason': '再次提出', 'client_request_id': 'after-auto'}, format='json')
        self.assertEqual(result.status_code, 400)

    def test_validation_failure_does_not_starve_later_due_rooms(self):
        first = self.expire()
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=None)
        other = OrderRoomTests.order(self)
        OrderRoomTests.assign(self, other, [(self.provider, self.provider_account)])
        from orders.state_machine import transition
        transition(other.pk, Order.Status.IN_SERVICE)
        response = self.provider_client.post(f'/api/orders/orders/{other.pk}/room/completion-requests/',
            {'description': '另一单已完成', 'client_request_id': 'other-request'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        pk = response.data['data']['id']
        response = self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'other-review'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        OrderRoomConfirmation.objects.filter(request_id=pk).update(deadline=timezone.now()-timedelta(seconds=1))
        self.assertEqual(process_due(batch_size=1), {'confirmed': 1, 'review': 1, 'skipped': 0, 'failed': 0})
        self.assertEqual(OrderRoomConfirmation.objects.get(pk=first).status, 'RUNNING')
        from site_messages.models import Message
        self.assertEqual(Message.objects.filter(title='房间自动结单待核对').count(), 1)
        queue = self.staff.get('/api/admin/room-confirmation-problems/')
        self.assertEqual(queue.data['data']['total'], 1)
        self.assertEqual(self.limited.get('/api/admin/room-confirmation-problems/').status_code, 403)
        self.assertEqual(process_one(first), 'review')
        self.assertEqual(Message.objects.filter(title='房间自动结单待核对').count(), 1)
        Order.objects.filter(pk=self.order.pk).update(service_clock_version=1)
        self.assertEqual(process_one(first), 'confirmed')
        self.assertEqual(self.staff.get('/api/admin/room-confirmation-problems/').data['data']['total'], 0)
        other.refresh_from_db()
        self.assertEqual(other.status, 'COMPLETED')

    def test_failed_completion_rolls_back_auto_record(self):
        wait_id = self.expire()
        with patch('orders.services.complete_fulfillment', side_effect=SettlementError('需核对')):
            self.assertEqual(process_one(wait_id), 'review')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertEqual(OrderRoomConfirmation.objects.get(pk=wait_id).status, 'RUNNING')
        self.assertFalse(OrderStatusLog.objects.filter(order=self.order, to_status='COMPLETED').exists())

    @override_settings(ROOM_AUTO_CONFIRMATION_ENABLED=False)
    def test_worker_disabled_by_default(self):
        wait_id = self.expire()
        self.assertEqual(process_one(wait_id), 'disabled')
        self.assertEqual(OrderRoomConfirmation.objects.get(pk=wait_id).status, 'RUNNING')


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True,
                   ROOM_AUTO_CONFIRMATION_ENABLED=True)
class RoomAutoConfirmationMoneyFlowTest(APITestCase):
    setUp = ReleaseFlowTest.setUp
    login = ReleaseFlowTest.login
    ok = ReleaseFlowTest.ok
    recharge = ReleaseFlowTest.recharge
    new_order = ReleaseFlowTest.new_order
    settle_after_window = ReleaseFlowTest.settle_after_window

    def test_auto_completion_does_not_release_income_until_report_and_clock(self):
        from wallet.models import SystemConfig, Wallet, ProviderReport
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline', defaults={'value': '{"minutes":30,"version":1}'})
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        application = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
            {'description': '已完成', 'client_request_id': 'request'}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/room-completion-requests/{application['id']}/review/",
            {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'review'}, format='json'))
        wait = OrderRoomConfirmation.objects.get(request_id=application['id'])
        OrderRoomConfirmation.objects.filter(pk=wait.pk).update(deadline=timezone.now()-timedelta(seconds=1))
        self.assertEqual(process_one(wait.pk), 'confirmed')
        order.refresh_from_db()
        self.assertEqual((order.status, order.settled_at), ('COMPLETED', None))
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
                   ROOM_BOSS_CONFIRMATION_ENABLED=True, ROOM_AUTO_CONFIRMATION_ENABLED=True)
class RoomAutoConfirmationStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_periodic_scan_only_processes_current_store(self):
        from orders.state_machine import transition
        from orders.tasks import auto_confirm_room_orders
        from stores.tasks import PERIODIC_STORE_TASKS
        self.assertIn('orders.tasks.auto_confirm_room_orders', PERIODIC_STORE_TASKS)
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['auto-confirm-room-orders']['task'], 'orders.tasks.auto_confirm_room_orders')
        rows = []
        for store in (resolve_store(), xinan_store()):
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
                request = OrderRoomCompletionRequest.objects.create(room=order.room, submitted_by=player_account,
                    request_id='request', description='已完成', participants=[player_account.pk], status='VERIFIED',
                    reviewed_by=player_account, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
                wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
                    started_at=timezone.now()-timedelta(minutes=31), deadline=timezone.now()-timedelta(minutes=1))
                rows.append((store, order.pk, wait.pk))
        self.assertEqual(rows[0][1:], rows[1][1:])
        with store_context(rows[1][0]):
            result = auto_confirm_room_orders.run()
            self.assertEqual(result['confirmed'], 1)
        for index, (store, order_id, wait_id) in enumerate(rows):
            with store_context(store):
                self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED' if index else 'IN_SERVICE')
                self.assertEqual(OrderRoomConfirmation.objects.get(pk=wait_id).status, 'AUTO' if index else 'RUNNING')
                self.assertFalse(Transaction.objects.filter(order_id=order_id).exists())
