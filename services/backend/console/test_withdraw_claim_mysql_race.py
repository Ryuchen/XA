"""Two reviewers contest one real withdrawal on an isolated row-locking database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig, Transaction, WithdrawRequest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class WithdrawClaimMySQLRaceTest(APITransactionTestCase):
    def test_two_staff_claim_then_race_to_audit_only_claimant_can_succeed(self):
        provider = make_provider()
        owner = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = owner
        provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(user=provider, account=owner)
        wallet.balance = 60000
        wallet.save(update_fields=['balance'])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value': '2'})
        staff = []
        for _ in range(2):
            user = make_console_user(['withdraw:view', 'withdraw:audit'])
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            staff.append(account)

        def client_for(account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            return client

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        client = client_for(owner)
        payee = ok(client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-account-1234', 'payee_name': '隔离测试',
            'expected_id': 0, 'expected_version': 0,
        }, format='json'))
        created = ok(client.post('/api/wallet/withdraw/', {
            'amount': 20000, 'withdrawal_account_id': payee['id'],
            'expected_payee_version': payee['version'], 'client_request_id': 'claim-and-audit-race',
        }, format='json'))
        withdrawal_id = created['request']['id']
        url = f'/api/admin/withdrawals/{withdrawal_id}/'

        def race(action, payload):
            barrier = Barrier(2)
            def worker(account):
                close_old_connections()
                try:
                    actor = client_for(account)
                    barrier.wait(timeout=10)
                    response = actor.post(url + action + '/', payload, format='json')
                    return account.pk, response.status_code, response.data['code']
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(worker, staff))

        claims = race('claim', {})
        self.assertEqual(sorted(status for _, status, _ in claims), [200, 409], claims)
        winner = next(pk for pk, status, _ in claims if status == 200)
        loser = next(pk for pk, status, _ in claims if status == 409)
        withdrawal = WithdrawRequest.objects.get(pk=withdrawal_id)
        self.assertEqual(withdrawal.claimed_by_account_id, winner)
        self.assertEqual(withdrawal.status, WithdrawRequest.Status.PENDING)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount), (40000, 20000))

        winner_account = next(account for account in staff if account.pk == winner)
        current_claim = ok(client_for(winner_account).get(url))
        reviews = race('approve', {'payout_reference': 'ISOLATED-PAYOUT-REFERENCE',
                                   'expected_claimed_at': current_claim['claimed_at']})
        self.assertEqual(next(status for pk, status, _ in reviews if pk == winner), 200, reviews)
        loser_response = next((status, code) for pk, status, code in reviews if pk == loser)
        # Some established API failures use HTTP 200 with a non-zero envelope.
        self.assertIn(loser_response[0], (200, 400, 409), reviews)
        self.assertIn(loser_response[1], (400, 409), reviews)
        withdrawal.refresh_from_db()
        self.assertEqual(withdrawal.status, WithdrawRequest.Status.APPROVED)
        self.assertEqual(withdrawal.auditor_account_id, winner)
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, wallet.frozen_amount), (40000, 0))
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='WITHDRAW', status='SUCCESS').count(), 1)
        self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=400).count(), 1)
