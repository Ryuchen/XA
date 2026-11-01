"""Real withdrawal claim and review; all accounts and balances are isolated."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections
from django.test import override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.tests.factories import make_console_user, make_provider
from wallet.models import SystemConfig, WithdrawRequest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class WithdrawClaimWorkflowTest(APITransactionTestCase):
    def setUp(self):
        provider = make_provider()
        self.owner = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = self.owner
        provider.escort_profile.save(update_fields=['account'])
        self.wallet = get_wallet(user=provider, account=self.owner)
        self.wallet.balance = 60000
        self.wallet.save(update_fields=['balance'])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value': '2'})
        self.staff = []
        for _ in range(2):
            user = make_console_user(['withdraw:view', 'withdraw:audit'])
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            self.staff.append(account)
        viewer = make_console_user(['withdraw:view'])
        self.viewer = get_or_create_account_for_legacy_user(viewer)
        viewer.admin_membership.account = self.viewer
        viewer.admin_membership.save(update_fields=['account'])
        client = self.client_for(self.owner)
        saved = self.ok(client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'test-account-1234', 'payee_name': '隔离测试',
            'expected_id': 0, 'expected_version': 0,
        }, format='json'))
        self.withdraw_id = self.ok(client.post('/api/wallet/withdraw/', {
            'amount': 20000, 'withdrawal_account_id': saved['id'],
            'expected_payee_version': saved['version'], 'client_request_id': 'claim-workflow-test',
        }, format='json'))['request']['id']
        self.url = f'/api/admin/withdrawals/{self.withdraw_id}/'

    @staticmethod
    def client_for(account):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']

    def test_claim_owner_only_approval_and_idempotence(self):
        first, other = [self.client_for(account) for account in self.staff]
        unclaimed = self.ok(first.get(self.url))
        self.assertIsNone(unclaimed['claimed_by_account'])
        self.assertFalse(unclaimed['claim_is_mine'])
        denied = first.post(self.url + 'approve/', {'payout_reference': 'isolated-only'}, format='json')
        self.assertEqual(denied.status_code, 409)
        viewer = self.client_for(self.viewer).post(self.url + 'claim/')
        self.assertEqual(viewer.status_code, 403)
        claimed = self.ok(first.post(self.url + 'claim/'))
        self.assertEqual(claimed['claimed_by_account'], self.staff[0].pk)
        self.assertTrue(claimed['claim_is_mine'])
        self.assertTrue(claimed['claimed_at'])
        self.assertEqual(self.ok(first.post(self.url + 'claim/'))['claimed_at'], claimed['claimed_at'])
        self.assertEqual(other.post(self.url + 'claim/').status_code, 409)
        self.assertEqual(other.post(self.url + 'approve/', {'payout_reference':'other'}, format='json').status_code, 409)
        self.assertEqual(other.post(self.url + 'reject/', {'audit_remark':'other'}, format='json').status_code, 409)
        self.assertFalse(self.ok(other.get(self.url))['claim_is_mine'])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        result = self.ok(first.post(self.url + 'approve/', {'payout_reference':'isolated-only', 'expected_claimed_at': claimed['claimed_at']}, format='json'))
        self.assertEqual(result['status'], 'APPROVED')
        self.assertEqual(result['auditor_name'], result['claimed_by_name'])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 0))
        self.assertEqual(first.post(self.url + 'claim/').status_code, 409)

    def test_owner_can_reject_and_restore_wallet(self):
        first = self.client_for(self.staff[0])
        claimed = self.ok(first.post(self.url + 'claim/'))
        self.ok(first.post(self.url + 'reject/', {'audit_remark':'隔离测试驳回', 'expected_claimed_at': claimed['claimed_at']}, format='json'))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (60000, 0))
        self.assertEqual(WithdrawRequest.objects.get(pk=self.withdraw_id).status, 'REJECTED')

    def test_manager_releases_verified_unpaid_claim_for_another_reviewer(self):
        first, other = [self.client_for(account) for account in self.staff]
        manager_user = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager_user)
        manager_user.admin_membership.account = manager_account
        manager_user.admin_membership.save(update_fields=['account'])
        manager = self.client_for(manager_account)
        claimed = self.ok(first.post(self.url + 'claim/'))
        payload = {'confirmed_unpaid': True, 'reason': '领取人离线，已核对未打款',
                   'expected_claimed_by_account': claimed['claimed_by_account'],
                   'expected_claimed_at': claimed['claimed_at']}
        self.assertEqual(first.post(self.url + 'release-claim/', payload, format='json').status_code, 403)
        self.assertEqual(manager.post(self.url + 'release-claim/', dict(payload, confirmed_unpaid=False), format='json').status_code, 400)
        self.assertEqual(manager.post(self.url + 'release-claim/', dict(payload, reason='  '), format='json').status_code, 400)
        released = self.ok(manager.post(self.url + 'release-claim/', payload, format='json'))
        self.assertIsNone(released['claimed_by_account'])
        self.assertIsNone(released['claimed_at'])
        audit = AdminAuditLog.objects.get(path=self.url + 'release-claim/')
        self.assertEqual(audit.operator_account_id, manager_account.pk)
        self.assertEqual(audit.request_body['previous_claimed_by_account'], self.staff[0].pk)
        self.assertEqual(audit.request_body['reason'], payload['reason'])
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        new_claim = self.ok(other.post(self.url + 'claim/'))
        self.assertEqual(new_claim['claimed_by_account'], self.staff[1].pk)
        # A delayed request from the old confirmation must not free a new claim.
        self.assertEqual(manager.post(self.url + 'release-claim/', payload, format='json').status_code, 409)
        self.assertEqual(first.post(self.url + 'reject/', {'audit_remark': '旧领取人'}, format='json').status_code, 409)
        self.ok(other.post(self.url + 'reject/', {'audit_remark': '隔离测试', 'expected_claimed_at': new_claim['claimed_at']}, format='json'))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (60000, 0))

    def test_claim_is_not_affected_by_stale_list_filters(self):
        first = self.client_for(self.staff[0])
        # A filtered queue may retain its filters when calling a detail action.
        # The ID identifies the request; the list filter must not hide a write
        # that has already taken effect and make its result look unsuccessful.
        claimed = self.ok(first.post(self.url + 'claim/?status=APPROVED'))
        self.assertTrue(claimed['claim_is_mine'])
        self.assertEqual(claimed['claimed_by_account'], self.staff[0].pk)
        self.assertEqual(WithdrawRequest.objects.get(pk=self.withdraw_id).claimed_by_account_id, self.staff[0].pk)
        self.ok(first.post(self.url + 'reject/', {'audit_remark': '隔离测试驳回', 'expected_claimed_at': claimed['claimed_at']}, format='json'))

    def test_same_account_reclaim_rejects_old_device_approval(self):
        first = self.client_for(self.staff[0])
        second = self.client_for(self.staff[0])
        manager_user = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager_user)
        manager_user.admin_membership.account = manager_account
        manager_user.admin_membership.save(update_fields=['account'])
        manager = self.client_for(manager_account)
        old = self.ok(first.post(self.url + 'claim/'))
        self.ok(manager.post(self.url + 'release-claim/', {
            'confirmed_unpaid': True, 'reason': '隔离核对未打款',
            'expected_claimed_by_account': old['claimed_by_account'], 'expected_claimed_at': old['claimed_at'],
        }, format='json'))
        new = self.ok(second.post(self.url + 'claim/'))
        self.assertNotEqual(old['claimed_at'], new['claimed_at'])
        stale = first.post(self.url + 'approve/', {
            'payout_reference': 'isolated-stale-reference', 'expected_claimed_at': old['claimed_at'],
        }, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        self.ok(second.post(self.url + 'approve/', {
            'payout_reference': 'isolated-current-reference', 'expected_claimed_at': new['claimed_at'],
        }, format='json'))

    def test_review_requires_current_claim_version(self):
        staff = self.client_for(self.staff[0])
        claimed = self.ok(staff.post(self.url + 'claim/'))
        self.assertEqual(staff.post(self.url + 'approve/', {'payout_reference': 'isolated'}, format='json').status_code, 409)
        self.assertEqual(staff.post(self.url + 'reject/', {'audit_remark': '隔离测试'}, format='json').status_code, 409)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        self.ok(staff.post(self.url + 'reject/', {
            'audit_remark': '隔离测试', 'expected_claimed_at': claimed['claimed_at'],
        }, format='json'))

    def test_same_account_reclaim_rejects_old_device_rejection(self):
        first = self.client_for(self.staff[0])
        second = self.client_for(self.staff[0])
        manager_user = make_console_user(['withdraw:view', 'withdraw:release'])
        manager_account = get_or_create_account_for_legacy_user(manager_user)
        manager_user.admin_membership.account = manager_account
        manager_user.admin_membership.save(update_fields=['account'])
        manager = self.client_for(manager_account)
        old = self.ok(first.post(self.url + 'claim/'))
        self.ok(manager.post(self.url + 'release-claim/', {
            'confirmed_unpaid': True, 'reason': '隔离核对未打款',
            'expected_claimed_by_account': old['claimed_by_account'], 'expected_claimed_at': old['claimed_at'],
        }, format='json'))
        new = self.ok(second.post(self.url + 'claim/'))
        self.assertNotEqual(old['claimed_at'], new['claimed_at'])
        stale = first.post(self.url + 'reject/', {
            'audit_remark': '旧设备驳回', 'expected_claimed_at': old['claimed_at'],
        }, format='json')
        self.assertEqual(stale.status_code, 409, stale.data)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        self.ok(second.post(self.url + 'reject/', {
            'audit_remark': '新领取核对', 'expected_claimed_at': new['claimed_at'],
        }, format='json'))

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_staff_race_to_claim_only_one_can_review(self):
        barrier = Barrier(2)
        def worker(account):
            close_old_connections()
            try:
                client = self.client_for(account)
                barrier.wait(timeout=10)
                response = client.post(self.url + 'claim/')
                return (account.pk, response.status_code)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, self.staff))
        self.assertEqual(sorted(status for _, status in results), [200, 409], results)
        winner = next(pk for pk, status in results if status == 200)
        self.assertEqual(WithdrawRequest.objects.get(pk=self.withdraw_id).claimed_by_account_id, winner)
