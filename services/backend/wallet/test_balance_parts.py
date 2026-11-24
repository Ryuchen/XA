"""老板钱包三类可用余额必须来自真实资金批次，而非累计充值字段。"""
from io import BytesIO
import os
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.test import TransactionTestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot, RechargePackage
from orders.tests.factories import make_console_user, make_provider, make_service
from wallet.services import get_wallet


@override_settings(
    LEGACY_FORCE_AUTH_COMPAT=False,
    REQUIRE_RECHARGE_PROOF=True,
    REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    KOOK_ENABLED=False,
    WECOM_DISPATCH_ENABLED=False,
)
class WalletBalancePartsTest(TransactionTestCase):
    def setUp(self):
        self.media = TemporaryDirectory(prefix='xa-wallet-parts-')
        self.addCleanup(self.media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))
        self.boss = ClubAccount.objects.create_account(
            username='isolated-wallet-parts-boss', account_type='BOSS',
        )
        self.staff = make_console_user([
            'wallet:view', 'wallet:recharge', 'wallet:adjust',
            'order:view', 'order:cancel', 'order:refund',
        ])
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        self.owner = APIClient()
        self.owner.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss)[0])
        self.operator = APIClient()
        self.operator.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff_account)[0])
        self.provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        self.provider_account = get_or_create_account_for_legacy_user(self.provider)
        self.provider.escort_profile.account = self.provider_account
        self.provider.escort_profile.save(update_fields=['account'])
        self.wallet = get_wallet(account=self.boss)
        RechargePackage.objects.create(amount=1000, gift_amount=100, active=True)
        self.sequence = 0

    def next_id(self, label):
        self.sequence += 1
        return f'wallet-parts-{label}-{self.sequence}'

    def recharge(self, amount=1000, gift=100):
        image = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image, format='PNG')
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.operator.post('/api/admin/wallets/recharge/', {
            'account_id': self.boss.pk,
            'amount': amount,
            'gift_amount': gift,
            'trade_no': self.next_id('receipt'),
            'client_request_id': self.next_id('recharge'),
            'proof_image': SimpleUploadedFile('receipt.png', image.getvalue(), content_type='image/png'),
        }, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)

    def adjust(self, amount, kind):
        response = self.operator.post(f'/api/admin/wallets/{self.wallet.pk}/adjust/', {
            'amount': amount,
            'balance_kind': kind,
            'remark': '隔离资金批次调账',
            'client_request_id': self.next_id('adjust'),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

    def pay(self, amount):
        service = make_service(name='隔离三类余额服务', price=amount)
        response = self.owner.post('/api/orders/create/', {
            'service_id': service.pk,
            'provider_id': self.provider_account.pk,
            'client_request_id': self.next_id('order'),
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']['id']

    def assertParts(self, balance, prepaid, gift, compensation):
        expected = {
            'balance': balance,
            'prepaid_balance': prepaid,
            'gift_balance': gift,
            'compensation_balance': compensation,
        }
        info_response = self.owner.get('/api/wallet/info/')
        history_response = self.owner.get('/api/wallet/transactions/')
        self.assertEqual(info_response.data['code'], 0, info_response.data)
        self.assertEqual(history_response.data['code'], 0, history_response.data)
        for response in (info_response, history_response):
            actual = {key: response.data['data'][key] for key in expected}
            self.assertEqual(actual, expected)
            self.assertEqual(
                sum(actual[key] for key in ('prepaid_balance', 'gift_balance', 'compensation_balance')),
                actual['balance'],
            )

    def test_recharge_payment_and_cancel_refund_restore_original_parts(self):
        self.recharge(amount=1000, gift=100)
        self.assertParts(1100, 1000, 100, 0)
        order_id = self.pay(500)
        self.assertParts(600, 500, 100, 0)
        preview = self.operator.get(f'/api/admin/orders/{order_id}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refunded = self.operator.post(f'/api/admin/orders/{order_id}/refund/', {
            'reason': '隔离三类余额退款',
            'plan_token': preview.data['data']['plan_token'],
            'client_request_id': self.next_id('refund'),
        }, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        self.assertParts(1100, 1000, 100, 0)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '赠送/赔付与本金共享 FIFO 支付普通订单须混合资金选择契约（现行普通单本金优先，18-盘点订单售后资金组合）；最终联调启用')
    def test_compensation_and_gift_share_the_existing_fifo_and_refund_ledger(self):
        self.recharge(amount=1000, gift=100)
        self.adjust(300, PointFundingLot.Kind.COMPENSATION)
        self.adjust(50, PointFundingLot.Kind.GIFT)
        self.assertParts(1450, 1000, 150, 300)
        order_id = self.pay(1200)
        self.assertParts(250, 0, 50, 200)
        preview = self.operator.get(f'/api/admin/orders/{order_id}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        refunded = self.operator.post(f'/api/admin/orders/{order_id}/refund/', {
            'reason': '隔离三类余额退款',
            'plan_token': preview.data['data']['plan_token'],
            'client_request_id': self.next_id('refund'),
        }, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        self.assertParts(1450, 1000, 150, 300)

    def test_negative_adjustment_reconciles_tracked_lots_immediately(self):
        self.recharge(amount=1000, gift=100)
        self.adjust(300, PointFundingLot.Kind.COMPENSATION)
        self.adjust(-250, PointFundingLot.Kind.PREPAID)
        self.assertParts(1150, 750, 100, 300)
