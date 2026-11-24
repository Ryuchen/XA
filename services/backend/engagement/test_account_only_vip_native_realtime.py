"""Confirmed order maturation upgrades VIP, re-prices checkout and propagates tier edits."""
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import MemberTag, VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from stores.context import resolve_store, store_context
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AccountOnlyVipNativeRealtimeTest(LiveServerTestCase):
    def test_open_pages_follow_confirmed_order_without_losing_edits(self):
        self.enterContext(store_context(resolve_store()))
        media = TemporaryDirectory(prefix='xa-vip-live-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = ClubAccount.objects.create_account(username='native-vip-confirm', account_type='BOSS')
        staff = make_console_user(['wallet:recharge', 'order:view', 'report:view', 'report:audit', 'engagement:view', 'engagement:edit'])
        admin = get_or_create_account_for_legacy_user(staff); staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(deposit_mode='EARNINGS'); player = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = player; provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000, commission_rate=20); provider.escort_profile.service_items.add(service)
        tier = make_boss_type(name='实时成长会员', discount_rate=90)
        vip = VipTier.objects.create(level=6, boss_type=tier, recharge_threshold=10000)
        MemberTag.objects.create(account=boss, event_key='native-vip-tag', title='体验标签')
        def client(account):
            host = 'admin.esports.club' if account.account_type == 'STAFF' else 'sl.esports.club'
            result = APIClient(HTTP_HOST=host); result.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); return result
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data); self.assertEqual(response.data['code'], 0, response.data); return response.data.get('data')
        owner, operator, worker = client(boss), client(admin), client(player)
        ok(operator.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'native-vip-receipt', 'client_request_id': 'native-vip-recharge', 'proof_image': screenshot()}, format='multipart'))
        order = ok(owner.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': player.pk,
            'client_request_id': 'native-vip-order'}, format='json'))
        for action, field in [('start', 'entry_image'), ('complete', 'completion_image')]:
            ok(worker.post(f"/api/orders/orders/{order['id']}/{action}/", {field: screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order['id'])
        ok(worker.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        ok(worker.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order['id']).update(completed_at=old)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
        member = ok(owner.get('/api/club/me/'))
        self.assertIsNone(member['vip'])
        quote = ok(owner.post('/api/orders/quote/', {'service_id': service.pk, 'provider_id': player.pk}, format='json'))
        self.assertEqual(quote['amount'], 10000)
        self.assertTrue(process_order(order['id']))
        member = ok(owner.get('/api/club/me/'))
        self.assertEqual(member['vip']['level'], 6)
        self.assertEqual(member['consumption_amount'], 10000)
        quote = ok(owner.post('/api/orders/quote/', {'service_id': service.pk, 'provider_id': player.pk}, format='json'))
        self.assertEqual(quote['amount'], 9000)
        member = ok(owner.patch('/api/club/me/', {'birthday_public': False}, format='json'))
        self.assertFalse(member['birthday_public'])
        self.assertEqual(member['vip']['level'], 6)
        ok(operator.patch(f'/api/admin/vip-tiers/{vip.pk}/', {'benefits': {'专属说明': '配置更新'}}, format='json'))
        member = ok(owner.get('/api/club/me/'))
        self.assertEqual(member['vip']['benefits']['专属说明'], '配置更新')