"""Native WeChat IDE receipt against isolated real settlement data."""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.models import VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import get_platform_wallet


@skipUnless(os.environ.get('XA_WECHAT_SUBSIDY_IDE') == '1', 'Requires an open isolated WeChat IDE simulator')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class VipSubsidyWechatIDETest(LiveServerTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_waiting_and_settled_receipts_on_iphone12(self):
        media = TemporaryDirectory(prefix='xa-ide-subsidy-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        service = make_service(name='小程序验收安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier_type = make_boss_type(name='小程序六级会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier_type, level=6, recharge_threshold=100, safe_box_service_ids=[service.pk])
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        player = make_provider(display_name='小程序验收陪玩')
        player_account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = player_account
        player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        operator, provider = self.client_for(staff_account), self.client_for(player_account)
        other = make_user()
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(other, other_account)
        make_wallet(other, balance=0)
        cases = []
        for index, buyer in enumerate((self.account, other_account)):
            buyer.boss_type = tier_type
            buyer.save(update_fields=['boss_type'])
            grant_periodic(buyer)
            voucher = UserCoupon.objects.get(account=buyer, coupon__scope='SAFE_BOX')
            client = self.client_for(buyer)
            data = self.ok(client.post('/api/orders/create/', {'service_id': service.pk, 'user_coupon_id': voucher.pk,
                'dispatch_route': 'STAFF', 'client_request_id': f'ide-subsidy-{index}'}, format='json'))
            self.ok(operator.post(f"/api/admin/orders/{data['id']}/assign/", {'provider_id': player_account.pk}, format='json'))
            self.ok(provider.post(f"/api/orders/orders/{data['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
            platform = get_platform_wallet()
            platform.balance = 7999 if index == 0 else 8000
            platform.save(update_fields=['balance'])
            base = f"/api/admin/orders/{data['id']}/"
            share = self.ok(operator.get(base + 'subsidy-negotiation-options/'))['shares'][0]
            allocations = [{'share_id': share['share_id'], 'amount': 8000}]
            plan = self.ok(operator.post(base + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
            self.ok(operator.post(base + 'subsidy-negotiation-confirm/', {'allocations': allocations,
                'plan_token': plan['plan_token'], 'client_request_id': f'ide-confirm-{index}',
                'reason': '双方协商终止', 'reviewed': True}, format='json'))
            order = Order.objects.get(pk=data['id'])
            self.assertEqual(bool(order.settled_at), index == 1)
            cases.append({'orderId': order.pk, 'bossId': buyer.pk, 'token': issue_account_tokens(buyer)[0],
                          'expected': '报酬结算后，按会员权益发放下一张券' if index == 0 else '报酬已结算，会员权益可在我的券查看'})
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/native-vip-subsidy-ide.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'cases': cases, 'screenshot': str(repo / '.tools/vip-subsidy-ide-iphone12.png')}),
            text=True, encoding='utf-8', capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


del CouponAvailabilityWorkflowTest
