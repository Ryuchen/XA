"""Two devices make real concurrent claims against MySQL."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.test import LiveServerTestCase, override_settings, skipUnlessDBFeature
from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class AccountOnlyCouponConcurrencyTest(LiveServerTestCase):
    def test_two_devices_claim_once_and_last_coupon_is_not_oversold(self):
        first = ClubAccount.objects.create_account(username='concurrent-coupon-one', account_type='BOSS')
        second = ClubAccount.objects.create_account(username='concurrent-coupon-two', account_type='BOSS')
        first_token, second_token = issue_account_tokens(first)[0], issue_account_tokens(second)[0]

        def race(coupon, tokens):
            barrier = threading.Barrier(2)
            def send(token):
                request = Request(f'{self.live_server_url}/api/coupons/{coupon.pk}/claim/', data=b'{}',
                    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}, method='POST')
                barrier.wait(timeout=10)
                try:
                    with urlopen(request, timeout=20) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.loads(error.read())
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(send, tokens))

        duplicate = Coupon.objects.create(name='双设备优惠券', discount_type='DIRECT', amount=100, total_qty=10)
        replies = race(duplicate, [first_token, first_token])
        self.assertEqual(sum(status == 200 and body['code'] == 0 for status, body in replies), 1, replies)
        self.assertTrue(all(status < 500 for status, _ in replies), replies)
        duplicate.refresh_from_db()
        self.assertEqual((duplicate.claimed_qty, UserCoupon.objects.filter(account=first, coupon=duplicate).count()), (1, 1))
        last = Coupon.objects.create(name='最后一张优惠券', discount_type='DIRECT', amount=100, total_qty=1)
        replies = race(last, [first_token, second_token])
        self.assertEqual(sum(status == 200 and body['code'] == 0 for status, body in replies), 1, replies)
        self.assertTrue(all(status < 500 for status, _ in replies), replies)
        last.refresh_from_db()
        self.assertEqual((last.claimed_qty, UserCoupon.objects.filter(coupon=last).count()), (1, 1))
        self.assertFalse(LegacyAccountMap.objects.filter(account__in=[first, second]).exists())
