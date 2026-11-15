"""Real paid order: staff assignment ends the old public application round."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderApplication
from orders.tests import test_release_flow as release_flow
from orders.tests.factories import make_provider
from site_messages.models import Message


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class StaffAssignApplicationsRealTest(APITransactionTestCase):
    setUp = release_flow.ReleaseFlowTest.setUp
    login = release_flow.ReleaseFlowTest.login
    ok = release_flow.ReleaseFlowTest.ok
    recharge = release_flow.ReleaseFlowTest.recharge

    def _assignment_then_reject(self, use_legacy_endpoint=False, assign_applicant=False):
        applicant = self.provider if assign_applicant else make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        applicant_account = get_or_create_account_for_legacy_user(applicant)
        self.accounts[applicant.pk] = applicant_account
        for user in (applicant, self.provider):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])

        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'staff-assign-old-application',
        }, format='json'))
        order_id = data['id']
        self.login(applicant)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        application = OrderApplication.objects.get(order_id=order_id, applicant=applicant_account)
        self.login(self.admin)
        endpoint = (f'/api/orders/orders/{order_id}/assign/' if use_legacy_endpoint
                    else f'/api/admin/orders/{order_id}/assign/')
        self.ok(self.client.post(endpoint, {
            'provider_id': self.accounts[self.provider.pk].pk,
        }, format='json'))
        application.refresh_from_db()
        self.assertEqual(application.status, 'SELECTED' if assign_applicant else 'NOT_SELECTED')
        notices = Message.objects.filter(recipient_account=applicant_account, related_order_id=order_id,
                                         title='报名结果已更新')
        self.assertEqual(notices.count(), 0 if assign_applicant else 1)
        if not assign_applicant:
            self.login(applicant)
            result = self.ok(self.client.get(f'/api/orders/orders/{order_id}/applications/{application.pk}/mine/'))
            self.assertEqual(result['closed_reason'], '订单已选择其他陪玩，本次报名结束。')
        self.login(self.provider)
        order = Order.objects.get(pk=order_id)
        before = (order.status, order.provider_account_id, order.payment_status, order.status_logs.count(),
                  order.transactions.count())
        rejected = self.client.post(f'/api/orders/orders/{order_id}/reject/', {'reason': '时间冲突'}, format='json')
        self.assertEqual(rejected.data['errorCode'], 'ORDER_SELF_REJECTION_DISABLED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id, order.payment_status, order.status_logs.count(),
                          order.transactions.count()), before)
        self.login(self.boss)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(detail['applications'], [])
        self.login(applicant)
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json').data['code'], 0)
        application.refresh_from_db()
        self.assertEqual(application.status, 'SELECTED' if assign_applicant else 'NOT_SELECTED')
        self.assertFalse(OrderApplication.objects.filter(order_id=order_id, status='PENDING').exists())

    def test_admin_assignment_closes_others_and_rejection_keeps_assignment(self):
        self._assignment_then_reject()

    def test_legacy_assignment_closes_others_and_rejection_keeps_assignment(self):
        self._assignment_then_reject(use_legacy_endpoint=True)

    def test_assigning_the_applicant_records_selection_without_wrong_notice(self):
        self._assignment_then_reject(assign_applicant=True)

    @override_settings(PRESTART_HANDOFF_ENABLED=True)
    def test_old_selection_cannot_replay_after_staff_handoff_away_and_back(self):
        replacement = make_provider(deposit_mode='EARNINGS')
        replacement_account = get_or_create_account_for_legacy_user(replacement)
        replacement.escort_profile.account = replacement_account
        replacement.escort_profile.save(update_fields=['account'])
        self.accounts[replacement.pk] = replacement_account
        profile = self.provider.escort_profile
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'selection-round-replay',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        first = OrderApplication.objects.get(order_id=order_id, status='PENDING')
        first_select = f'/api/orders/orders/{order_id}/applications/{first.pk}/select/'
        self.login(self.boss)
        self.ok(self.client.post(first_select, {}, format='json'))
        self.ok(self.client.post(first_select, {}, format='json'))
        order = Order.objects.get(pk=order_id)
        before = (order.payment_status, order.amount, order.transactions.count())
        self.login(self.admin)
        for old, new in ((self.provider, replacement), (replacement, self.provider)):
            self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
                'reason': '客服协调更换', 'transfers': [{'old_provider_id': self.accounts[old.pk].pk,
                    'new_provider_id': self.accounts[new.pk].pk, 'split_type': 'PERCENT',
                    'split_value': 0, 'penalty_amount': 0}],
            }, format='json'))
        self.login(self.boss)
        stale = self.client.post(first_select, {}, format='json').data
        self.assertNotEqual(stale['code'], 0, stale)
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', self.accounts[self.provider.pk].pk))
        self.assertEqual((order.payment_status, order.amount, order.transactions.count()), before)
        self.assertEqual(order.prestart_handoffs.count(), 2)
        self.assertEqual(OrderApplication.objects.get(pk=first.pk).status, 'SELECTED')

    @skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
    def test_boss_selection_races_applicant_withdrawal(self):
        profile = self.provider.escort_profile
        profile.pass_tier = 'BLACK'
        profile.pass_expires_at = timezone.now() + timedelta(days=1)
        profile.save(update_fields=['pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'selection-withdraw-race',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        application = OrderApplication.objects.get(order_id=order_id)
        barrier = Barrier(2)

        def post(user, path, payload):
            close_old_connections()
            try:
                client = APIClient()
                token, _ = issue_account_tokens(self.accounts[user.pk])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(path, payload, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            selection = pool.submit(post, self.boss,
                f'/api/orders/orders/{order_id}/applications/{application.pk}/select/', {})
            withdrawal = pool.submit(post, self.provider,
                f'/api/orders/orders/{order_id}/applications/{application.pk}/action/', {'action': 'WITHDRAW'})
            results = [selection.result(timeout=30), withdrawal.result(timeout=30)]
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, results)
        application.refresh_from_db()
        order = Order.objects.get(pk=order_id)
        if results[0]['code'] == 0:
            self.assertEqual((order.status, application.status, order.provider_account_id),
                             ('GRABBED', 'SELECTED', self.accounts[self.provider.pk].pk))
        else:
            self.assertEqual((order.status, application.status, order.provider_account_id),
                             ('PENDING', 'WITHDRAWN', None))

    @skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
    def test_boss_selection_races_staff_assignment(self):
        other = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        self.accounts[other.pk] = get_or_create_account_for_legacy_user(other)
        for user in (self.provider, other):
            profile = user.escort_profile
            profile.account = self.accounts[user.pk]
            profile.pass_tier = 'BLACK'
            profile.pass_expires_at = timezone.now() + timedelta(days=1)
            profile.save(update_fields=['account', 'pass_tier', 'pass_expires_at'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_mode': 'BOSS_SELECT',
            'support_contact_id': self.card.pk, 'client_request_id': 'selection-staff-race',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/grab/', {}, format='json'))
        application = OrderApplication.objects.get(order_id=order_id)
        barrier = Barrier(2)

        def post(user, path, payload):
            close_old_connections()
            try:
                client = APIClient()
                token, _ = issue_account_tokens(self.accounts[user.pk])
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                barrier.wait(timeout=10)
                return client.post(path, payload, format='json').data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            selection = pool.submit(post, self.boss,
                f'/api/orders/orders/{order_id}/applications/{application.pk}/select/', {})
            assignment = pool.submit(post, self.admin, f'/api/admin/orders/{order_id}/assign/',
                                     {'provider_id': self.accounts[other.pk].pk})
            results = [selection.result(timeout=30), assignment.result(timeout=30)]
        self.assertEqual(sum(result['code'] == 0 for result in results), 1, results)
        application.refresh_from_db()
        order = Order.objects.get(pk=order_id)
        if results[0]['code'] == 0:
            self.assertEqual((order.status, application.status, order.provider_account_id),
                             ('GRABBED', 'SELECTED', self.accounts[self.provider.pk].pk))
        else:
            self.assertEqual((order.status, application.status, order.provider_account_id),
                             ('GRABBED', 'NOT_SELECTED', self.accounts[other.pk].pk))
