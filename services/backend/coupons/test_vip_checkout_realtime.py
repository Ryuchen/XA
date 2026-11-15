"""Open native checkout responds to a real staff tier change."""
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_console_user, make_service, make_user, make_wallet
from users.test_native_checkin_order_realtime import isolated_redis_asgi


@skipUnless(os.environ.get('XA_VIP_CHECKOUT_REALTIME') == '1', 'Requires isolated Redis and ASGI')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class VipCheckoutRealtimeTest(LiveServerTestCase):
    def test_account_only_checkout_requotes_after_staff_changes_discount(self):
        from wallet.services import get_wallet
        account = ClubAccount.objects.create_account(username='vip-price-account-only', account_type='BOSS')
        tier = make_boss_type(name='隔离结算折扣', discount_rate=90)
        account.boss_type = tier; account.save(update_fields=['boss_type'])
        wallet = get_wallet(account=account); wallet.balance = 50000; wallet.save(update_fields=['balance'])
        staff = make_console_user(['boss_type:view', 'boss_type:edit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        service = make_service(name='隔离折扣服务', price=10000)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/native-checkout-coupon-live.cjs')], cwd=repo,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'serviceId': service.pk, 'bossTypeId': tier.pk,
                    'adminToken': issue_account_tokens(staff_account)[0],
                    'session': {'token': issue_account_tokens(account)[0], 'user': {'id': account.pk, 'role': 'customer'}}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(LegacyAccountMap.objects.filter(account=account).exists())
        from orders.models import Order
        self.assertEqual(Order.objects.get(customer_account=account).amount, 8000)

    def test_open_checkout_drops_removed_binding_and_requotes(self):
        media = TemporaryDirectory(prefix='xa-vip-checkout-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        link_legacy_relations(boss, account)
        make_wallet(boss, balance=50000)
        staff = make_console_user(['engagement:view', 'engagement:edit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        old = make_service(name='原结算安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        new = make_service(name='新结算安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC')
        tier_type = make_boss_type(name='结算页六级会员', discount_rate=90)
        tier = VipTier.objects.create(boss_type=tier_type, level=6, recharge_threshold=100, safe_box_service_ids=[old.pk])
        account.boss_type = tier_type
        account.save(update_fields=['boss_type'])
        grant_periodic(account)
        voucher = UserCoupon.objects.get(account=account, coupon__scope='SAFE_BOX')
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi() as (base, ws_base):
            result = subprocess.run(['node', str(repo / 'scripts/native-checkout-coupon-live.cjs')], cwd=repo,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'serviceId': old.pk, 'voucherId': voucher.pk,
                    'vipTierId': tier.pk, 'replacementServiceId': new.pk,
                    'adminToken': issue_account_tokens(staff_account)[0],
                    'session': {'token': issue_account_tokens(account)[0], 'user': {'id': account.pk, 'role': 'customer'}}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'UNUSED')
        self.assertEqual(voucher.coupon.service_ids, [new.pk])
