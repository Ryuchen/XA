"""Starting a reassigned order notifies its current provider, not a former rejecter."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderApplication, OrderProvider
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class StaffStartRecipientScopeTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order

    def test_rejected_provider_does_not_get_new_start_notice(self):
        incoming = make_provider(display_name='重新选择的陪玩')
        self.accounts[incoming.pk] = get_or_create_account_for_legacy_user(incoming)
        incoming.escort_profile.account = self.accounts[incoming.pk]
        incoming.escort_profile.save(update_fields=['account'])
        incoming.escort_profile.service_items.add(self.service)
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/reject/', {'reason':'临时不能服务'}, format='json'))
        self.assertTrue(OrderProvider.objects.get(order=order, provider=self.provider).released_at)
        # Advance only the pool visibility clock in this isolated test database.
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now()-timedelta(hours=1))
        self.login(incoming)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size':1}, format='json'))
        application = OrderApplication.objects.get(order=order, applicant=self.accounts[incoming.pk], status='PENDING')
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json'))
        self.login(self.admin)
        started = self.ok(self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json'))
        self.assertEqual(started['status'], 'IN_SERVICE')
        self.assertNotEqual(self.client.post(f'/api/admin/orders/{order.pk}/start/', {}, format='json').data['code'], 0)
        for user in (self.boss, incoming, self.admin):
            self.login(user)
            path = f'/api/admin/orders/{order.pk}/' if user == self.admin else f'/api/orders/orders/{order.pk}/'
            self.assertEqual(self.ok(self.client.get(path))['status'], 'IN_SERVICE')
        for user in (self.provider, incoming):
            self.login(user)
            messages = self.ok(self.client.get('/api/messages/'))['messages']
            starts = [row for row in messages if row['title']=='订单已开始服务' and row['related_order_id']==order.pk]
            self.assertEqual(len(starts), 0 if user == self.provider else 1, messages)
        self.assertTrue(OrderProvider.objects.get(order=order, provider=self.provider).released_at)
        self.assertIsNone(OrderProvider.objects.get(order=order, provider=incoming).released_at)

    def test_both_current_team_members_receive_notice(self):
        teammate = make_provider(display_name='当前队友')
        self.accounts[teammate.pk] = get_or_create_account_for_legacy_user(teammate)
        teammate.escort_profile.account = self.accounts[teammate.pk]
        teammate.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        self.provider.escort_profile.service_items.add(self.service)
        teammate.escort_profile.service_items.add(self.service)
        self.ok(self.recharge()); self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'client_request_id':'team-start-notice'}, format='json'))
        self.login(self.admin)
        self.assertEqual(self.ok(self.client.post(f"/api/admin/orders/{order['id']}/start/", {}, format='json'))['status'], 'IN_SERVICE')
        for user in (self.provider, teammate):
            self.login(user)
            messages = self.ok(self.client.get('/api/messages/'))['messages']
            self.assertEqual(len([row for row in messages if row['title']=='订单已开始服务' and row['related_order_id']==order['id']]), 1)
            self.assertEqual(self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['status'], 'IN_SERVICE')
