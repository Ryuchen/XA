"""Two providers' actual report reviews gate one order's confirmation and settlement."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.confirmation import process_order
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class TeamReviewRejectJourneyTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_one_rejection_blocks_both_income_and_boss_confirmation_until_corrected(self):
        second = make_provider(display_name='待补证队友')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'client_request_id':'team-review-reject-journey', 'support_contact_id':self.card.pk}, format='json'))
        order_id = data['id']
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':flow.screenshot()}, format='multipart'))
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image':flow.screenshot()}, format='multipart'))
        for user in (self.provider, second):
            self.login(user)
            report = ProviderReport.objects.get(order_id=order_id, provider=user)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        first = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        rejected = ProviderReport.objects.get(order_id=order_id, provider=second)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{first.pk}/approve/', {}, format='json'))
        self.ok(self.client.post(f'/api/admin/reports/{rejected.pk}/reject/', {'audit_remark':'请补充清晰截图'}, format='json'))
        self.login(self.provider)
        approved_view = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id']==first.pk)
        self.assertEqual((approved_view['status'], approved_view['settlement_status']), ('APPROVED', 'WAITING'))
        self.login(second)
        rejected_view = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id']==rejected.pk)
        self.assertEqual(rejected_view['status'], 'REJECTED')
        self.assertTrue(rejected_view['can_edit'])
        self.assertEqual(rejected_view['audit_remark'], '请补充清晰截图')
        self.assertTrue(any(row['title']=='报单审核未通过' and row['related_order_id']==order_id
                            for row in self.ok(self.client.get('/api/messages/'))['messages']))
        self.login(self.boss)
        boss = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertIsNone(boss['consumption_confirmed_at'])
        self.assertEqual(boss['status'], 'COMPLETED')
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
        blocked = Order.objects.get(pk=order_id)
        self.assertIsNone(blocked.settled_at)
        self.assertIsNone(blocked.consumption_confirmed_at)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
        self.login(second)
        self.ok(self.client.post(f'/api/wallet/reports/{rejected.pk}/images/', {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{rejected.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{rejected.pk}/approve/', {}, format='json'))
        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        order = Order.objects.get(pk=order_id)
        self.assertIsNotNone(order.consumption_confirmed_at)
        self.assertIsNotNone(order.settled_at)
        shares = list(order.providers.filter(released_at__isnull=True))
        self.assertEqual(len(shares), 2)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
        self.login(self.boss)
        self.assertIsNotNone(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['consumption_confirmed_at'])
        self.login(self.admin)
        self.assertEqual(self.ok(self.client.get(f'/api/admin/orders/{order_id}/'))['settlement_status'], 'SETTLED')
        for user in (self.provider, second):
            self.login(user)
            report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['order_id']==order_id)
            self.assertEqual((report['status'], report['settlement_status']), ('APPROVED', 'SETTLED'))
        for share in shares:
            self.assertEqual(Wallet.objects.get(user_id=share.provider_id).balance, share.provider_income)
            self.assertEqual(Transaction.objects.filter(order=order, wallet__user_id=share.provider_id, tx_type='INCOME').count(), 1)
