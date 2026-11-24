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
from engagement.models import PointFundingLot, PointLedger
from orders.grab_team_flow import resolve
from orders.models import (Order, OrderGrabPoolSnapshot, OrderGrabRound, OrderPointsRedemption,
                           OrderPointsRefund, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot)
from orders.tests import test_points_redemption_preview as quote_fixture
from orders.tests.factories import make_provider
from wallet.models import SystemConfig, Transaction


@override_settings(POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
                   BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, SERVICE_ORDER_POINTS_TIMEOUT_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   SERVICE_POINTS_ASSIGN_TESTING=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True,
                   GRAB_TEAM_WINDOW_ENABLED=True, GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True,
                   CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', ALLOWED_HOSTS=['*'])
class GrabTeamPointsFlowTest(APITransactionTestCase):
    setUp = quote_fixture.PointsServiceQuoteTest.setUp
    payload = quote_fixture.PointsServiceQuoteTest.payload

    def prepare(self, *, mixed_sources=False, dispatch_route='PUBLIC', team_size=2, selected=False,
                expected_status=200, expected_code=0, high_discount=False, before_payment=None, referral=False,
                host='sl.esports.club'):
        self.enterContext(patch('orders.grab_team_flow.enqueue'))
        self.wallet.balance = 25000
        self.wallet.save(update_fields=['balance'])
        self.service.billing_provider_count = team_size
        self.service.commission_policy = 'REGULAR'
        self.service.save(update_fields=['billing_provider_count', 'commission_policy'])
        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier, defaults={'value': '0'})
        if high_discount:
            from console.points_redemption_config import save_points_terms
            save_points_terms(group_point_hundredths=10000, group_coin_units=7000 if team_size == 2 else 3000,
                              max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        if referral:
            from orders.tests.factories import make_user
            self.referrer = get_or_create_account_for_legacy_user(make_user())
            self.account.inviter = self.referrer
            self.account.inviter_commission_rate = 10
            self.account.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.accounts, self.clients = [], []
        for index in range(2):
            user = make_provider(deposit_mode='EARNINGS', pass_tier='BLACK',
                                 pass_expires_at=timezone.now() + timedelta(hours=1))
            account = get_or_create_account_for_legacy_user(user)
            account.is_openid_bound, account.openid = True, f'points-team-bound-{index}'
            account.save(update_fields=['is_openid_bound', 'openid'])
            user.escort_profile.account = account
            user.escort_profile.wechat_avatar_verified_at = timezone.now()
            user.escort_profile.save(update_fields=['account', 'wechat_avatar_verified_at'])
            user.escort_profile.service_items.add(self.service)
            self.accounts.append(account)
            self.clients.append(APIClient(HTTP_HOST=host,
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]))
        payload = {**self.payload(), 'dispatch_mode': 'AUTO_GRAB', 'dispatch_route': dispatch_route}
        if selected:
            payload.update(dispatch_mode='BOSS_SELECT', provider_ids=[row.pk for row in self.accounts[:team_size]])
        if mixed_sources:
            for kind, amount in (('PREPAID', 24000), ('GIFT', 600), ('COMPENSATION', 400)):
                PointFundingLot.objects.create(account=self.account, source_key=f'team-points-{kind}',
                                               kind=kind, remaining=amount)
            payload.update(gift_balance_amount=600, compensation_balance_amount=400)
        quote = self.client.post('/api/orders/quote/', payload, format='json')
        self.assertEqual(quote.status_code, 200, quote.data)
        token = quote.data['data']['points_preview']['checkout_test_token']
        if before_payment:
            before_payment()
        data = {k: v for k, v in payload.items() if k != 'preview_points_hundredths'}
        response = self.client.post('/api/orders/create/', {**data, 'points_checkout_test_token': token}, format='json')
        self.assertEqual(response.status_code, expected_status, response.data)
        self.assertEqual(response.data['code'], expected_code if expected_status == 200 else expected_status, response.data)
        if expected_status != 200 or expected_code != 0:
            return response
        self.order = Order.objects.get(pk=response.data['data']['id'])
        self.receipt = OrderPointsRedemption.objects.get(order=self.order)
        time.sleep(0.01)

    def invite_and_confirm(self):
        invite = self.clients[0].post(f'/api/orders/orders/{self.order.pk}/grab-team/',
            {'partner_id': self.accounts[1].pk, 'client_request_id': 'points-team-invite'}, format='json')
        self.assertEqual(invite.status_code, 200, invite.data)
        team_id = invite.data['data']['team_id']
        confirm = self.clients[1].post(f'/api/orders/grab-team/invitations/{team_id}/confirm/',
            {'client_request_id': 'points-team-confirm'}, format='json')
        self.assertEqual(confirm.status_code, 200, confirm.data)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_team_assignment_captures_each_share_on_pre_points_deal(self):
        self.prepare(dispatch_route='STAFF')
        self.invite_and_confirm()
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'ASSIGNED')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'GRABBED')
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=self.order)
        self.assertEqual((coverage.source, coverage.status, coverage.deal_amount),
                         ('WALLET_POINTS', 'ACTIVE', self.receipt.deal_units))
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=self.order))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(sum(row.incoming_basis for row in snapshots), self.receipt.deal_units)
        self.assertGreater(sum(row.incoming_basis for row in snapshots), self.order.amount)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancel_returns_wallet_points_and_coupon_once(self):
        self.prepare(mixed_sources=True)
        self.invite_and_confirm()
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance, self.profile.points_balance),
                         ('CANCELLED', 25000, 15000))
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)
        self.assertEqual(dict(PointFundingLot.objects.filter(account=self.account).values_list('kind', 'remaining')),
                         {'PREPAID': 24000, 'GIFT': 600, 'COMPENSATION': 400})
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
        self.assertEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                          {'reason': '不再需要'}, format='json').status_code, 200)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_cancel_after_empty_window_returns_points_without_worker(self):
        self.prepare(mixed_sources=True)
        time.sleep(1.05)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance, self.profile.points_balance),
                         ('CANCELLED', 25000, 15000))
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
        self.assertEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                          {'reason': '不再需要'}, format='json').status_code, 200)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_single_points_late_first_grab_assigns_without_worker(self):
        self.enterContext(patch('orders.grab_single_flow.enqueue'))
        self.prepare(team_size=1)
        time.sleep(1.05)
        result = self.clients[0].post(f'/api/orders/orders/{self.order.pk}/grab/',
            {'client_request_id': 'single-points-late'}, format='json')
        self.assertEqual((result.status_code, result.data['data']['status']), (200, 'WON'), result.data)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.provider_account_id), ('GRABBED', self.accounts[0].pk))
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=self.order)
        self.assertEqual(snapshot.incoming_basis, self.receipt.deal_units)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_single_points_grab_through_report_and_settlement(self):
        self._single_points_report_and_settlement(selected=False)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_specified_single_points_through_report_and_settlement(self):
        self._single_points_report_and_settlement(selected=True)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_specified_points_waits_for_store_funding(self):
        self._single_points_report_and_settlement(selected=True, high_discount=True)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_specified_points_referral_uses_main_paid_amount(self):
        self._single_points_report_and_settlement(selected=True, referral=True)

    def _single_points_report_and_settlement(self, *, selected, high_discount=False, referral=False):
        import tempfile
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.context import resolve_store, store_context
        from wallet.models import ProviderReport

        self.enterContext(patch('orders.grab_single_flow.enqueue'))
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.prepare(team_size=1, selected=selected, high_discount=high_discount, referral=referral)
            player = self.clients[0]
            if selected:
                self.assertEqual((self.order.status, self.order.provider_account_id),
                                 ('GRABBED', self.accounts[0].pk))
            else:
                time.sleep(1.05)
                grab = player.post(f'/api/orders/orders/{self.order.pk}/grab/',
                                   {'client_request_id': 'single-points-fulfillment'}, format='json')
                self.assertEqual((grab.status_code, grab.data['data']['status']), (200, 'WON'), grab.data)
            for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                response = player.post(f'/api/orders/orders/{self.order.pk}/{action}/',
                                       {field: screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order=self.order)
            upload = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(upload.data['code'], 0, upload.data)
            submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            admin = make_console_user(['report:view', 'report:audit'])
            reviewer = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = reviewer
            admin.admin_membership.save(update_fields=['account'])
            review = APIClient(HTTP_HOST='admin.esports.club',
                               HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
            approved = review.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(approved.data['code'], 0, approved.data)
            report.refresh_from_db()
            snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=self.order)
            with store_context(resolve_store('shenglang')), patch(
                    'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                if high_discount:
                    self.assertGreater(snapshot.income_at_entry, self.receipt.paid_units)
                    self.assertFalse(process_order(self.order.pk))
                    self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=self.order).exists())
                    return
                self.assertTrue(process_order(self.order.pk))
                self.assertFalse(process_order(self.order.pk))
            income = Transaction.objects.get(order=self.order, tx_type='INCOME',
                                             wallet__account=self.accounts[0])
            self.assertEqual(income.amount, snapshot.income_at_entry)
            if referral:
                referral_income = Transaction.objects.get(order=self.order, tx_type='INCOME',
                                                           wallet__account=self.referrer)
                self.assertEqual(referral_income.amount, self.receipt.paid_units * 10 // 100)
            confirmation = ConsumptionConfirmation.objects.get(order=self.order)
            self.assertEqual(confirmation.amount, self.receipt.paid_units)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_specified_double_points_captures_deal_basis(self):
        self.prepare(team_size=2, selected=True)
        self.assertEqual(self.order.status, 'GRABBED')
        self.assertEqual(set(self.order.providers.values_list('provider_account_id', flat=True)),
                         {row.pk for row in self.accounts})
        self.assertFalse(OrderGrabPoolSnapshot.objects.filter(order=self.order).exists())
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=self.order))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(sum(row.incoming_basis for row in snapshots), self.receipt.deal_units)
        self.assertGreater(sum(row.incoming_basis for row in snapshots), self.receipt.paid_units)
        expected = {row.segment.provider_account_id: row.income_at_entry for row in snapshots}
        for account, player in zip(self.accounts, self.clients):
            detail = player.get(f'/api/orders/orders/{self.order.pk}/')
            self.assertEqual(detail.data['data']['expected_income'], expected[account.pk], detail.data)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_boss_specified_double_points_settles_each_provider(self):
        import tempfile
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.context import resolve_store, store_context
        from wallet.models import ProviderReport

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            self.prepare(team_size=2, selected=True)
            for player in self.clients:
                started = player.post(f'/api/orders/orders/{self.order.pk}/start/',
                                      {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
            for player in self.clients:
                completed = player.post(f'/api/orders/orders/{self.order.pk}/complete/',
                                        {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(completed.data['code'], 0, completed.data)
            admin = make_console_user(['report:view', 'report:audit'])
            reviewer = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = reviewer
            admin.admin_membership.save(update_fields=['account'])
            review = APIClient(HTTP_HOST='admin.esports.club',
                               HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
            reports = []
            for player, account in zip(self.clients, self.accounts):
                report = ProviderReport.objects.get(order=self.order, provider_account=account)
                uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(uploaded.data['code'], 0, uploaded.data)
                submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(submitted.data['code'], 0, submitted.data)
                approved = review.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(approved.data['code'], 0, approved.data)
                report.refresh_from_db()
                reports.append(report)
            with store_context(resolve_store('shenglang')), patch(
                    'django.utils.timezone.now', return_value=max(row.submitted_at for row in reports) + timedelta(hours=73)):
                self.assertTrue(process_order(self.order.pk))
                self.assertFalse(process_order(self.order.pk))
            snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=self.order)
            incomes = dict(Transaction.objects.filter(order=self.order, tx_type='INCOME').values_list(
                'wallet__account_id', 'amount'))
            self.assertEqual(incomes, {row.segment.provider_account_id: row.income_at_entry for row in snapshots})
            self.assertEqual(ConsumptionConfirmation.objects.get(order=self.order).amount, self.receipt.paid_units)

    @override_settings(SERVICE_POINTS_ASSIGN_TESTING=False)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_specified_points_rejected_without_assignment_integration(self):
        self.prepare(team_size=1, selected=True, expected_status=400)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        self.assertFalse(Order.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_specified_points_rechecks_provider_availability_before_payment(self):
        def take_provider_offline():
            profile = self.accounts[0].escort_profile
            profile.status = 'OFFLINE'
            profile.save(update_fields=['status'])
        self.prepare(team_size=1, selected=True, expected_code=400, before_payment=take_provider_offline)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        self.assertFalse(Order.objects.exists())

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_specified_provider_cannot_also_enter_auto_grab(self):
        self.prepare(team_size=1, selected=True)
        before = (Order.objects.count(), Transaction.objects.count())
        response = self.client.post('/api/orders/quote/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[0].pk], 'dispatch_mode': 'AUTO_GRAB'}, format='json')
        self.assertEqual(response.data['code'], 400, response.data)
        self.assertIn('不能同时选择', str(response.data['msg']))
        self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)

    @override_settings(PROVIDER_WECHAT_GATE_ENABLED=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_specified_points_rechecks_wechat_binding_before_payment(self):
        def unbind_provider():
            account = self.accounts[0]
            account.is_openid_bound = False
            account.save(update_fields=['is_openid_bound'])
        self.prepare(team_size=1, selected=True, expected_code=400, before_payment=unbind_provider)
        self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.profile.points_balance), (25000, 15000))
        self.assertFalse(Order.objects.exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True,
                       SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_specified_points_prestart_reassignment_preserves_funding_facts(self):
        import tempfile
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.points_funding_plan import plan_verified_order
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.context import resolve_store, store_context
        from stores.db import transaction
        from wallet.models import ProviderReport
        self.prepare(team_size=1, selected=True)
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        result = admin.post(f'/api/admin/orders/{self.order.pk}/transfer/', {'reason': '客服协调更换',
            'transfers': [{'old_provider_id': self.accounts[0].pk, 'new_provider_id': self.accounts[1].pk}]}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.provider_account_id, self.accounts[1].pk)
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order=self.order)
        self.assertEqual(coverage.status, 'ACTIVE')
        with store_context(resolve_store('shenglang')), transaction.atomic():
            plan = plan_verified_order(self.order.pk)
            self.assertEqual(plan.provider_income_units, 4800)
        old, new = list(OrderSegmentFinancialSnapshot.objects.filter(
            segment__attribution__order=self.order).order_by('segment_id'))
        self.assertEqual((old.retained_basis, new.incoming_basis, new.income_at_entry), (0, 6000, 4800))
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            player = self.clients[1]
            for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                response = player.post(f'/api/orders/orders/{self.order.pk}/{action}/',
                                       {field: screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order=self.order, provider_account=self.accounts[1])
            response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                   {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            response = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            report.refresh_from_db()
            with store_context(resolve_store('shenglang')), patch(
                    'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(self.order.pk))
                self.assertFalse(process_order(self.order.pk))
            self.assertEqual(Transaction.objects.get(order=self.order, tx_type='INCOME',
                wallet__account=self.accounts[1]).amount, 4800)
            self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME',
                                                        wallet__account=self.accounts[0]).exists())
            self.assertEqual(ConsumptionConfirmation.objects.get(order=self.order).amount, self.receipt.paid_units)

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True,
                       SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_points_prestart_reassignment_uses_new_rates_and_seat_limit(self):
        import tempfile
        from engagement.confirmation import process_order
        from orders.points_funding_plan import plan_verified_order
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.context import resolve_store, store_context
        from stores.db import transaction
        from wallet.models import ProviderReport
        self.prepare(team_size=1, selected=True)
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        actor = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = actor
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
        url = f'/api/admin/orders/{self.order.pk}/transfer/'
        def move(old, new):
            return admin.post(url, {'reason': '协商更换', 'transfers': [{
                'old_provider_id': self.accounts[old].pk, 'new_provider_id': self.accounts[new].pk}]}, format='json')
        with override_settings(SERVICE_POINTS_PRESTART_TESTING=False):
            self.assertNotEqual(move(0, 1).data['code'], 0)
        self.service.commission_override = True
        self.service.commission_rate = 30
        self.service.save(update_fields=['commission_override', 'commission_rate'])
        first = move(0, 1)
        self.assertEqual(first.data['code'], 0, first.data)
        with store_context(resolve_store('shenglang')), transaction.atomic():
            self.assertEqual(plan_verified_order(self.order.pk).provider_income_units, 4200)
        self.service.commission_rate = 25
        self.service.save(update_fields=['commission_rate'])
        second = move(1, 0)
        self.assertEqual(second.data['code'], 0, second.data)
        with store_context(resolve_store('shenglang')), transaction.atomic():
            self.assertEqual(plan_verified_order(self.order.pk).provider_income_units, 4500)
        self.assertNotEqual(move(0, 1).data['code'], 0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.provider_account_id, self.accounts[0].pk)
        self.assertEqual(self.order.prestart_handoffs.count(), 2)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            player = self.clients[0]
            for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                response = player.post(f'/api/orders/orders/{self.order.pk}/{action}/',
                                       {field: screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order=self.order, provider_account=self.accounts[0])
            response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                   {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            response = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            report.refresh_from_db()
            with store_context(resolve_store('shenglang')), patch(
                    'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(self.order.pk))
            income = Transaction.objects.filter(order=self.order, tx_type='INCOME')
            self.assertEqual(list(income.values_list('wallet__account_id', 'amount')), [(self.accounts[0].pk, 4500)])

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True,
                       SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_points_prestart_double_replacements_keep_each_fixed_rule(self):
        import tempfile
        from engagement.confirmation import process_order
        from orders.points_funding_plan import plan_verified_order
        from orders.tests.factories import make_console_user
        from orders.tests.test_release_flow import screenshot
        from stores.context import resolve_store, store_context
        from stores.db import transaction
        from wallet.models import ProviderReport
        self.prepare(team_size=2, selected=True)
        replacements = []
        for index in range(2):
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(self.service)
            replacements.append(account)
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        actor = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = actor
        staff.admin_membership.save(update_fields=['account'])
        admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
        self.service.commission_override = True
        self.service.commission_type = 'FIXED'
        self.service.commission_fixed = 2001
        self.service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        def move(old, new):
            return admin.post(f'/api/admin/orders/{self.order.pk}/transfer/', {'reason': '协调换人',
                'transfers': [{'old_provider_id': old.pk, 'new_provider_id': new.pk}]}, format='json')
        second_seat = move(self.accounts[1], replacements[1])
        self.assertEqual(second_seat.data['code'], 0, second_seat.data)
        with store_context(resolve_store('shenglang')), transaction.atomic():
            self.assertEqual(plan_verified_order(self.order.pk).provider_income_units, 12500)
        self.service.commission_fixed = 3001
        self.service.save(update_fields=['commission_fixed'])
        first_seat = move(self.accounts[0], replacements[0])
        self.assertEqual(first_seat.data['code'], 0, first_seat.data)
        with store_context(resolve_store('shenglang')), transaction.atomic():
            self.assertEqual(plan_verified_order(self.order.pk).provider_income_units, 12499)
        snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=self.order,
                                                                  segment__released_at__isnull=True)
        self.assertEqual(set(snapshots.values_list('fixed_rule_total', flat=True)), {2001, 3001})
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            clients = [APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(row)[0])
                       for row in replacements]
            for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                for player in clients:
                    response = player.post(f'/api/orders/orders/{self.order.pk}/{action}/',
                                           {field: screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
            reports = []
            for player, account in zip(clients, replacements):
                report = ProviderReport.objects.get(order=self.order, provider_account=account)
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                report.refresh_from_db()
                reports.append(report)
            with store_context(resolve_store('shenglang')), patch(
                    'django.utils.timezone.now', return_value=max(row.submitted_at for row in reports) + timedelta(hours=73)):
                self.assertTrue(process_order(self.order.pk))
            paid = dict(Transaction.objects.filter(order=self.order, tx_type='INCOME').values_list('wallet__account_id', 'amount'))
            self.assertEqual(paid, {row.segment.provider_account_id: row.income_at_entry for row in snapshots})

    @override_settings(GRAB_SINGLE_WINDOW_ENABLED=True)
    def test_single_points_cancel_after_empty_window_returns_assets(self):
        self.enterContext(patch('orders.grab_single_flow.enqueue'))
        self.prepare(mixed_sources=True, team_size=1)
        time.sleep(1.05)
        result = self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                  {'reason': '不再需要'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance, self.profile.points_balance),
                         ('CANCELLED', 25000, 15000))
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
        self.assertEqual(self.client.post(f'/api/orders/orders/{self.order.pk}/cancel/',
                                          {'reason': '不再需要'}, format='json').status_code, 200)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '抢单池窗口决议/团队草稿/积分抢单/退款事实/分组恢复属正式契约深水区（18-盘点 抢单池/积分/陪玩生命周期待联调）；最终联调启用')
    def test_unmatched_timeout_returns_wallet_points_and_coupon_once(self):
        self.prepare()
        pool = OrderGrabPoolSnapshot.objects.get(order=self.order)
        OrderGrabPoolSnapshot.objects.filter(pk=pool.pk).update(
            original_total_deadline=pool.first_deadline, total_deadline=pool.first_deadline)
        OrderGrabRound.objects.filter(pool=pool, number=1).update(total_deadline=pool.first_deadline)
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=pool.first_deadline)
        time.sleep(1.05)
        self.assertEqual(resolve(self.order.pk), 'CANCELLED')
        self.assertEqual(resolve(self.order.pk), 'CANCELLED')
        self.order.refresh_from_db(); self.wallet.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.order.status, self.wallet.balance, self.profile.points_balance),
                         ('CANCELLED', 25000, 15000))
        self.assertEqual(UserCoupon.objects.get().status, UserCoupon.Status.UNUSED)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=self.order).count(), 1)
