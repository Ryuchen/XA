from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminPendingDeadlineWorkflowTest(APITestCase):
    def test_expired_order_keeps_deadline_and_is_removed_from_dispatchable_queue(self):
        boss = make_user()
        boss_account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, boss_account)
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        token, _ = issue_account_tokens(boss_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        created = self.client.post('/api/orders/create/', {
            'service_id': service.pk, 'dispatch_route': 'STAFF', 'client_request_id': 'deadline-queue-test',
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']

        staff = make_console_user(['order:view', 'order:dispatch'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        staff_token, _ = issue_account_tokens(staff_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        queue = self.client.get('/api/admin/orders/', {'actionable_staff': '1'}).data['data']
        row = next(item for item in queue['list'] if item['id'] == order_id)
        self.assertTrue(row['dispatch_assignment_allowed'])
        self.assertTrue(row['auto_cancel_at'])

        # The cancellation worker has not run yet; make the real saved deadline
        # elapsed to exercise the next read without altering business functions.
        expired = timezone.now() - timedelta(seconds=1)
        Order.objects.filter(pk=order_id).update(auto_cancel_at=expired)
        general = self.client.get('/api/admin/orders/').data['data']
        row = next(item for item in general['list'] if item['id'] == order_id)
        self.assertEqual((row['status'], row['payment_status']), ('PENDING', 'PAID'))
        self.assertFalse(row['dispatch_assignment_allowed'])
        self.assertTrue(row['auto_cancel_at'])
        queue = self.client.get('/api/admin/orders/', {'actionable_staff': '1'}).data['data']
        self.assertNotIn(order_id, [item['id'] for item in queue['list']])
