"""Paid service income follows a claimed and released withdrawal to its receipt."""
from django.test import override_settings
from rest_framework.test import APITestCase, APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user
from orders.tests import test_release_flow as flow
from wallet.models import SystemConfig, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OrderIncomeWithdrawalJourneyTest(APITestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def test_service_income_claim_release_and_payout(self):
        self.run_journey(club_rules=False)

    def test_deposit_and_frozen_service_income_before_withdrawal(self):
        self.run_journey(club_rules=True)

    def run_journey(self, *, club_rules):
        self.service.price = 30000
        self.service.save(update_fields=['price'])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value': '2'})
        if club_rules:
            SystemConfig.objects.update_or_create(key='club_income_rules_enabled', defaults={'value': '1'})
            profile = self.provider.escort_profile
            profile.deposit_mode = 'EARNINGS'
            profile.save(update_fields=['deposit_mode'])
        self.login(self.admin)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 50000, 'gift_amount': 0,
            'trade_no': 'order-income-withdraw-receipt', 'proof_image': flow.screenshot(),
            'client_request_id': 'order-income-withdraw-recharge',
        }, format='multipart'))
        self.login(self.boss)
        created = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': self.accounts[self.provider.pk].pk,
            'support_contact_id': self.card.pk, 'client_request_id': 'order-income-withdraw-order',
        }, format='json'))
        order = Order.objects.get(pk=created['id'])
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'player_dashboard:view']
        role.save(update_fields=['permissions'])
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        if club_rules:
            from datetime import timedelta
            from wallet.income import release_due_income
            from wallet.models import IncomeHold
            profile = self.provider.escort_profile
            profile.refresh_from_db()
            hold = IncomeHold.objects.get(income_transaction__order=order, wallet__user=self.provider)
            self.assertEqual((profile.deposit_paid, hold.deposit_amount, hold.amount), (14400, 14400, 9600))
            wallet = Wallet.objects.get(user=self.provider)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (0, 9600))
            secondary = self.ok(self.client.get('/api/admin/dashboard/player'))['secondary']
            self.assertEqual((secondary['total_deposit'], secondary['total_wallet'], secondary['frozen_amount']),
                             (14400, 9600, 9600))
            self.assertEqual(release_due_income(now=hold.release_at + timedelta(seconds=1)), 1)
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 9600)
        else:
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 24000)

        self.login(self.provider)
        payee = self.ok(self.client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'ISOLATED-ACCOUNT', 'payee_name': '隔离测试', 'expected_id': 0, 'expected_version': 0,
        }, format='json'))
        amount = 6000 if club_rules else 20000
        actual = 5880 if club_rules else 19600
        requested = self.ok(self.client.post('/api/wallet/withdraw/', {'amount': amount,
            'withdrawal_account_id': payee['id'], 'expected_payee_version': payee['version'],
            'client_request_id': 'order-income-withdraw-request'}, format='json'))['request']
        request_id = requested['id']
        self.assertEqual((requested['status'], requested['processing'], requested['actual_amount']), ('PENDING', False, actual))
        remaining = 3600 if club_rules else 4000
        self.assertEqual((Wallet.objects.get(user=self.provider).balance, Wallet.objects.get(user=self.provider).frozen_amount), (remaining, amount))

        first_user = make_console_user(['withdraw:view', 'withdraw:audit'])
        manager_user = make_console_user(['withdraw:view', 'withdraw:release'])
        staff = []
        for user in (first_user, manager_user):
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            staff.append(client)
        first, manager = staff
        first_claim = self.ok(first.post(f'/api/admin/withdrawals/{request_id}/claim/'))
        self.assertTrue(next(row for row in self.ok(self.client.get('/api/wallet/withdraw/'))['requests'] if row['id'] == request_id)['processing'])
        self.ok(manager.post(f'/api/admin/withdrawals/{request_id}/release-claim/', {
            'confirmed_unpaid': True, 'reason': '已核实未打款',
            'expected_claimed_by_account': first_claim['claimed_by_account'], 'expected_claimed_at': first_claim['claimed_at'],
        }, format='json'))
        self.assertFalse(next(row for row in self.ok(self.client.get('/api/wallet/withdraw/'))['requests'] if row['id'] == request_id)['processing'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'withdraw:view', 'withdraw:audit', 'dashboard:view']
        role.save(update_fields=['permissions'])
        self.login(self.admin)
        claim = self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/claim/'))
        self.ok(self.client.post(f'/api/admin/withdrawals/{request_id}/approve/', {
            'payout_reference': 'ISOLATED-OFFLINE-PAYOUT', 'expected_claimed_at': claim['claimed_at'],
        }, format='json'))
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual(dashboard['pending_withdraw_total'], 0)
        if club_rules:
            secondary = self.ok(self.client.get('/api/admin/dashboard/player'))['secondary']
            self.assertEqual((secondary['total_deposit'], secondary['total_wallet'], secondary['frozen_amount'], secondary['settled_salary']),
                             (14400, remaining, 0, amount))
        self.login(self.provider)
        receipt = next(row for row in self.ok(self.client.get('/api/wallet/withdraw/'))['requests'] if row['id'] == request_id)
        self.assertEqual((receipt['status'], receipt['actual_amount'], receipt['payout_reference']),
                         ('APPROVED', actual, 'ISOLATED-OFFLINE-PAYOUT'))
        wallet = Wallet.objects.get(user=self.provider)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (remaining, 0))
