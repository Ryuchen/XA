"""The dashboard queue and withdrawal review read the same real requests."""
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_console_user
from wallet.test_withdraw_feedback_real import WithdrawalFeedbackRealTest as fixtures


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DashboardWithdrawalWorkflowTest(APITestCase):
    setUp = fixtures.setUp
    client_for = staticmethod(fixtures.client_for)
    ok = fixtures.ok
    bind = fixtures.bind
    payload = fixtures.payload

    def test_claim_scopes_follow_real_claim_release_and_review(self):
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view', 'withdraw:release']
        role.save(update_fields=['permissions'])
        second_user = make_console_user(['dashboard:view', 'withdraw:view', 'withdraw:audit'])
        second_account = get_or_create_account_for_legacy_user(second_user)
        second_user.admin_membership.account = second_account
        second_user.admin_membership.save(update_fields=['account'])
        second = self.client_for(second_account)
        payee = self.bind('isolated-claim-1234')
        ids = [self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, f'dashboard-claim-{index}'), format='json'))['data']['request']['id']
            for index in range(2)]

        def check(client, total, unclaimed, mine, unclaimed_ids, mine_ids):
            data = self.ok(client.get('/api/admin/dashboard/stats'))['data']
            self.assertEqual((data['pending_withdraw_total'], data['pending_withdraw_unclaimed'], data['pending_withdraw_mine']),
                             (total, unclaimed, mine))
            for scope, expected in [('unclaimed', unclaimed_ids), ('mine', mine_ids)]:
                result = self.ok(client.get('/api/admin/withdrawals/', {'status': 'PENDING', 'claim_scope': scope}))['data']
                self.assertEqual(result['claim_scope_applied'], scope)
                self.assertEqual(result['total'], len(expected))
                self.assertEqual({row['id'] for row in result['list']}, set(expected))
            others = self.ok(client.get('/api/admin/withdrawals/', {'status': 'PENDING', 'claim_scope': 'other'}))['data']
            self.assertEqual(others['claim_scope_applied'], 'other')
            self.assertEqual(others['total'], total - unclaimed - mine)
            self.assertTrue(all(row['claimed_by_account'] and not row['claim_is_mine'] for row in others['list']))

        check(self.staff_client, 2, 2, 0, ids, [])
        first = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{ids[0]}/claim/'))['data']
        check(self.staff_client, 2, 1, 1, [ids[1]], [ids[0]])
        self.ok(second.post(f'/api/admin/withdrawals/{ids[1]}/claim/'))
        check(second, 2, 0, 1, [], [ids[1]])
        release = {'confirmed_unpaid': True, 'reason': '隔离测试核实未打款',
                   'expected_claimed_by_account': first['claimed_by_account'], 'expected_claimed_at': first['claimed_at']}
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{ids[0]}/release-claim/', release, format='json'))
        check(self.staff_client, 2, 1, 0, [ids[0]], [])
        reclaimed = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{ids[0]}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{ids[0]}/reject/',
            {'audit_remark': '隔离测试驳回', 'expected_claimed_at': reclaimed['claimed_at']}, format='json'))
        check(self.staff_client, 1, 0, 0, [], [])
        self.assertEqual(self.staff_client.get('/api/admin/withdrawals/', {'claim_scope': 'invalid'}).status_code, 400)
        role.permissions = ['dashboard:view']
        role.save(update_fields=['permissions'])
        data = self.ok(self.staff_client.get('/api/admin/dashboard/stats'))['data']
        self.assertIsNone(data['pending_withdraw_unclaimed'])
        self.assertIsNone(data['pending_withdraw_mine'])
        self.assertEqual(self.staff_client.get('/api/admin/withdrawals/', {'claim_scope': 'mine'}).status_code, 403)

    def test_application_rejection_and_permission_change_match_review_queue(self):
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'dashboard:view']
        role.save(update_fields=['permissions'])
        payee = self.bind('local-test-account')
        request_id = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'dashboard-withdraw-request'), format='json'))['data']['request']['id']

        def snapshot():
            return self.ok(self.staff_client.get('/api/admin/dashboard/stats'))['data']

        queue = self.ok(self.staff_client.get('/api/admin/withdrawals/', {'status': 'PENDING'}))['data']
        self.assertEqual(snapshot()['pending_withdraw_total'], 1)
        self.assertEqual([row['id'] for row in queue['list']], [request_id])
        claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{request_id}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{request_id}/reject/',
                                       {'audit_remark': '隔离测试核对资料', 'expected_claimed_at': claim['claimed_at']}, format='json'))
        self.assertEqual(snapshot()['pending_withdraw_total'], 0)
        self.assertEqual(self.ok(self.staff_client.get('/api/admin/withdrawals/', {'status': 'PENDING'}))['data']['total'], 0)
        role.permissions = ['dashboard:view']
        role.save(update_fields=['permissions'])
        self.assertIsNone(snapshot()['pending_withdraw_total'])
        self.assertEqual(self.staff_client.get('/api/admin/withdrawals/', {'status': 'PENDING'}).status_code, 403)
