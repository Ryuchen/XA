"""A historical boss account keeps issued VIP rights across a refund."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.models import LegacyAccountMap
from engagement.confirmation import process_order
from engagement.models import RewardClaim, RewardItem, VipTier
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_boss_type
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountOnlyVipRefundNoticeTest(APITransactionTestCase):
    def setUp(self):
        super().setUp()
        flow.ReleaseFlowTest.setUp(self)
        profile = self.provider.escort_profile
        profile.deposit_paid = profile.effective_deposit_required
        profile.save(update_fields=['deposit_paid'])

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def approve(self, order):
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order.pk).update(completed_at=old)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)

    def test_refund_and_requalification_preserve_same_current_account_right(self):
        reward = RewardItem.objects.create(code='account-only-vip-notice', name='隔离会员权益', kind='SERVICE', in_shop=False)
        VipTier.objects.create(level=1, boss_type=make_boss_type(), recharge_threshold=1000,
                               upgrade_rewards=[{'reward':reward.pk, 'quantity':1}])
        self.admin.admin_membership.roles.update(permissions=['wallet:recharge', 'report:view', 'report:audit',
            'order:dispatch', 'order:view', 'order:refund', 'order:settle', 'engagement:fulfill'])
        first = self.new_order()
        self.approve(first)
        self.assertTrue(process_order(first.pk))
        claim = RewardClaim.objects.get(account=self.accounts[self.boss.pk], reward=reward)
        self.assertEqual(claim.status, 'PENDING')

        self.login(self.boss)
        second = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'support_contact_id':self.card.pk,
            'client_request_id':'account-only-vip-second'}, format='json'))
        second = Order.objects.get(pk=second['id'])
        self.approve(second)
        # Both orders retain an explicit business owner after the historical mapping is removed.
        account = self.accounts[self.boss.pk]
        LegacyAccountMap.objects.filter(account=account).delete()
        self.login(self.admin)
        preview = self.ok(self.client.get(f'/api/admin/orders/{first.pk}/refund-preview/'))
        self.ok(self.client.post(f'/api/admin/orders/{first.pk}/refund/', {'reason':'隔离会员退款',
            'plan_token':preview['plan_token'], 'client_request_id':'account-only-vip-refund'}, format='json'))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'PENDING')
        self.assertTrue(process_order(second.pk))
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'PENDING')
        self.login(self.boss)
        notices = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertFalse([row for row in notices if row['title'] in ('权益状态已调整', '权益已恢复')])
        self.assertEqual(RewardClaim.objects.filter(account=account, reward=reward).count(), 1)
