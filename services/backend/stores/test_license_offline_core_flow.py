"""Full control-DB outage against three disposable, physically distinct schemas."""
import os
from unittest import skipUnless
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount, LicenseStatusSnapshot
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from orders.models import Order
from orders.tests import test_service_funding_api as funding_fixture
from promotions.models import Promotion
from stores.context import resolve_store, store_context
from stores.models import Store
from wallet.models import Transaction


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, PLATFORM_IDENTITY_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=True, LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True,
    LICENSE_SNAPSHOT_CACHE_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    DISCOUNT_OPTIONS_PREVIEW_ENABLED=True, PROVIDER_WECHAT_GATE_ENABLED=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LicenseOfflineCoreFlowTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_missing_license_cache_keeps_existing_order_wallet_coupon_and_fulfillment(self):
        now = timezone.now()
        store = Store.objects.create(slug='xinan', name='兴安电竞', currency_name='星辉',
            database_alias='store_b', status='ACTIVE', initialized_at=now)
        with store_context(store), override_settings(LICENSE_ACCOUNT_ADMISSION_ENABLED=False):
            funding_fixture.ServiceFundingApiTest.setUp(self)
            self.provider.openid = 'fixture-verified-provider-wechat'
            self.provider.is_openid_bound = True
            self.provider.save(update_fields=['openid', 'is_openid_bound'])
            profile = self.provider_user.escort_profile
            profile.wechat_avatar_verified_at = now
            profile.save(update_fields=['wechat_avatar_verified_at'])
            coupon = Coupon.objects.create(name='已有券', discount_type='DIRECT', amount=100)
            owned = UserCoupon.objects.create(account=self.account, user=self.user, coupon=coupon)
            Promotion.objects.create(title='停发活动', discount_rate=80, scope='ALL',
                start_at=now - timedelta(days=1), end_at=now + timedelta(days=30))
            self.boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
            LicenseStatusSnapshot.objects.all().delete()
        # Only the signed routing snapshot is available; the License snapshot is absent.
        resolve_store('xinan')
        codes = []

        def fail_control(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated control outage')

        with connections['default'].execute_wrapper(fail_control):
            self.assertEqual(self.boss.get('/api/wallet/info/').status_code, 200)
            preview = self.boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
            self.assertEqual(preview.data['code'], 0, preview.data)
            self.assertFalse(any(row['promotion_id'] for row in preview.data['data']['options']))
            self.assertTrue(any(row['coupon_id'] == owned.pk for row in preview.data['data']['options']))
            payload = funding_fixture.ServiceFundingApiTest.payload(self, user_coupon_id=owned.pk)
            result = self.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            with store_context(store):
                order = Order.objects.get(pk=result.data['data']['id'])
                self.assertEqual(order.amount, 900)
                self.assertTrue(Transaction.objects.filter(order=order, tx_type='PAY', amount=-900).exists())
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {}, format='json')
            self.assertEqual(started.data['code'], 0, started.data)
            with store_context(store):
                self.assertEqual(Order.objects.get(pk=order.pk).status, 'IN_SERVICE')

            anonymous = APIClient(HTTP_HOST='xa.esports.club')
            with patch('users.boss_registration.send_verification_code', side_effect=lambda phone, code: codes.append(code)):
                challenge = anonymous.post('/api/users/boss-registration/sms/', {'phone': '13900001111'}, format='json')
            self.assertEqual(challenge.status_code, 200, challenge.data)
            denied = anonymous.post('/api/users/boss-registration/', {'smsChallenge': challenge.data['data']['smsChallenge'],
                'smsCode': codes[0], 'username': 'offline-new-boss', 'password': 'Example-Password-2026'}, format='json')
            self.assertEqual(denied.status_code, 503, denied.data)
            with store_context(store):
                self.assertFalse(ClubAccount.objects.filter(username='offline-new-boss').exists())
