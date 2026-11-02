"""The native coupon controller uses real isolated accounts and HTTP business logic."""
import json
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class NativeClaimRaceTest(LiveServerTestCase):
    def test_account_switch_and_lost_committed_response(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        accounts = [get_or_create_account_for_legacy_user(make_user()) for _ in range(2)]
        coupons = [Coupon.objects.create(name=name, amount=100) for name in ('隔离优惠券', '隔离补充券')]
        sessions = []
        for account in accounts:
            token, refresh = issue_account_tokens(account)
            sessions.append({'token': token, 'refresh': refresh,
                             'user': {'id': str(account.pk), 'role': 'customer'}})
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/native-coupon-claim-race-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'couponId': coupons[0].pk,
                'otherCouponId': coupons[1].pk, 'first': sessions[0], 'second': sessions[1]}),
            text=True, encoding='utf-8', capture_output=True, timeout=40, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(UserCoupon.objects.filter(coupon=coupons[0]).count(), 2)
        self.assertEqual(UserCoupon.objects.filter(coupon=coupons[1], account=accounts[1]).count(), 1)
        coupons[0].refresh_from_db()
        coupons[1].refresh_from_db()
        self.assertEqual(coupons[0].claimed_qty, 2)
        self.assertEqual(coupons[1].claimed_qty, 1)
