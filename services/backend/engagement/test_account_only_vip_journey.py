"""A new boss earns VIP through the actual paid-service workflow."""
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import UserCoupon
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AccountOnlyVipJourneyTest(APITransactionTestCase):
    def test_recharge_consumption_confirmation_and_vip_vouchers(self):
        media = TemporaryDirectory(prefix='xa-vip-account-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = ClubAccount.objects.create_account(username='vip-journey-boss', account_type='BOSS')
        staff = make_console_user(['wallet:recharge', 'wallet:view', 'order:view', 'report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='会员订单陪玩')
        player = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = player; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='会员成长服务', price=10000, commission_rate=20)
        safe_service = make_service(name='会员安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        provider.escort_profile.service_items.add(service)
        tier = make_boss_type(name='真实成长会员', discount_rate=90)
        VipTier.objects.create(level=6, boss_type=tier, recharge_threshold=10000,
                               safe_box_service_ids=[safe_service.pk])

        def client(account):
            result = APIClient(); result.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); return result
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')
        owner, operator, worker = client(boss), client(staff_account), client(player)
        ok(operator.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 20000,
            'gift_amount': 0, 'trade_no': 'vip-journey-receipt', 'client_request_id': 'vip-journey-recharge',
            'proof_image': screenshot()}, format='multipart'))
        before = ok(owner.get('/api/club/me/'))
        self.assertEqual((before['recharge_amount'], before['consumption_amount'], before['vip']), (20000, 0, None))
        order = ok(owner.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': player.pk,
            'client_request_id': 'vip-journey-order'}, format='json'))
        for action, field in [('start', 'entry_image'), ('complete', 'completion_image')]:
            ok(worker.post(f"/api/orders/orders/{order['id']}/{action}/", {field: screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order['id'])
        ok(worker.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        ok(worker.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        pending = ok(owner.get('/api/club/me/'))
        self.assertEqual((pending['consumption_amount'], pending['pending_consumption_amount'], pending['vip']), (0, 10000, None))
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order['id']).update(completed_at=old)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
        self.assertTrue(process_order(order['id']))
        current = ok(owner.get('/api/club/me/'))
        self.assertEqual((current['consumption_amount'], current['vip']['level']), (10000, 6))
        admin_wallet = ok(operator.get(f'/api/admin/wallets/{Wallet.objects.get(account=boss).pk}/'))
        self.assertEqual((admin_wallet['account_id'], admin_wallet['boss_type_name']), (boss.pk, tier.name))
        self.assertEqual(ok(worker.get('/api/wallet/info/'))['balance'], 8000)
        coupons = ok(owner.get('/api/coupons/mine/'))
        self.assertEqual(sum(row['scope'] == 'SAFE_BOX' and row['service_ids'] == [safe_service.pk] for row in coupons), 1)
        self.assertEqual(UserCoupon.objects.filter(account=boss, coupon__scope='COMPANION', user__isnull=True).count(), 4)
        self.assertEqual(Wallet.objects.get(account=player).balance, 8000)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order_id=order['id']).count(), 1)
        self.assertFalse(process_order(order['id']))
        self.assertFalse(LegacyAccountMap.objects.filter(account=boss).exists())
