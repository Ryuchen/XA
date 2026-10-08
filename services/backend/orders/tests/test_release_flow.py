import io
import tempfile
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from support.models import SupportContactCard
from wallet.models import ProviderReport, RechargeRecord, Transaction, Wallet


def screenshot():
    output = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(output, format='PNG')
    return SimpleUploadedFile('evidence.png', output.getvalue(), content_type='image/png')


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False)
class ReleaseFlowTest(APITestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.boss = make_user()
        self.provider = make_provider()
        self.admin = make_console_user(['wallet:recharge', 'report:view', 'report:audit', 'order:dispatch', 'order:view', 'order:refund'])
        self.accounts = {user.pk: get_or_create_account_for_legacy_user(user) for user in (self.boss, self.provider, self.admin)}
        membership = self.admin.admin_membership
        membership.account = self.accounts[self.admin.pk]
        membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        self.card = SupportContactCard.objects.create(name='负责客服', wecom_corp_id='wwtest', wecom_service_url='https://work.weixin.qq.com/kfid/kf-test')

    def login(self, user):
        token, _ = issue_account_tokens(self.accounts[user.pk])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def recharge(self, trade_no='receipt-1', request_id='recharge-1', account=None):
        self.login(self.admin)
        return self.client.post('/api/admin/wallets/recharge/', {
            'account_id': (account or self.accounts[self.boss.pk]).pk,
            'amount': 20000, 'gift_amount': 1000, 'trade_no': trade_no,
            'proof_image': screenshot(), 'client_request_id': request_id,
        }, format='multipart')

    def new_order(self):
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'order-1',
        }))
        return Order.objects.get(pk=data['id'])

    def finish(self, order):
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        return ProviderReport.objects.get(order=order, provider=self.provider)

    def settle_after_window(self):
        from datetime import timedelta
        from django.utils import timezone
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            for pk in Order.objects.filter(status='COMPLETED',payment_status='PAID').values_list('pk',flat=True):
                process_order(pk)

    def test_manual_receipt_to_order_to_report_to_wallet(self):
        order = self.new_order()
        report = self.finish(order)
        self.assertEqual(report.status, 'DRAFT')
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {'commission_rate': 0}))
        report.refresh_from_db()
        self.assertEqual(report.commission_rate, 20)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
        repeated = self.client.post(f'/api/admin/reports/{report.pk}/approve/')
        self.assertNotEqual(repeated.data['code'], 0)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)

    def test_receipt_and_request_replay_cannot_credit_again(self):
        self.ok(self.recharge())
        self.assertEqual(self.recharge(request_id='different-request').data['code'], 409)
        self.assertEqual(self.recharge(trade_no='different-receipt').data['code'], 409)
        self.assertEqual(self.recharge(account=self.accounts[self.provider.pk], request_id='other-boss').data['code'], 409)
        self.assertEqual(RechargeRecord.objects.count(), 1)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)

    def test_no_direct_topup_and_no_unverified_receipt(self):
        self.login(self.boss)
        self.assertNotEqual(self.client.post('/api/wallet/topup/', {'amount': 10000}).data['code'], 0)
        self.login(self.admin)
        self.assertNotEqual(self.client.post('/api/admin/wallets/recharge/', {'account_id': self.accounts[self.boss.pk].pk, 'amount': 10000}).data['code'], 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 0)

    def test_missing_result_evidence_cannot_submit_or_approve(self):
        report = self.finish(self.new_order())
        self.assertEqual(self.client.post(f'/api/wallet/reports/{report.pk}/submit/').data['code'], 400)
        ProviderReport.objects.filter(pk=report.pk).update(status='PENDING')
        self.login(self.admin)
        self.assertEqual(self.client.post(f'/api/admin/reports/{report.pk}/approve/').data['code'], 400)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)

    def test_order_customer_service_uses_assigned_card_and_own_identity(self):
        order = self.new_order()
        SupportContactCard.objects.create(name='默认客服', sort_order=0)
        data = self.ok(self.client.get('/api/support/contact-card/', {'order_id': order.pk}))
        self.assertEqual(data['id'], self.card.pk)
        self.assertEqual(data['customer_reference'], self.accounts[self.boss.pk].account_no)
        self.login(self.provider)
        self.assertEqual(self.client.get('/api/support/contact-card/', {'order_id': order.pk}).data['code'], 404)

    def test_rejected_report_can_be_corrected_and_resubmitted(self):
        report = self.finish(self.new_order())
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '结单截图不清晰'}))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'COMPLETION', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.settle_after_window()
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)

    def test_invalid_start_and_completion_images_do_not_advance_order(self):
        def fake_image():
            return SimpleUploadedFile('evidence.png', b'not an image', content_type='image/png')

        self.login(self.admin)
        response = self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 10000,
            'trade_no': 'invalid-proof', 'proof_image': fake_image(),
            'client_request_id': 'invalid-proof-request',
        }, format='multipart')
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(RechargeRecord.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 0)
        order = self.new_order()
        self.login(self.provider)
        response = self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': fake_image()}, format='multipart')
        self.assertEqual(response.data['code'], 400)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.GRABBED)
        self.assertFalse(ProviderReport.objects.filter(order=order).exists())
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order)
        response = self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': fake_image()}, format='multipart')
        self.assertEqual(response.data['code'], 400)
        self.assertFalse(report.result_images.exists())
        response = self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': fake_image()}, format='multipart')
        self.assertEqual(response.data['code'], 400)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.IN_SERVICE)
        self.assertFalse(ProviderReport.objects.get(order=order).completion_image)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)

    def prepare_transfer(self):
        second = make_provider()
        account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = account
        profile = second.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        return order, second

    def transfer(self, order, old, new):
        self.login(self.admin)
        return self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.accounts[old.pk].pk, 'new_provider_id': self.accounts[new.pk].pk,
            'split_type': 'PERCENT', 'split_value': 50, 'penalty': 0,
        }]}, format='json')

    def test_returning_provider_can_transfer_again_and_settles_accumulated_share_once(self):
        from orders.services import count_active_orders
        order, second = self.prepare_transfer()
        self.ok(self.transfer(order, self.provider, second))
        self.ok(self.transfer(order, second, self.provider))
        returned = OrderProvider.objects.get(order=order, provider=self.provider)
        self.assertIsNone(returned.released_at)
        self.assertEqual(count_active_orders(self.accounts[self.provider.pk]), 1)
        self.assertEqual(count_active_orders(self.accounts[second.pk]), 0)
        self.ok(self.transfer(order, self.provider, second))
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        for user in (self.provider, second):
            self.login(user)
            report = ProviderReport.objects.get(order=order, provider=user)
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            self.settle_after_window()
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 3000)
        self.assertEqual(Wallet.objects.get(user=second).balance, 5000)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)

    @override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
    def test_legacy_paid_transfer_cannot_reuse_an_already_settled_provider_row(self):
        order, second = self.prepare_transfer()
        self.ok(self.transfer(order, self.provider, second))
        response = self.transfer(order, second, self.provider)
        self.assertEqual(response.data['code'], 400)
        order.refresh_from_db()
        self.assertEqual(order.provider_id, second.pk)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 4000)
        self.assertEqual(Wallet.objects.get(user=second).balance, 0)
        self.assertIsNone(OrderProvider.objects.get(order=order, provider=second).released_at)

    def test_double_dispatch_settles_only_after_both_reports_pass(self):
        second = make_provider()
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        profile = second.escort_profile
        profile.account = second_account
        profile.save(update_fields=['account'])
        self.ok(self.recharge())
        dispatch_request = {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'double-dispatch-1',
            'escort_mode': 'DOUBLE', 'providers': [
                {'provider_id': self.accounts[self.provider.pk].pk, 'commission_rate': 20},
                {'provider_id': second_account.pk, 'commission_rate': 30},
            ],
        }
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', dispatch_request, format='json'))
        self.assertEqual(self.client.post('/api/admin/orders/dispatch/', dispatch_request, format='json').data['code'], 409)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get(pk=data['id'])
        self.finish(order)
        for user in (self.provider, second):
            self.login(user)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': kind, 'image': screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/reports/{report['id']}/approve/"))
            self.settle_after_window()
            if user.pk == self.provider.pk:
                self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
                self.assertEqual(Wallet.objects.get(user=second).balance, 0)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 4000)
        self.assertEqual(Wallet.objects.get(user=second).balance, 3500)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)

    def test_zero_base_double_member_can_report_and_does_not_block_settlement(self):
        second = make_provider()
        account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = account
        profile = second.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        self.service.price = 1
        self.service.save(update_fields=['price'])
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'small-double-order', 'escort_mode': 'DOUBLE',
            'providers': [
                {'provider_id': self.accounts[self.provider.pk].pk, 'commission_rate': 0},
                {'provider_id': account.pk, 'commission_rate': 0},
            ],
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        self.finish(order)
        self.assertEqual(list(order.providers.order_by('id').values_list('settlement_base', flat=True)), [1, 0])
        self.assertEqual(ProviderReport.objects.filter(order=order).count(), 2)
        for user in (self.provider, second):
            self.login(user)
            report = ProviderReport.objects.get(order=order, provider=user)
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {
                    'kind': kind, 'image': screenshot(),
                }, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            self.settle_after_window()
            if user == self.provider:
                order.refresh_from_db()
                self.assertIsNone(order.settled_at)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 1)
        self.assertEqual(Wallet.objects.get(user=second).balance, 0)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)

    def test_recharge_rollback_allows_same_request_after_database_failure(self):
        with patch('console.views.Transaction.objects.create', side_effect=RuntimeError('simulated ledger failure')):
            with self.assertRaises(RuntimeError):
                self.recharge()
        self.assertEqual(RechargeRecord.objects.count(), 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 0)
        self.ok(self.recharge())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)

    def test_report_result_images_can_only_be_removed_by_owner_before_review(self):
        report = self.finish(self.new_order())
        data = self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        image_id = data['result_images'][0]['id']
        path = f'/api/wallet/reports/{report.pk}/images/{image_id}/'
        self.login(self.boss)
        self.assertNotEqual(self.client.delete(path).data['code'], 0)
        self.assertEqual(report.result_images.count(), 1)
        self.login(self.provider)
        self.ok(self.client.delete(path))
        self.assertEqual(report.result_images.count(), 0)
        self.assertEqual(self.client.post(f'/api/wallet/reports/{report.pk}/submit/').data['code'], 400)
        data = self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
        self.assertEqual(self.client.delete(f"/api/wallet/reports/{report.pk}/images/{data['result_images'][0]['id']}/").data['code'], 400)
        self.assertEqual(report.result_images.count(), 1)

    def test_refund_after_service_before_audit_prevents_later_settlement(self):
        order = self.new_order()
        report = self.finish(order)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '客服确认终止服务'}))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.provider)
        self.assertEqual(self.client.post(f'/api/wallet/reports/{report.pk}/submit/').data['code'], 400)
        self.login(self.admin)
        self.assertNotEqual(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '重复退款'}).data['code'], 0)

    def test_transfer_defers_old_and_new_provider_income_until_audit(self):
        from orders.services import count_active_orders
        second = make_provider()
        account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = account
        profile = second.escort_profile
        profile.account = account
        profile.save(update_fields=['account'])
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': account.pk,
            'split_type': 'PERCENT', 'split_value': 50, 'penalty': 0,
        }]}, format='json'))
        self.assertEqual(count_active_orders(self.accounts[self.provider.pk]), 0)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.login(self.provider)
        stats = self.ok(self.client.get('/api/users/provider-stats/'))
        self.assertEqual(stats['active_orders'], 0)
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        for user in (self.provider, second):
            self.login(user)
            report = ProviderReport.objects.get(provider=user, order=order)
            for kind in ('ENTRY', 'COMPLETION', 'RESULT'):
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            self.settle_after_window()
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 4000)
        self.assertEqual(Wallet.objects.get(user=second).balance, 4000)
        self.assertEqual(sum(Transaction.objects.filter(order=order).values_list('amount', flat=True)), 0)
