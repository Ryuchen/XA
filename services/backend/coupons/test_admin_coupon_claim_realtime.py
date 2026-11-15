"""Customer service sees actual coupon claims while the records dialog is open."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_ADMIN_COUPON_ROOT'), 'requires isolated admin build and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminCouponClaimRealtimeTest(LiveServerTestCase):
    def test_boss_claim_updates_staff_list_and_open_records(self):
        self.run_claim_flow(account_only=False)

    def test_account_only_boss_claim_updates_staff_records(self):
        self.run_claim_flow(account_only=True)

    def run_claim_flow(self, *, account_only):
        staff = make_console_user(['coupon:view'])
        editor = make_console_user(['coupon:view', 'coupon:edit', 'coupon:delete'])
        if account_only:
            boss_account = ClubAccount.objects.create_account(username='account-only-claim', account_type='BOSS', nickname='新账号领券老板')
            from wallet.services import get_wallet
            wallet = get_wallet(account=boss_account); wallet.balance = 50000; wallet.save(update_fields=['balance'])
        else:
            boss = make_user(nickname='实时领券老板')
            boss_account = get_or_create_account_for_legacy_user(boss)
            link_legacy_relations(boss, boss_account)
            make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        editor_account = get_or_create_account_for_legacy_user(editor)
        editor.admin_membership.account = editor_account
        editor.admin_membership.save(update_fields=['account'])
        coupon = Coupon.objects.create(name='客服实时领券', discount_type='DIRECT', amount=500, total_qty=3)
        root = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi() as (base, ws_base):
            result = subprocess.run(['node', str(root / 'scripts/admin-coupon-claim-live.cjs')], cwd=root,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'root': os.environ['XA_ADMIN_COUPON_ROOT'],
                    'couponId': coupon.pk, 'serviceId': service.pk, 'bossToken': issue_account_tokens(boss_account)[0],
                    'staffToken': issue_account_tokens(staff_account)[0],
                    'editorToken': issue_account_tokens(editor_account)[0],
                    'bossName': boss_account.nickname, 'mobile': account_only}),
                text=True, encoding='utf-8', capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        coupon.refresh_from_db()
        self.assertEqual(coupon.claimed_qty, 1)
        self.assertTrue(UserCoupon.objects.filter(account=boss_account, coupon=coupon).exists())
        self.assertEqual(UserCoupon.objects.get(account=boss_account, coupon=coupon).status, 'UNUSED')
        if account_only:
            self.assertFalse(LegacyAccountMap.objects.filter(account=boss_account).exists())
            self.assertIsNone(UserCoupon.objects.get(account=boss_account, coupon=coupon).user_id)
