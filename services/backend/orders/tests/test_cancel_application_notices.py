"""Real paid orders, team applications and cancellation notice handoff."""
from datetime import timedelta

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from orders.models import Order
from orders.tasks import auto_cancel_pending_order
from orders.tests import test_team_invitation_detail as fixture_module
from site_messages.models import Message


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_RECHARGE_PROOF=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class CancelApplicationNoticesTest(APITransactionTestCase):
    setUp = fixture_module.TeamInvitationDetailTest.setUp
    login = fixture_module.TeamInvitationDetailTest.login
    ok = fixture_module.TeamInvitationDetailTest.ok
    recharge = fixture_module.TeamInvitationDetailTest.recharge
    invite = fixture_module.TeamInvitationDetailTest.invite

    def verify_recipients(self):
        for user in (self.provider, self.partner):
            self.login(user)
            rows = self.ok(self.client.get('/api/messages/'))['messages']
            notices = [row for row in rows if row['title'] == '报名订单已取消']
            self.assertEqual(len(notices), 1)
            self.assertIn(self.order.order_no, notices[0]['preview'])
            self.assertEqual(notices[0]['action_url'], f'/pages/orders/index?application_id={self.application.pk}')
            detail = self.ok(self.client.get(self.path))
            self.assertEqual(detail['order']['status'], 'CANCELLED')
            self.assertEqual(detail['closed_reason'], '订单已取消，本次报名结束。')
            self.assertFalse(detail['can_confirm'])
            self.assertNotEqual(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json').data['code'], 0)
        self.login(self.outsider)
        self.assertFalse(any(row['title'] == '报名订单已取消' for row in self.ok(self.client.get('/api/messages/'))['messages']))
        self.assertEqual(self.client.get(self.path).status_code, 404)

    def test_boss_cancels_pending_team_application(self):
        self.invite()
        self.login(self.partner)
        self.ok(self.client.post(self.action, {'action': 'CONFIRM_TEAM'}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json'))
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/', {}, format='json').data['code'], 0)
        self.verify_recipients()

    def test_staff_cancels_pending_team_application(self):
        self.invite()
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'order:cancel']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{self.order.pk}/cancel/', {}, format='json'))
        self.verify_recipients()

    def test_expiration_worker_notifies_once(self):
        self.invite()
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(auto_cancel_pending_order.run(self.order.pk), 'cancelled')
        auto_cancel_pending_order.run(self.order.pk)
        self.verify_recipients()

    def test_timeout_management_command_notifies_once(self):
        self.invite()
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now() - timedelta(seconds=1))
        call_command('check_order_timeout', verbosity=0)
        call_command('check_order_timeout', verbosity=0)
        self.assertEqual(Message.objects.filter(title='报名订单已取消').count(), 2)
        self.verify_recipients()
