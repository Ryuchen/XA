"""The native coupon controller uses real isolated accounts and HTTP business logic."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.test import LiveServerTestCase, override_settings, skipUnlessDBFeature

from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class NativeClaimRaceTest(LiveServerTestCase):
    def test_account_switch_and_lost_committed_response(self):
        self.run_native_claim_flow(account_only=False)

    def test_account_only_switch_and_lost_committed_response(self):
        self.run_native_claim_flow(account_only=True)

    def run_native_claim_flow(self, *, account_only):
        accounts = ([ClubAccount.objects.create_account(username=f'native-claim-only-{index}', account_type='BOSS') for index in range(2)]
                    if account_only else [get_or_create_account_for_legacy_user(make_user()) for _ in range(2)])
        coupons = [Coupon.objects.create(name=name, amount=100) for name in ('隔离优惠券', '隔离补充券')]
        tokens = [issue_account_tokens(account)[0] for account in accounts]

        def claim(token, coupon_id):
            request = Request(f'{self.live_server_url}/api/coupons/{coupon_id}/claim/', data=b'{}',
                headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
            try:
                with urlopen(request, timeout=20) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, json.loads(error.read())

        barrier = threading.Barrier(2)
        def race(token):
            barrier.wait(timeout=10)
            return claim(token, coupons[0].pk)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(race, tokens))
        self.assertTrue(all(status == 200 and body['code'] == 0 for status, body in replies), replies)
        status, body = claim(tokens[1], coupons[1].pk)
        self.assertEqual((status, body['code']), (200, 0), body)
        status, replay = claim(tokens[1], coupons[1].pk)
        self.assertEqual((status, replay['code'], replay['data']['replayed']), (200, 0, True), replay)
        self.assertEqual(UserCoupon.objects.filter(coupon=coupons[0]).count(), 2)
        self.assertEqual(UserCoupon.objects.filter(coupon=coupons[1], account=accounts[1]).count(), 1)
        coupons[0].refresh_from_db()
        coupons[1].refresh_from_db()
        self.assertEqual(coupons[0].claimed_qty, 2)
        self.assertEqual(coupons[1].claimed_qty, 1)
        if account_only:
            self.assertFalse(LegacyAccountMap.objects.filter(account__in=accounts).exists())
            self.assertFalse(UserCoupon.objects.filter(account__in=accounts).exclude(user__isnull=True).exists())