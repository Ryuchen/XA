from django.test import override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from wallet.models import IncomeHold, SystemConfig, Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION')
class NegotiatedTransferRefundTest(flow.ReleaseFlowTest):
    def prepare_transfer(self):
        incoming = make_provider()
        self.accounts[incoming.pk] = get_or_create_account_for_legacy_user(incoming)
        incoming.escort_profile.account = self.accounts[incoming.pk]
        incoming.escort_profile.save(update_fields=['account'])
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': self.accounts[incoming.pk].pk,
            'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}], 'reason': '隔离转单'}, format='json'))
        for user in (self.provider, incoming):
            self.login(user)
            reports = self.ok(self.client.get('/api/wallet/reports/'))
            report = next((row for row in reports if row['order_id'] == order.pk), None)
            if not report:
                report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json'))
            for kind in ('ENTRY',):
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                    {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        return order, incoming

    def test_negotiated_partial_and_idempotency(self):
        order, incoming = self.prepare_transfer()
        base = f'/api/admin/orders/{order.pk}/'
        options = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        amounts = {self.accounts[self.provider.pk].pk: 1000, self.accounts[incoming.pk].pk: 3000}
        allocation = [{'key': row['key'], 'amount': 1000 if row['role'] == 'PLATFORM' else amounts[row['account_id']]} for row in options]
        payload = {'refund_amount': 5000, 'allocations': allocation, 'reason': '履约协商', 'client_request_id': 'negotiated-1', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertTrue(preview['eligible'], preview)
        self.assertEqual(preview['recovery_total'], 1000)
        payload['plan_token'] = preview['plan_token']
        unreviewed = self.client.post(base + 'negotiated-refund/', dict(payload, reviewed=False), format='json')
        self.assertEqual(unreviewed.data['errorCode'], 'REFUND_REVIEW_REQUIRED')
        self.assertFalse(order.refund_records.exists())
        result = self.ok(self.client.post(base + 'negotiated-refund/', payload, format='json'))
        self.assertEqual((result['status'], result['payment_status'], result['refund_summary']['amount']), ('CANCELLED', 'PARTIALLY_REFUNDED', 5000))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 16000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 2200)
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 1800)
        order.refresh_from_db()
        refund = order.refund_records.get()
        self.assertEqual(refund.recovered_amount, 1000)
        recovery = refund.recovery_lines.get(source='INCOME')
        self.assertEqual((recovery.original_transaction.amount, recovery.reversal_transaction.amount), (3200, -1000))
        self.assertEqual(recovery.reversal_transaction.reversal_of_id, recovery.original_transaction_id)
        money = order.transactions.filter(status='SUCCESS', tx_type__in=['PAY', 'REFUND', 'INCOME', 'SHOP_INCOME'])
        self.assertEqual(sum(money.values_list('amount', flat=True)), 0)
        self.assertEqual(money.get(tx_type='SHOP_INCOME').amount, 1000)
        self.assertEqual(order.transactions.filter(tx_type='REFUND').count(), 1)
        self.ok(self.client.post(base + 'negotiated-refund/', payload, format='json'))
        self.assertEqual(order.transactions.filter(tx_type='REFUND').count(), 1)
        changed = dict(payload, allocations=[dict(item, amount=item['amount']+1) if item['key'] == 'platform' else item for item in allocation])
        self.assertEqual(self.client.post(base + 'negotiated-refund/', changed, format='json').data['errorCode'], 'REFUND_REQUEST_CONFLICT')
        for user in (self.boss, self.provider, incoming):
            self.login(user)
            detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual((detail['status'], detail['refund_summary']['amount']), ('CANCELLED', 5000))
            if user == self.boss:
                import json, subprocess
                from pathlib import Path
                from django.conf import settings
                from django.core.serializers.json import DjangoJSONEncoder
                repo = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(repo / 'scripts/native-order-refund-live.cjs')],
                    input=json.dumps({'order': detail, 'statusText': '已取消', 'refunded': 5000,
                                      'remaining': 5000, 'count': 1}, cls=DjangoJSONEncoder),
                    text=True, encoding='utf-8', capture_output=True, timeout=15, cwd=repo)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shortfall_and_stale_plan_are_atomic(self):
        order, incoming = self.prepare_transfer()
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        allocations = [{'key': row['key'], 'amount': 1000 if row['role'] == 'PLATFORM' or row['account_id'] == self.accounts[self.provider.pk].pk else 3000} for row in rows]
        payload = {'refund_amount': 5000, 'allocations': allocations, 'reason': '履约协商', 'client_request_id': 'negotiated-short', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value': '0'})
        self.login(self.provider)
        self.ok(self.client.post('/api/wallet/withdraw/', {'amount': 2700, 'payee_method': 'WECHAT',
            'payee_account': 'LOCAL_TEST_ONLY', 'payee_name': '隔离提现', 'client_request_id': 'negotiated-test-withdraw'}, format='json'))
        self.login(self.admin)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (500, 2700))
        before = list(order.transactions.values_list('pk', flat=True))
        result = self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json')
        self.assertEqual(result.data['errorCode'], 'REFUND_RECOVERY_INSUFFICIENT', result.data)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.refund_records.count()), ('IN_SERVICE', 'PAID', 0))
        self.assertEqual(list(order.transactions.values_list('pk', flat=True)), before)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertFalse(preview['eligible'])
        self.assertEqual(preview['shortfalls'][0]['shortfall'], 500)
        from wallet.models import WithdrawRequest
        role = self.admin.admin_membership.roles.first()
        role.permissions = list(set(role.permissions) | {'withdraw:audit'})
        role.save(update_fields=['permissions'])
        withdrawal = WithdrawRequest.objects.get(account=self.accounts[self.provider.pk], status='PENDING')
        claimed = self.ok(self.client.post(f'/api/admin/withdrawals/{withdrawal.pk}/claim/', {}, format='json'))
        self.ok(self.client.post(f'/api/admin/withdrawals/{withdrawal.pk}/reject/',
            {'audit_remark': '隔离测试退回', 'expected_claimed_at': claimed['claimed_at']}, format='json'))
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.login(self.provider)
        self.ok(self.client.post('/api/wallet/withdraw/', {'amount': 100, 'payee_method': 'WECHAT',
            'payee_account': 'LOCAL_TEST_ONLY', 'payee_name': '隔离提现', 'client_request_id': 'negotiated-stale-withdraw'}, format='json'))
        self.login(self.admin)
        result = self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json')
        self.assertEqual(result.data['errorCode'], 'REFUND_PLAN_CHANGED', result.data)
        self.assertFalse(order.refund_records.exists())

    def test_negotiated_termination_without_completion_images(self):
        order, incoming = self.prepare_transfer()
        order.provider_reports.filter(provider=incoming).update(completion_image='')
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        allocations = [{'key': row['key'], 'amount': 1000 if row['role'] == 'PLATFORM' or row['account_id'] == self.accounts[self.provider.pk].pk else 3000} for row in rows]
        payload = {'refund_amount': 5000, 'allocations': allocations, 'reason': '履约协商', 'client_request_id': 'negotiated-evidence', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertFalse(order.provider_reports.exclude(completion_image='').exists())
        self.assertTrue(preview['eligible'])
        self.ok(self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'PARTIALLY_REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 16000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 2200)
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 1800)

    def test_inviter_and_pending_income_follow_freeze_and_deposit_rules(self):
        self.boss.inviter = self.provider
        self.boss.inviter_commission_rate = 50
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter = self.accounts[self.provider.pk]
        boss_account.inviter_commission_rate = 50
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        order, incoming = self.prepare_transfer()
        # Model an existing order whose settlement snapshot uses the 72-hour rule.
        Order.objects.filter(pk=order.pk).update(income_hold_hours=72)
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value': '1'})
        profile = incoming.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.save(update_fields=['deposit_mode'])
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        burdens = {'INVITER': 500, 'PLATFORM': 500}
        allocation = [{'key': row['key'], 'amount': burdens.get(row['role'],
            1000 if row['account_id'] == self.accounts[self.provider.pk].pk else 3000)} for row in rows]
        self.assertEqual(sum(item['amount'] for item in allocation), 5000)
        payload = {'refund_amount': 5000, 'allocations': allocation, 'reason': '履约协商', 'client_request_id': 'negotiated-inviter', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertTrue(preview['eligible'], preview)
        result = self.ok(self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json'))
        self.assertEqual(len(result['refund_summary']['records'][0]['negotiated_allocation']), 4)
        profile.refresh_from_db()
        wallet = Wallet.objects.get(user=incoming)
        self.assertEqual((wallet.balance, wallet.frozen_amount, profile.deposit_paid), (0, 720, 1080))
        self.assertEqual(IncomeHold.objects.get(income_transaction__order=order, wallet=wallet).amount, 720)
        self.login(self.provider)
        summary = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['refund_summary']
        self.assertEqual((summary['net_income'], summary['referral_net_income']), (2200, 500))

    def test_existing_frozen_and_deposit_income_are_recovered_by_source(self):
        from django.db import transaction
        from wallet.income import hold_provider_income
        order, incoming = self.prepare_transfer()
        SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value': '1'})
        with transaction.atomic():
            profile = type(self.provider.escort_profile).objects.select_for_update().get(user=self.provider)
            profile.deposit_mode = 'EARNINGS'
            profile.save(update_fields=['deposit_mode'])
            original = order.transactions.get(tx_type='INCOME', wallet__user=self.provider)
            hold_provider_income(original, profile=profile, hold_hours=72)
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        allocation = [{'key': row['key'], 'amount': 500 if row['role'] == 'PLATFORM' else
                       3000 if row['account_id'] == self.accounts[self.provider.pk].pk else 1500} for row in rows]
        payload = {'refund_amount': 5000, 'allocations': allocation, 'reason': '协商冻结收益',
                   'client_request_id': 'negotiated-held', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        recovery = next(row for row in preview['recoveries'] if row['account_id'] == self.accounts[self.provider.pk].pk)
        self.assertEqual((recovery['held_amount'], recovery['deposit_amount'], recovery['available_required']), (1280, 1720, 0))
        self.ok(self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json'))
        profile.refresh_from_db()
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount, profile.deposit_paid), (0, 0, 200))
        self.assertEqual(Wallet.objects.get(user=incoming).balance, 3300)
        refund = order.refund_records.get()
        self.assertEqual(refund.recovery_lines.filter(source='HOLD').count(), 1)
        self.assertEqual(refund.recovery_lines.filter(source='DEPOSIT').count(), 2)
        money = order.transactions.filter(status='SUCCESS', tx_type__in=['PAY', 'REFUND', 'INCOME', 'SHOP_INCOME'])
        self.assertEqual(sum(money.values_list('amount', flat=True)), 0)

    def test_team_zero_retained_coupon_points_and_vip(self):
        from coupons.models import Coupon, UserCoupon
        from engagement.models import ConsumptionConfirmation, MemberProfile, PointLedger
        from orders.tests.factories import make_boss_type
        teammate = make_provider(display_name='留队陪玩')
        incoming = make_provider(display_name='转入陪玩')
        inviter = make_provider(display_name='独立推荐人')
        for user in (teammate, incoming, inviter):
            self.accounts[user.pk] = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = self.accounts[user.pk]
            user.escort_profile.save(update_fields=['account'])
        vip = make_boss_type(discount_rate=100)
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter, boss_account.inviter_commission_rate, boss_account.boss_type = self.accounts[inviter.pk], 50, vip
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate', 'boss_type'])
        self.boss.inviter, self.boss.inviter_commission_rate, self.boss.boss_type = inviter, 50, vip
        self.boss.save(update_fields=['inviter', 'inviter_commission_rate', 'boss_type'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        coupon = Coupon.objects.create(name='隔离协商券', discount_type='DIRECT', amount=1000)
        self.ok(self.recharge())
        self.login(self.boss)
        self.ok(self.client.post(f'/api/coupons/{coupon.pk}/claim/'))
        voucher = UserCoupon.objects.get(account=boss_account, coupon=coupon)
        created = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[teammate.pk].pk],
            'user_coupon_id': voucher.pk, 'client_request_id': 'negotiated-team-coupon'}, format='json'))
        order = Order.objects.get(pk=created['id'])
        self.assertEqual((order.amount, order.coupon_discount), (19000, 1000))
        for user in (self.provider, teammate):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.accounts[self.provider.pk].pk, 'new_provider_id': self.accounts[incoming.pk].pk,
            'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}], 'reason': '隔离双陪转单'}, format='json'))
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        self.assertEqual(len(rows), 5)
        self.assertEqual({row['role'] for row in rows}, {'PROVIDER', 'INVITER', 'PLATFORM'})
        amounts = {self.accounts[self.provider.pk].pk: 'all', self.accounts[incoming.pk].pk: 'all',
                   self.accounts[teammate.pk].pk: 'none'}
        allocations = [{'key': row['key'], 'amount': (row['entitlement'] if amounts.get(row['account_id']) == 'all' else
            0 if amounts.get(row['account_id']) == 'none' else row['entitlement'] // 2)} for row in rows]
        refund_amount = sum(item['amount'] for item in allocations)
        self.assertEqual(refund_amount, 9500)
        profile = MemberProfile.objects.get(account=boss_account)
        points_before = profile.points_balance
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(sum(order.point_allocations.values_list('amount', flat=True)), order.amount)
        payload = {'refund_amount': refund_amount, 'allocations': allocations, 'reason': '双陪协商',
                   'client_request_id': 'negotiated-team-refund', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertTrue(preview['eligible'], preview)
        self.ok(self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json'))
        order.refresh_from_db()
        voucher.refresh_from_db()
        profile.refresh_from_db()
        boss_account.refresh_from_db()
        self.assertEqual((order.status, voucher.status, profile.points_balance, boss_account.boss_type_id),
                         ('CANCELLED', 'USED', points_before, vip.pk))
        self.assertFalse(PointLedger.objects.filter(order=order).exists())
        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
        self.assertEqual(sum(order.point_allocations.values_list('refunded_amount', flat=True)), refund_amount)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11500)
        self.assertEqual(Wallet.objects.get(user=inviter).balance, 950)
        self.assertEqual(order.transactions.get(tx_type='SHOP_INCOME').amount, 950)
        for user in (self.provider, incoming):
            self.login(user)
            detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual(detail['refund_summary']['net_income'], 0)
            self.assertTrue(order.providers.get(provider=user).settled_at)
        self.login(teammate)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
        own = next(row for row in rows if row['account_id'] == self.accounts[teammate.pk].pk)
        self.assertEqual(detail['refund_summary']['net_income'], own['entitlement'])
        money = order.transactions.filter(status='SUCCESS', tx_type__in=['PAY', 'REFUND', 'INCOME', 'SHOP_INCOME'])
        self.assertEqual(sum(money.values_list('amount', flat=True)), 0)

    def test_full_refund_all_remainders_zero(self):
        order, incoming = self.prepare_transfer()
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        payload = {'refund_amount': order.amount, 'allocations': [
            {'key': row['key'], 'amount': row['entitlement']} for row in rows],
            'reason': '协商全额终止', 'client_request_id': 'negotiated-all-zero', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertTrue(preview['eligible'])
        result = self.ok(self.client.post(base + 'negotiated-refund/', dict(payload, plan_token=preview['plan_token']), format='json'))
        self.assertEqual((result['status'], result['payment_status']), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 21000)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.assertFalse(order.transactions.filter(wallet__user=incoming, tx_type='INCOME').exists())
        self.assertFalse(order.transactions.filter(tx_type='SHOP_INCOME').exists())
        self.assertEqual(sum(order.point_allocations.values_list('refunded_amount', flat=True)), order.amount)
        self.assertFalse(order.point_allocations.filter(released=False).exists())
        self.assertTrue(all(row['retained'] == 0 for row in order.refund_records.get().negotiated_allocation))
        money = order.transactions.filter(status='SUCCESS', tx_type__in=['PAY', 'REFUND', 'INCOME', 'SHOP_INCOME'])
        self.assertEqual(sum(money.values_list('amount', flat=True)), 0)

    def test_view_only_staff_cannot_access_negotiated_refund(self):
        from orders.tests.factories import make_console_user
        order, _ = self.prepare_transfer()
        viewer = make_console_user(['order:view'])
        self.accounts[viewer.pk] = get_or_create_account_for_legacy_user(viewer)
        viewer.admin_membership.account = self.accounts[viewer.pk]
        viewer.admin_membership.save(update_fields=['account'])
        self.login(viewer)
        base = f'/api/admin/orders/{order.pk}/'
        for method, path in ((self.client.get, 'negotiated-refund-options/'),
                             (self.client.post, 'negotiated-refund-preview/'),
                             (self.client.post, 'negotiated-refund/')):
            response = method(base + path, {}, format='json')
            self.assertIn(response.status_code, (403, 404), response.data)
        self.assertFalse(order.refund_records.exists())

    def test_same_total_changed_allocation_invalidates_signed_plan(self):
        order, incoming = self.prepare_transfer()
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        allocation = [{'key': row['key'], 'amount': 1000 if row['role'] == 'PLATFORM' or
                       row['account_id'] == self.accounts[self.provider.pk].pk else 3000} for row in rows]
        payload = {'refund_amount': 5000, 'allocations': allocation, 'reason': '协商分摊复核',
                   'client_request_id': 'negotiated-changed', 'reviewed': True}
        preview = self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        changed = [{'key': row['key'], 'amount': 1000 if row['role'] == 'PLATFORM' else
                    1100 if row['account_id'] == self.accounts[self.provider.pk].pk else 2900} for row in rows]
        before = list(order.transactions.values_list('pk', flat=True))
        result = self.client.post(base + 'negotiated-refund/',
            dict(payload, allocations=changed, plan_token=preview['plan_token']), format='json')
        self.assertEqual(result.data['errorCode'], 'REFUND_PLAN_CHANGED', result.data)
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(list(order.transactions.values_list('pk', flat=True)), before)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)


from django.test import LiveServerTestCase
from rest_framework.test import APIClient


@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False)
class NegotiatedRefundAdminBrowserTest(LiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    prepare_transfer = NegotiatedTransferRefundTest.prepare_transfer

    def test_admin_shortfall_shows_review_and_blocks_submit(self):
        import json, os, subprocess
        from pathlib import Path
        from django.conf import settings
        from club_accounts.tokens import issue_account_tokens
        root = os.environ.get('XA_ADMIN_NEGOTIATED_ROOT')
        if not root:
            self.skipTest('requires isolated admin build')
        order, _ = self.prepare_transfer()
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value': '0'})
        self.login(self.provider)
        self.ok(self.client.post('/api/wallet/withdraw/', {'amount': 2700, 'payee_method': 'WECHAT',
            'payee_account': 'LOCAL_TEST_ONLY', 'payee_name': '隔离提现', 'client_request_id': 'negotiated-ui-withdraw'}, format='json'))
        self.login(self.admin)
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (500, 2700))
        before = list(order.transactions.values_list('pk', flat=True))
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/admin-negotiated-refund-375.cjs')], input=json.dumps({
            'root': root, 'base': self.live_server_url, 'token': issue_account_tokens(self.accounts[self.admin.pk])[0],
            'orderNo': order.order_no, 'orderId': order.pk, 'shortfall': True}),
            text=True, encoding='utf-8', capture_output=True, timeout=35, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.refund_records.count()), ('IN_SERVICE', 'PAID', 0))
        self.assertEqual(list(order.transactions.values_list('pk', flat=True)), before)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 11000)

    def test_admin_page_submission_and_three_roles(self):
        import json, os, subprocess
        from pathlib import Path
        from django.conf import settings
        from club_accounts.tokens import issue_account_tokens
        root = os.environ.get('XA_ADMIN_NEGOTIATED_ROOT')
        if not root:
            self.skipTest('requires isolated admin build')
        existing_native_id = int(os.environ.get('XA_NEGOTIATED_NATIVE_EXISTING_ID', '0'))
        if existing_native_id:
            from orders.tests.factories import make_order
            if not 1 <= existing_native_id <= 20:
                self.fail('unexpected isolated native page order id')
            for _ in range(existing_native_id - 1):
                make_order(self.boss, self.service, status=Order.Status.CANCELLED, payment_status='UNPAID')
        order, incoming = self.prepare_transfer()
        if existing_native_id:
            self.assertEqual(order.pk, existing_native_id)
        repo = Path(settings.BASE_DIR).parent.parent
        log_path = repo / '.tools/negotiated-refund-browser.log'
        with log_path.open(mode='w+', encoding='utf-8') as log:
            result = subprocess.run(['node', str(repo / 'scripts/admin-negotiated-refund-375.cjs')], input=json.dumps({
                'root': root, 'base': self.live_server_url, 'token': issue_account_tokens(self.accounts[self.admin.pk])[0],
                'orderNo': order.order_no, 'orderId': order.pk,
                'providerRoot': os.environ.get('XA_PROVIDER_VUE_ROOT') or str(repo / '.tools/provider-refund-receipt-20261102'),
                'providers': [{'id': self.accounts[user.pk].pk, 'token': issue_account_tokens(self.accounts[user.pk])[0],
                               'recovered': recovered, 'net': net, 'snapshot': snapshot}
                              for user, recovered, net, snapshot in ((self.provider, 100, 220, 320), (incoming, 0, 180, 480))]}),
                text=True, encoding='utf-8', stdout=log, stderr=log, timeout=45, cwd=repo)
            log.seek(0)
            output = log.read()
        self.assertEqual(result.returncode, 0, output)
        for user in (self.boss, self.provider, incoming):
            self.login(user)
            detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual((detail['status'], detail['payment_status'], detail['refund_summary']['amount']),
                             ('CANCELLED', 'PARTIALLY_REFUNDED', 5000))
        if os.environ.get('XA_NEGOTIATED_NATIVE') == '1':
            result = subprocess.run(['node', str(repo / 'scripts/native-aftersale-receipt-iphone12.cjs')], input=json.dumps({
                'base': self.live_server_url, 'orderId': order.pk, 'bossId': self.accounts[self.boss.pk].pk,
                'token': issue_account_tokens(self.accounts[self.boss.pk])[0], 'refundAmount': 5000,
                'refundText': '500', 'remainingText': '500', 'negotiated': True, 'orderNo': order.order_no,
                'useExistingPage': bool(existing_native_id)}),
                text=True, encoding='utf-8', capture_output=True, timeout=25, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

