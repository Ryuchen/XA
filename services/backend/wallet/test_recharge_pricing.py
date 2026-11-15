"""Real store recharge quotes and guarded accounting with synthetic accounts."""
from django.test import override_settings
from unittest.mock import patch
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import RechargePackage
from orders.tests.factories import make_console_user, make_user
from wallet.models import RechargeGiftApproval, RechargeRecord, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class RechargePricingTest(APITestCase):
    def setUp(self):
        staff = make_console_user(['wallet:recharge', 'wallet:adjust', 'engagement:view', 'engagement:edit'])
        self.staff = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = self.staff
        staff.admin_membership.save(update_fields=['account'])
        self.boss = get_or_create_account_for_legacy_user(make_user())
        self.client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff)[0])
        manager = make_console_user(['recharge:view', 'recharge:gift_approve'])
        manager_account = get_or_create_account_for_legacy_user(manager)
        manager.admin_membership.account = manager_account
        manager.admin_membership.save(update_fields=['account'])
        self.manager = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(manager_account)[0])
        RechargePackage.objects.create(amount=300000, gift_amount=10000, active=True)
        self.high = RechargePackage.objects.create(amount=600000, gift_amount=25000, active=True)
        RechargePackage.objects.create(amount=100000, gift_amount=99999, active=False)

    def quote(self, amount):
        response = self.client.get('/api/admin/wallets/recharge-quote/', {'amount': amount})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_greedy_combinations_are_store_rules_not_best_promotion(self):
        for amount, bonus, remaining, counts in [(700000, 25000, 100000, [1]),
                (900000, 35000, 0, [1, 1]), (1200000, 50000, 0, [2]), (200000, 0, 200000, [])]:
            result = self.quote(amount)
            self.assertEqual((result['gift_amount'], result['remaining']), (bonus, remaining))
            self.assertEqual([row['count'] for row in result['matches']], counts)

    def test_real_recharge_preserves_snapshot_and_rejects_stale_bonus(self):
        quote = self.quote(900000)
        payload = {'account_id': self.boss.pk, 'amount': 900000, 'gift_amount': 35000,
                   'trade_no': 'synthetic-guarded-1', 'client_request_id': 'synthetic-guarded-1', 'bonus_version': quote['version']}
        update = self.client.patch(f'/api/admin/recharge-packages/{self.high.pk}/', {'gift_amount': 26000}, format='json')
        self.assertEqual(update.status_code, 200, update.data)
        stale = self.client.post('/api/admin/wallets/recharge/', {**payload, 'gift_amount': 35000}, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.assertEqual(stale.data['errorCode'], 'RECHARGE_BONUS_CHANGED')
        quote = self.quote(900000)
        result = self.client.post('/api/admin/wallets/recharge/', {**payload, 'gift_amount': 36000,
                                  'bonus_version': quote['version']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        record = RechargeRecord.objects.get()
        wallet = Wallet.objects.get(account=self.boss)
        self.assertEqual((record.amount, record.gift_amount, wallet.balance), (900000, 36000, 936000))
        self.assertEqual(record.bonus_snapshot, quote)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='TOPUP').count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='GIFT').count(), 1)
        repeat = self.client.post('/api/admin/wallets/recharge/', {**payload, 'gift_amount': 36000,
                                  'bonus_version': quote['version']}, format='json')
        self.assertNotEqual(repeat.data.get('code'), 0, repeat.data)
        self.assertEqual(RechargeRecord.objects.count(), 1)

    def test_custom_bonus_credits_principal_then_approval_credits_once(self):
        quote = self.quote(900000)
        result = self.client.post('/api/admin/wallets/recharge/', {'account_id': self.boss.pk, 'amount': 900000,
            'gift_amount': 40000, 'trade_no': 'synthetic-approval', 'client_request_id': 'synthetic-approval',
            'bonus_version': quote['version']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(result.data['data']['gift_approval_pending'])
        record = RechargeRecord.objects.get()
        approval = RechargeGiftApproval.objects.get(recharge=record)
        wallet = Wallet.objects.get(account=self.boss)
        self.assertEqual((wallet.balance, record.gift_amount, approval.standard_amount, approval.requested_amount),
                         (900000, 0, 35000, 40000))
        from engagement.services import wallet_funding_parts
        self.assertEqual(wallet_funding_parts(wallet), {'prepaid_balance': 900000, 'gift_balance': 0, 'compensation_balance': 0})
        adjustment = self.client.post(f'/api/admin/wallets/{wallet.pk}/adjust/', {'amount': -1000,
            'balance_kind': 'PREPAID', 'remark': '隔离核对本金', 'client_request_id': 'synthetic-principal-adjust'}, format='json')
        self.assertEqual(adjustment.status_code, 200, adjustment.data)
        endpoint = f'/api/admin/recharge-records/{record.pk}/gift-decision/'
        denied = self.client.post(endpoint, {'decision': 'APPROVED'}, format='json')
        self.assertEqual(denied.status_code, 403)
        decided = self.manager.post(endpoint, {'decision': 'APPROVED', 'reason': '核对同意'}, format='json')
        self.assertEqual(decided.status_code, 200, decided.data)
        wallet.refresh_from_db(); record.refresh_from_db(); approval.refresh_from_db()
        self.assertEqual((wallet.balance, record.gift_amount, approval.status), (939000, 40000, 'APPROVED'))
        self.assertEqual(wallet_funding_parts(wallet), {'prepaid_balance': 899000, 'gift_balance': 40000, 'compensation_balance': 0})
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='GIFT').count(), 1)
        replay = self.manager.post(endpoint, {'decision': 'APPROVED', 'reason': '核对同意'}, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(self.manager.post(endpoint, {'decision': 'REJECTED'}, format='json').status_code, 409)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance, 939000)

    def test_rejected_custom_bonus_credits_the_frozen_standard_amount(self):
        quote = self.quote(900000)
        result = self.client.post('/api/admin/wallets/recharge/', {'account_id': self.boss.pk, 'amount': 900000,
            'gift_amount': 1000, 'trade_no': 'synthetic-reject', 'client_request_id': 'synthetic-reject',
            'bonus_version': quote['version']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        record = RechargeRecord.objects.get()
        self.high.gift_amount = 1; self.high.save(update_fields=['gift_amount'])
        decided = self.manager.post(f'/api/admin/recharge-records/{record.pk}/gift-decision/',
                                    {'decision': 'REJECTED', 'reason': '恢复标准赠送'}, format='json')
        self.assertEqual(decided.status_code, 200, decided.data)
        record.refresh_from_db()
        self.assertEqual((record.gift_amount, record.gift_approval.status), (35000, 'REJECTED'))

    def test_approval_ledger_failure_rolls_back_status_and_wallet(self):
        quote = self.quote(900000)
        result = self.client.post('/api/admin/wallets/recharge/', {'account_id': self.boss.pk, 'amount': 900000,
            'gift_amount': 40000, 'trade_no': 'synthetic-rollback', 'client_request_id': 'synthetic-rollback',
            'bonus_version': quote['version']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        record = RechargeRecord.objects.get()
        with patch('engagement.services.record_wallet_adjustment', side_effect=ValueError('synthetic ledger failure')):
            with self.assertRaises(ValueError):
                self.manager.post(f'/api/admin/recharge-records/{record.pk}/gift-decision/',
                                  {'decision': 'APPROVED'}, format='json')
        record.refresh_from_db()
        self.assertEqual((record.gift_amount, record.gift_approval.status), (0, 'PENDING'))
        wallet = Wallet.objects.get(account=self.boss)
        self.assertEqual(wallet.balance, 900000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='GIFT').count(), 0)

    def test_invalid_active_tier_cannot_be_quoted_or_recharged(self):
        RechargePackage.objects.create(amount=0, gift_amount=100, active=True)
        self.assertEqual(self.client.get('/api/admin/wallets/recharge-quote/', {'amount': 100}).status_code, 400)
        result = self.client.post('/api/admin/wallets/recharge/', {'account_id': self.boss.pk, 'amount': 100,
            'trade_no': 'invalid-tier', 'client_request_id': 'invalid-tier'}, format='json')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertFalse(RechargeRecord.objects.exists())
