"""A real database arbitrates release and approval of one withdrawal."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import skipUnlessDBFeature

from club_accounts.services import get_or_create_account_for_legacy_user
from console.models import AdminAuditLog
from console.test_withdraw_claim_workflow import WithdrawClaimWorkflowTest
from orders.tests.factories import make_console_user
from wallet.models import WithdrawRequest


@skipUnlessDBFeature('has_select_for_update')
class WithdrawReleaseRaceTest(WithdrawClaimWorkflowTest):
    def test_release_and_approval_of_same_claim_have_one_winner(self):
        claimant = self.client_for(self.staff[0])
        claimed = self.ok(claimant.post(self.url + 'claim/'))
        manager_user = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager_user)
        manager_user.admin_membership.account = manager_account
        manager_user.admin_membership.save(update_fields=['account'])
        payload = {'confirmed_unpaid': True, 'reason': '隔离核实未打款',
                   'expected_claimed_by_account': claimed['claimed_by_account'],
                   'expected_claimed_at': claimed['claimed_at']}
        barrier = Barrier(2)

        def worker(account, action, data):
            close_old_connections()
            try:
                client = self.client_for(account)
                barrier.wait(timeout=10)
                response = client.post(self.url + action + '/', data, format='json')
                return action, response.status_code, response.data['code']
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            release = pool.submit(worker, manager_account, 'release-claim', payload)
            approve = pool.submit(worker, self.staff[0], 'approve', {'payout_reference': 'ISOLATED-REFERENCE',
                                                                      'expected_claimed_at': claimed['claimed_at']})
            results = {result[0]: result[1:] for result in (release.result(), approve.result())}
        self.assertEqual(sum(code == 0 for _, code in results.values()), 1, results)
        row = WithdrawRequest.objects.get(pk=self.withdraw_id)
        self.wallet.refresh_from_db()
        if results['release-claim'][1] == 0:
            self.assertEqual(row.status, 'PENDING')
            self.assertIsNone(row.claimed_by_account_id)
            self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
            self.assertEqual(AdminAuditLog.objects.filter(path=self.url + 'release-claim/').count(), 1)
        else:
            self.assertEqual(row.status, 'APPROVED')
            self.assertEqual(row.claimed_by_account_id, self.staff[0].pk)
            self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 0))
            self.assertFalse(AdminAuditLog.objects.filter(path=self.url + 'release-claim/').exists())
