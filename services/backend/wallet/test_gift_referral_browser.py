"""Real recharge, direct gift and refund viewed by its recipient/referrer."""
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftReferralBrowserTest(LiveServerTestCase):
    @skipUnless(os.environ.get('XA_GIFT_REFERRAL_FORMAL') == '1',
                '礼物推荐分佣计提与受赠人/推荐人退款金额分离属礼物资金契约深水区（18-盘点订单售后资金组合）；最终联调启用')
    def test_recipient_and_referrer_see_separate_refund_amounts(self):
        media = TemporaryDirectory(prefix='xa-gift-referral-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider(deposit_mode='CASH')
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)]
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = accounts[2]
        staff.admin_membership.save(update_fields=['account'])
        boss.inviter, boss.inviter_commission_rate = provider, 50
        boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        gift = make_service(name='推荐人礼物', price=10000, commission_rate=20,
                            service_category=ServiceCategory.objects.create(name='礼物', is_gift=True))
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        ok(clients[2].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
            'amount': 20000, 'gift_amount': 0, 'trade_no': 'gift-referral-recharge',
            'proof_image': screenshot(), 'client_request_id': 'gift-referral-recharge'}, format='multipart'))
        order_id = ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id': gift.pk,
            'quantity': 1, 'provider_id': accounts[1].pk, 'expected_amount': 10000,
            'client_request_id': 'gift-referral-send'}, format='json'))['id']
        order = Order.objects.get(pk=order_id)
        self.assertGreater(order.inviter_commission, 0)
        preview = ok(clients[2].get(f'/api/admin/orders/{order_id}/refund-preview/', {'refund_amount': 2500}))
        ok(clients[2].post(f'/api/admin/orders/{order_id}/refund/', {'refund_amount': 2500,
            'reason': '隔离部分退款', 'client_request_id': 'gift-referral-partial',
            'plan_token': preview['plan_token']}, format='json'))
        summary = ok(clients[1].get(f'/api/orders/orders/{order_id}/'))['refund_summary']
        self.assertEqual((summary['net_income'], summary['recovered_amount']), (6000, 2000))
        self.assertGreater(summary['referral_net_income'], 0)
        self.assertGreater(summary['referral_recovered_amount'], 0)
        benefit = ok(clients[1].get('/api/wallet/provider-benefits/'))['gift_records'][0]
        self.assertEqual(benefit['received_income'], summary['net_income'])
