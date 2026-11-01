"""Real coupon lifecycle across buyer, staff and two providers."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon, UserCoupon
from engagement.confirmation import process_order
from orders.models import Order, OrderApplication
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class TeamCouponJourneyTest(APITransactionTestCase):
    def test_coupon_cancel_reuse_team_settlement_and_cumulative_refund(self):
        media = TemporaryDirectory(prefix='xa-team-coupon-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                                   pass_expires_at=timezone.now() + timedelta(days=7))
                     for name in ('优惠券双陪甲', '优惠券双陪乙')]
        staff = make_console_user(['dashboard:view', 'order:view', 'order:refund', 'report:view', 'report:audit'])
        accounts = {}
        for user in (boss, *providers, staff):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            accounts[user.pk] = account
        staff.admin_membership.account = accounts[staff.pk]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2, commission_rate=20)
        for user in providers:
            user.escort_profile.account = accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        wallet = make_wallet(boss, balance=50000)
        wallet.account = accounts[boss.pk]
        wallet.save(update_fields=['account'])
        coupon = Coupon.objects.create(name='隔离双陪优惠券', discount_type='DIRECT', amount=1000)

        def auth(user):
            self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[user.pk])[0])

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')

        auth(boss)
        ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=accounts[boss.pk], coupon=coupon)
        payload = {'service_id': service.pk, 'dispatch_route': 'PUBLIC', 'dispatch_mode': 'BOSS_SELECT',
                   'user_coupon_id': voucher.pk}
        first = ok(self.client.post('/api/orders/create/', dict(payload, client_request_id='team-coupon-cancel'), format='json'))
        self.assertEqual((first['original_amount'], first['coupon_discount'], first['amount']), (20000, 1000, 19000))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        ok(self.client.post(f"/api/orders/orders/{first['id']}/cancel/", {}, format='json'))
        voucher.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((voucher.status, wallet.balance), ('UNUSED', 50000))

        second = ok(self.client.post('/api/orders/create/', dict(payload, client_request_id='team-coupon-service'), format='json'))
        order = Order.objects.get(pk=second['id'])
        self.assertEqual((order.original_amount, order.coupon_discount, order.amount), (20000, 1000, 19000))
        Order.objects.filter(pk=order.pk).update(pool_entered_at=timezone.now() - timedelta(hours=1))
        auth(providers[0])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/grab/', {'team_size': 2,
                            'partner_id': accounts[providers[1].pk].pk}, format='json'))
        application = OrderApplication.objects.get(order=order, applicant=accounts[providers[0].pk])
        auth(providers[1])
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                            {'action': 'CONFIRM_TEAM'}, format='json'))
        auth(boss)
        ok(self.client.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json'))
        order.refresh_from_db()
        shares = {row.provider_account_id: row for row in order.providers.filter(released_at__isnull=True)}
        self.assertEqual(sum(row.settlement_base for row in shares.values()), 19000)
        self.assertEqual(sum(row.provider_income for row in shares.values()), 15200)
        for user in providers:
            auth(user)
            ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        for user in providers:
            auth(user)
            ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        for user in providers:
            auth(user)
            report = ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json'))
            self.assertEqual(report['amount'], shares[accounts[user.pk].pk].settlement_base)
            ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
        auth(staff)
        detail = ok(self.client.get(f'/api/admin/orders/{order.pk}/'))
        self.assertEqual((detail['amount'], detail['coupon_discount']), (19000, 1000))
        for report in ProviderReport.objects.filter(order=order):
            ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertTrue(process_order(order.pk))
        for user in providers:
            self.assertEqual(Wallet.objects.get(account=accounts[user.pk]).balance, shares[accounts[user.pk].pk].provider_income)
        preview = ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/', {'refund_amount': 2500}))
        ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'refund_amount': 2500,
            'reason': '隔离部分退款', 'plan_token': preview['plan_token'], 'client_request_id': 'team-coupon-partial'}, format='json'))
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'USED')
        preview = ok(self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/'))
        ok(self.client.post(f'/api/admin/orders/{order.pk}/refund/', {'reason': '隔离退还余款',
            'plan_token': preview['plan_token'], 'client_request_id': 'team-coupon-final'}, format='json'))
        voucher.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((voucher.status, wallet.balance), ('UNUSED', 50000))
        auth(boss)
        detail = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        self.assertEqual((detail['payment_status'], detail['refund_summary']['amount']), ('REFUNDED', 19000))
        self.assertEqual(detail['refund_summary']['records'][0]['coupon_results'][0]['outcome'], 'RETURNED')
        for user in providers:
            auth(user)
            detail = ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual((detail['payment_status'], detail['refund_summary']['net_income']), ('REFUNDED', 0))
            self.assertEqual(Wallet.objects.get(account=accounts[user.pk]).balance, 0)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='PAY').count(), 1)
