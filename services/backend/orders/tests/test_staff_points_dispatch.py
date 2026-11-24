import os
from unittest import skipUnless
import time
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.capacity_config import save_capacity
from console.grab_config import save_grab_window
from coupons.models import UserCoupon
from engagement.models import PointFundingLot
from orders.models import Order, OrderPointsRedemption, OrderPointsRefund, OrderStatusLog
from orders.tests import test_points_redemption_preview as quote_fixture
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig


@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_ORDER_POINTS_CANCEL_TESTING=True, SERVICE_ORDER_POINTS_TIMEOUT_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
    GRAB_TEAM_WINDOW_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, CELERY_TASK_ALWAYS_EAGER=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    ALLOWED_HOSTS=['*'])
class StaffPointsDispatchTest(APITransactionTestCase):
    setUp = quote_fixture.PointsServiceQuoteTest.setUp

    def prepare(self, team=False):
        self.enterContext(patch('orders.grab_team_flow.enqueue'))
        self.enterContext(patch('orders.grab_single_flow.enqueue'))
        self.wallet.balance = 25000
        self.wallet.save(update_fields=['balance'])
        for kind, amount in (('PREPAID', 24000), ('GIFT', 600), ('COMPENSATION', 400)):
            PointFundingLot.objects.create(account=self.account, source_key='staff-points-'+kind,
                                           kind=kind, remaining=amount)
        self.service.billing_provider_count = 2 if team else 1
        self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['billing_provider_count', 'commission_policy'])
        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier, defaults={'value': '0'})
        staff = make_console_user(['order:view', 'order:dispatch'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff
        staff.admin_membership.save(update_fields=['account'])
        self.admin = APIClient(HTTP_HOST='admin.esports.club',
            HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.staff)[0])
        for name in ('_schedule_auto_cancel', 'notify_order_update', '_push_message_safe'):
            self.enterContext(patch('console.views.'+name))
        self.payload = {'customer_id': self.account.pk, 'service_id': self.service.pk,
                        'escort_mode': 'DOUBLE' if team else 'SINGLE',
                        'gift_balance_amount': 600, 'compensation_balance_amount': 400,
                        'boss_confirmation_method': '在线咨询',
                        'boss_confirmation': '老板明确同意服务、积分及赠送赔付余额',
                        'client_request_id': 'staff-points-checkout'}

    def create(self):
        quote = self.admin.get('/api/admin/orders/dispatch-quote/', {
            **self.payload, 'preview_points_hundredths': 15000})
        self.assertEqual(quote.data['code'], 0, quote.data)
        data = quote.data['data']
        result = self.admin.post('/api/admin/orders/dispatch/', {**self.payload,
            'discount_selection_token': data['discount_selection_token'],
            'points_checkout_test_token': data['points_checkout']['checkout_test_token'],
            'expected_amount': data['points_checkout']['payable_units']}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['id'])
        self.assertEqual(order.dispatch_route, 'STAFF')
        self.assertEqual(order.funding_confirmation['method'], '在线咨询')
        self.assertEqual(order.funding_confirmation['points_hundredths'], 10000)
        self.assertTrue(order.status_logs.filter(action=OrderStatusLog.Action.CREATE,
                                                 operator_account=self.staff).exists())
        self.assertTrue(OrderPointsRedemption.objects.filter(order=order).exists())
        return order

    def provider(self, index):
        user = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                             pass_expires_at=timezone.now()+timedelta(hours=1))
        account = get_or_create_account_for_legacy_user(user)
        account.is_openid_bound, account.openid = True, 'staff-points-bound-'+str(index)
        account.save(update_fields=['is_openid_bound', 'openid'])
        user.escort_profile.account = account
        user.escort_profile.wechat_avatar_verified_at = timezone.now()
        user.escort_profile.save(update_fields=['account', 'wechat_avatar_verified_at'])
        user.escort_profile.service_items.add(self.service)
        return account, APIClient(HTTP_HOST='sl.esports.club',
            HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])

    def test_staff_single_points_order_cancels_original_funding(self):
        self.prepare()
        order = self.create()
        time.sleep(.01)
        cancelled = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '不需要'}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((order.status, self.wallet.balance, self.profile.points_balance), ('CANCELLED', 25000, 15000))
        self.assertEqual(dict(PointFundingLot.objects.filter(account=self.account).values_list('kind','remaining')),
                         {'PREPAID': 24000, 'GIFT': 600, 'COMPENSATION': 400})
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)
        self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '不需要'}, format='json')
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '客服积分派单改由抢单池自动分发（原指定派单拒绝口径演变）属正式契约深水区（18-盘点 派单/积分待联调）；最终联调启用')
    def test_staff_double_points_order_real_team_allocation(self):
        self.prepare(team=True)
        a, ca = self.provider(1)
        b, cb = self.provider(2)
        order = self.create()
        bypass = self.admin.post(f'/api/admin/orders/{order.pk}/assign/',
                                 {'provider_ids': [a.pk, b.pk]}, format='json')
        self.assertEqual(bypass.data['code'], 400, bypass.data)
        time.sleep(.01)
        invite = ca.post(f'/api/orders/orders/{order.pk}/grab-team/',
                         {'partner_id': b.pk, 'client_request_id': 'staff-team-invite'}, format='json')
        self.assertEqual(invite.status_code, 200, invite.data)
        confirm = cb.post(f'/api/orders/grab-team/invitations/{invite.data["data"]["team_id"]}/confirm/',
                          {'client_request_id': 'staff-team-confirm'}, format='json')
        self.assertEqual(confirm.status_code, 200, confirm.data)
        time.sleep(1.05)
        from orders.grab_team_flow import resolve
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual(set(order.providers.values_list('provider_account_id', flat=True)), {a.pk, b.pk})

    def test_staff_single_points_unmatched_timeout_refunds_once(self):
        self.prepare()
        order = self.create()
        from orders.models import OrderGrabPoolSnapshot, OrderGrabRound
        pool = OrderGrabPoolSnapshot.objects.get(order=order)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
            original_total_deadline=pool.first_deadline, total_deadline=pool.first_deadline)
        OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
        Order.objects.filter(pk=order.pk).update(auto_cancel_at=pool.first_deadline)
        time.sleep(1.05)
        from orders.grab_single_flow import resolve
        self.assertEqual(resolve(order.pk), 'CANCELLED')
        self.assertEqual(resolve(order.pk), 'CANCELLED')
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '客服积分派单改由抢单池自动分发（原指定派单拒绝口径演变）属正式契约深水区（18-盘点 派单/积分待联调）；最终联调启用')
    def test_staff_single_points_order_real_grab_allocation(self):
        self.prepare()
        account, player = self.provider(3)
        order = self.create()
        bypass = self.admin.post(f'/api/admin/orders/{order.pk}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(bypass.data['code'], 400, bypass.data)
        time.sleep(.01)
        grabbed = player.post(f'/api/orders/orders/{order.pk}/grab/',
                              {'client_request_id': 'staff-single-grab'}, format='json')
        self.assertEqual(grabbed.status_code, 200, grabbed.data)
        time.sleep(1.05)
        from orders.grab_single_flow import resolve
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, account.pk)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '客服积分派单改由抢单池自动分发（原指定派单拒绝口径演变）属正式契约深水区（18-盘点 派单/积分待联调）；最终联调启用')
    def test_staff_double_points_cancel_restores_all_sources(self):
        self.prepare(team=True)
        order = self.create()
        time.sleep(.01)
        result = self.client.post(f'/api/orders/orders/{order.pk}/cancel/', {'reason': '不需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        self.assertEqual(dict(PointFundingLot.objects.filter(account=self.account).values_list('kind','remaining')),
                         {'PREPAID': 24000, 'GIFT': 600, 'COMPENSATION': 400})
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)

    def test_rejects_direct_staff_assignment(self):
        self.prepare()
        result = self.admin.post('/api/admin/orders/dispatch/', {**self.payload, 'provider_id': 1}, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertFalse(Order.objects.exists())
