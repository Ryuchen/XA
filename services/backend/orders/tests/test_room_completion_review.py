from unittest import skipUnless
from unittest.mock import patch
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from console.models import AdminAuditLog
from orders.models import Order, OrderProvider, OrderRoomCompletionRequest, OrderRoomConfirmation
from orders.state_machine import IllegalTransitionError, transition
from orders.tests.test_order_rooms import OrderRoomTests
from orders.tests.test_room_objections import RoomObjectionTests
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from rest_framework.test import APIClient
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction, SystemConfig


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True)
class RoomCompletionReviewTests(APITestCase):
    def setUp(self):
        OrderRoomTests.setUp(self)
        self.order = OrderRoomTests.order(self)
        OrderRoomTests.assign(self, self.order, [(self.provider, self.provider_account)])
        self.staff_client = RoomObjectionTests.staff_client.__get__(self)
        self.staff = self.staff_client(['order:view', 'support:edit'])
        self.limited = self.staff_client(['order:view'])
        self.url = f'/api/orders/orders/{self.order.pk}/room/completion-requests/'
        self.payload = {'description': '目标已完成', 'client_request_id': 'request-1'}
        SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
            defaults={'value': '{"minutes":30,"version":1}'})
        transition(self.order.pk, Order.Status.IN_SERVICE)

    def submit(self):
        response = self.provider_client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']['id']

    def test_provider_submits_and_authorized_staff_reviews_without_completing(self):
        self.assertEqual(self.boss_client.post(self.url, self.payload, format='json').status_code, 404)
        self.assertEqual(self.other_client.post(self.url, self.payload, format='json').status_code, 404)
        pk = self.submit()
        self.assertEqual(self.submit(), pk)
        self.assertEqual(self.boss_client.get(self.url).data['data']['list'][0]['id'], pk)
        self.assertEqual(self.other_client.get(self.url).status_code, 404)
        self.assertEqual(self.provider_client.post(self.url, {**self.payload, 'description': '不同'}, format='json').status_code, 409)
        self.assertEqual(self.provider_client.post(self.url, {**self.payload, 'client_request_id': 'another'}, format='json').status_code, 409)
        self.assertEqual(self.limited.get('/api/admin/room-completion-requests/').status_code, 403)
        self.assertEqual(self.staff.get('/api/admin/room-completion-requests/').data['data']['total'], 1)
        path = f'/api/admin/room-completion-requests/{pk}/review/'
        action = {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review-1'}
        self.assertEqual(self.limited.post(path, action, format='json').status_code, 403)
        self.assertEqual(self.staff.post(path, action, format='json').status_code, 200)
        self.assertEqual(self.staff.post(path, action, format='json').status_code, 200)
        self.assertEqual(self.staff.post(path, {**action, 'note': '不同'}, format='json').status_code, 409)
        self.assertEqual(AdminAuditLog.objects.filter(resource='room-completion-review').count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED)

    def test_objection_or_participant_change_prevents_verification(self):
        pk = self.submit()
        path = f'/api/admin/room-completion-requests/{pk}/review/'
        action = {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review-1'}
        objection = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason': '尚有异议', 'client_request_id': 'objection'}, format='json')
        self.assertEqual(objection.status_code, 200)
        self.assertEqual(self.staff.post(path, action, format='json').status_code, 409)
        self.staff.post(f"/api/admin/room-objections/{objection.data['data']['id']}/resolve/",
                        {'resolution': '已核实', 'client_request_id': 'resolve'}, format='json')
        old = self.order.providers.get(provider_account=self.provider_account)
        old.released_at = timezone.now(); old.save(update_fields=['released_at'])
        OrderProvider.objects.create(order=self.order, provider=self.other, provider_account=self.other_account)
        Order.objects.filter(pk=self.order.pk).update(provider=self.other, provider_account=self.other_account)
        self.assertEqual(self.staff.post(path, action, format='json').status_code, 409)
        self.assertEqual(OrderRoomCompletionRequest.objects.get(pk=pk).status, 'PENDING')
        rejected = {**action, 'decision': 'REJECTED', 'note': '人员已变化'}
        self.assertEqual(self.staff.post(path, rejected, format='json').status_code, 200)
        self.assertEqual(self.other_client.post(self.url, self.payload, format='json').status_code, 200)

    def test_audit_failure_rolls_back_review(self):
        pk = self.submit()
        path = f'/api/admin/room-completion-requests/{pk}/review/'
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.staff.post(path, {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        self.assertEqual(OrderRoomCompletionRequest.objects.get(pk=pk).status, 'PENDING')

    def test_both_providers_are_snapshotted_for_team_review(self):
        self.service.billing_provider_count = 2
        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        order = OrderRoomTests.order(self, escort_mode='DOUBLE')
        OrderRoomTests.assign(self, order, [(self.provider, self.provider_account), (self.other, self.other_account)])
        transition(order.pk, Order.Status.IN_SERVICE)
        result = self.other_client.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                                        self.payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['participants'], sorted([self.provider_account.pk, self.other_account.pk]))

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=False)
    def test_legacy_completion_cannot_skip_the_room_flow(self):
        path = f'/api/orders/orders/{self.order.pk}/complete/'
        self.assertEqual(self.provider_client.post(path, {}, format='json').status_code, 409)
        self.assertEqual(self.other_client.post(path, {}, format='json').status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_single_completion_evidence_does_not_finish_or_settle_room_order(self):
        from wallet.models import ProviderReport
        path = f'/api/orders/orders/{self.order.pk}/complete/'
        result = self.provider_client.post(path, {}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertTrue(ProviderReport.objects.filter(order=self.order, provider_account=self.provider_account).exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        with self.assertRaises(IllegalTransitionError):
            transition(self.order.pk, Order.Status.COMPLETED)

    def test_double_completion_evidence_waits_for_room_confirmation(self):
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport
        self.service.billing_provider_count = 2
        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        order = OrderRoomTests.order(self, escort_mode='DOUBLE', requires_boss_selection=True)
        OrderRoomTests.assign(self, order, [(self.provider, self.provider_account), (self.other, self.other_account)])
        transition(order.pk, Order.Status.IN_SERVICE)
        path = f'/api/orders/orders/{order.pk}/complete/'
        request_path = f'/api/orders/orders/{order.pk}/room/completion-requests/'
        with TemporaryDirectory(prefix='xa-room-evidence-') as media, override_settings(MEDIA_ROOT=media):
            self.assertEqual(self.provider_client.post(request_path, self.payload, format='json').status_code, 409)
            result = self.provider_client.post(path, {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(self.provider_client.post(request_path, self.payload, format='json').status_code, 409)
            result = self.other_client.post(path, {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(ProviderReport.objects.filter(order=order).exclude(completion_image='').count(), 2)
            result = self.provider_client.post(request_path, self.payload, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            review = self.staff.post(f"/api/admin/room-completion-requests/{result.data['data']['id']}/review/",
                {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'team-review'}, format='json')
            self.assertEqual(review.status_code, 200, review.data)
            result = self.other_client.post(path, {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 409, result.data)
            report = ProviderReport.objects.get(order=order, provider_account=self.other_account)
            result = self.other_client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': 'COMPLETION', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.status_code, 409, result.data)
            order.refresh_from_db()
            self.assertEqual(order.status, Order.Status.IN_SERVICE)
            self.assertFalse(Transaction.objects.filter(order=order).exists())
            with self.assertRaises(IllegalTransitionError):
                transition(order.pk, Order.Status.COMPLETED)

    def test_notification_failure_rolls_back_submission(self):
        with patch('site_messages.utils.create_cycle_message', side_effect=RuntimeError('notification failed')):
            with self.assertRaises(RuntimeError):
                self.provider_client.post(self.url, self.payload, format='json')
        self.assertFalse(OrderRoomCompletionRequest.objects.exists())

    def test_review_requires_confirmation_configuration(self):
        pk = self.submit()
        SystemConfig.objects.filter(key='room_confirmation_deadline').delete()
        result = self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertEqual(OrderRoomCompletionRequest.objects.get(pk=pk).status, 'PENDING')
        self.assertFalse(OrderRoomConfirmation.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(resource='room-completion-review').exists())

    @override_settings(ROOM_REVIEW_NOTICE_RECOVERY_ENABLED=False)
    def test_review_notification_failure_rolls_back_confirmation_and_audit(self):
        pk = self.submit()
        with patch('site_messages.utils.create_cycle_message', side_effect=RuntimeError('notification failed')):
            with self.assertRaises(RuntimeError):
                self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
                    {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        self.assertEqual(OrderRoomCompletionRequest.objects.get(pk=pk).status, 'PENDING')
        self.assertFalse(OrderRoomConfirmation.objects.exists())
        self.assertFalse(AdminAuditLog.objects.filter(resource='room-completion-review').exists())

    def test_confirmation_clock_pauses_and_resumes_original_remaining_time(self):
        pk, start = self.submit(), timezone.now()
        review = {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}
        with patch('django.utils.timezone.now', return_value=start):
            result = self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/', review, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        wait = OrderRoomConfirmation.objects.get(request_id=pk)
        self.assertEqual((wait.config_minutes, wait.config_version, wait.deadline), (30, 1, start+timedelta(minutes=30)))
        SystemConfig.objects.filter(key='room_confirmation_deadline').update(value='{"minutes":5,"version":2}')
        with patch('django.utils.timezone.now', return_value=start+timedelta(minutes=10)):
            objection = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
                {'reason': '需要核对', 'client_request_id': 'objection'}, format='json')
        self.assertEqual(objection.status_code, 200, objection.data)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.deadline, wait.remaining_microseconds), ('PAUSED', None, 20*60*1000000))
        path = f"/api/admin/room-objections/{objection.data['data']['id']}/resolve/"
        data = {'resolution': '条件依然满足', 'client_request_id': 'resolve', 'confirmation_ready': True}
        self.assertEqual(self.staff.post(path, {k:v for k,v in data.items() if k != 'confirmation_ready'}, format='json').status_code, 400)
        with patch('django.utils.timezone.now', return_value=start+timedelta(minutes=15)):
            result = self.staff.post(path, data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.staff.post(path, data, format='json').status_code, 200)
        self.assertEqual(self.staff.post(path, {**data, 'confirmation_ready': False}, format='json').status_code, 409)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.deadline, wait.config_minutes, wait.config_version),
                         ('RUNNING', start+timedelta(minutes=35), 30, 1))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.IN_SERVICE)
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())

    def test_revised_conditions_allow_new_request_with_new_configuration(self):
        pk = self.submit()
        self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        objection = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
            {'reason': '进度有误', 'client_request_id': 'objection'}, format='json')
        resolved = self.staff.post(f"/api/admin/room-objections/{objection.data['data']['id']}/resolve/",
            {'resolution': '继续履约', 'client_request_id': 'resolve', 'confirmation_ready': False}, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        self.assertEqual(OrderRoomConfirmation.objects.get(request_id=pk).status, 'INVALIDATED')
        SystemConfig.objects.filter(key='room_confirmation_deadline').update(value='{"minutes":5,"version":2}')
        result = self.provider_client.post(self.url, {**self.payload, 'client_request_id': 'new-request'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        pk2 = result.data['data']['id']
        result = self.staff.post(f'/api/admin/room-completion-requests/{pk2}/review/',
            {'decision': 'VERIFIED', 'note': '重新核实', 'client_request_id': 'new-review'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual((OrderRoomConfirmation.objects.get(request_id=pk2).config_minutes,
                          OrderRoomConfirmation.objects.get(request_id=pk2).config_version), (5, 2))

    def test_expired_wait_can_be_paused_until_automatic_confirmation_executes(self):
        pk, start = self.submit(), timezone.now()
        with patch('django.utils.timezone.now', return_value=start):
            self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
                {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        with patch('django.utils.timezone.now', return_value=start+timedelta(minutes=30)):
            result = self.boss_client.post(f'/api/orders/orders/{self.order.pk}/room/objections/',
                {'reason': '核对', 'client_request_id': 'late'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        wait = OrderRoomConfirmation.objects.get(request_id=pk)
        self.assertEqual((wait.status, wait.remaining_microseconds), ('PAUSED', 0))
        with patch('django.utils.timezone.now', return_value=start+timedelta(minutes=35)):
            resolved = self.staff.post(f"/api/admin/room-objections/{result.data['data']['id']}/resolve/",
                {'resolution': '已核对', 'client_request_id': 'resolve-late', 'confirmation_ready': True}, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        wait.refresh_from_db()
        self.assertEqual((wait.status, wait.deadline), ('RUNNING', start+timedelta(minutes=35)))

    @override_settings(ROOM_OBJECTION_NOTICE_RECOVERY_ENABLED=False)
    def test_objection_notification_and_resolution_audit_failures_restore_clock(self):
        pk = self.submit()
        self.staff.post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'review'}, format='json')
        path = f'/api/orders/orders/{self.order.pk}/room/objections/'
        data = {'reason': '需要核对', 'client_request_id': 'objection'}
        original = OrderRoomConfirmation.objects.get(request_id=pk).deadline
        with patch('site_messages.utils.create_cycle_message', side_effect=RuntimeError('notification failed')):
            with self.assertRaises(RuntimeError):
                self.boss_client.post(path, data, format='json')
        wait = OrderRoomConfirmation.objects.get(request_id=pk)
        self.assertEqual((wait.status, wait.deadline), ('RUNNING', original))
        self.assertFalse(self.order.room.objections.exists())
        objection = self.boss_client.post(path, data, format='json').data['data']
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.staff.post(f"/api/admin/room-objections/{objection['id']}/resolve/",
                    {'resolution': '仍满足', 'client_request_id': 'resolve', 'confirmation_ready': True}, format='json')
        wait.refresh_from_db()
        self.assertEqual(wait.status, 'PAUSED')
        self.assertEqual(self.order.room.objections.get(pk=objection['id']).status, 'OPEN')

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=False)
    def test_incomplete_flow_is_disabled_by_default(self):
        self.assertEqual(self.provider_client.post(self.url, self.payload, format='json').status_code, 503)
        self.assertEqual(self.staff.get('/api/admin/room-completion-requests/').status_code, 503)
        self.assertFalse(OrderRoomCompletionRequest.objects.exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True)
class RoomCompletionReviewStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_requests_and_staff_queues_are_store_local_even_for_equal_order_ids(self):
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, provider = make_user(), make_provider(deposit_mode='EARNINGS')
                boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, provider)]
                order = make_order(boss, service=make_service(room_enabled=True), customer_account=boss_account, payment_status='PAID')
                SystemConfig.objects.update_or_create(key='room_confirmation_deadline',
                    defaults={'value': '{"minutes":30,"version":1}' if host.startswith('sl.')
                                       else '{"minutes":45,"version":1}'})
                def assign(row):
                    row.provider, row.provider_account = provider, player_account
                    OrderProvider.objects.create(order=row, provider=provider, provider_account=player_account)
                transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
                transition(order.pk, Order.Status.IN_SERVICE)
                staff = make_console_user(['order:view', 'support:edit'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                player_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(player_account)[0])
                staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
                rows.append((store, order.pk, player_client, staff_client))
        self.assertEqual(rows[0][1], rows[1][1])
        response = rows[1][2].post(f'/api/orders/orders/{rows[1][1]}/room/completion-requests/',
            {'description': '已达到条件', 'client_request_id': 'store-one'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(rows[0][3].get('/api/admin/room-completion-requests/').data['data']['total'], 0)
        self.assertEqual(rows[1][3].get('/api/admin/room-completion-requests/').data['data']['total'], 1)
        pk = response.data['data']['id']
        review = rows[1][3].post(f'/api/admin/room-completion-requests/{pk}/review/',
            {'decision': 'VERIFIED', 'note': '核实', 'client_request_id': 'review'}, format='json')
        self.assertEqual(review.status_code, 200, review.data)
        for index, (store, _, _, _) in enumerate(rows):
            with store_context(store):
                self.assertEqual(OrderRoomConfirmation.objects.count(), index)
                if index:
                    self.assertEqual(OrderRoomConfirmation.objects.get().config_minutes, 45)

