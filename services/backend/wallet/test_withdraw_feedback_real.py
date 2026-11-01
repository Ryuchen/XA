"""Real withdrawal workflow and receipt states used by the provider UI."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from wallet.models import IdempotencyKey, SystemConfig, WithdrawRequest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawalFeedbackRealTest(APITestCase):
    def setUp(self):
        self.provider = make_provider()
        self.admin = make_console_user(['withdraw:view', 'withdraw:audit'])
        self.account = get_or_create_account_for_legacy_user(self.provider)
        admin_account = get_or_create_account_for_legacy_user(self.admin)
        self.admin.admin_membership.account = admin_account
        self.admin.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.account
        self.provider.escort_profile.save(update_fields=['account'])
        self.wallet = get_wallet(user=self.provider, account=self.account)
        self.wallet.balance = 60000
        self.wallet.save(update_fields=['balance'])
        SystemConfig.objects.update_or_create(key='withdraw_tax_rate', defaults={'value': '2'})
        self.provider_client = self.client_for(self.account)
        self.staff_client = self.client_for(admin_account)

    @staticmethod
    def client_for(account):
        token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        return client

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data

    def bind(self, account_number, previous=None):
        return self.ok(self.provider_client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': account_number, 'payee_name': '本地测试',
            'expected_id': previous['id'] if previous else 0,
            'expected_version': previous['version'] if previous else 0,
        }, format='json'))['data']

    def payload(self, row, request_id):
        return {'amount': 20000, 'withdrawal_account_id': row['id'],
                'expected_payee_version': row['version'], 'client_request_id': request_id}

    def test_pending_approval_and_changed_account_keep_real_snapshot(self):
        original = self.bind('first-account-1234')
        first_payload = self.payload(original, 'withdraw-first-receipt')
        first = self.ok(self.provider_client.post('/api/wallet/withdraw/', first_payload, format='json'))
        self.assertIn('预计打款人民币 ¥196', first['msg'])
        self.assertNotIn('实际到账', first['msg'])
        self.assertEqual(first['data']['request']['status'], 'PENDING')
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))

        updated = self.bind('second-account-5678', original)
        second = self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(updated, 'withdraw-second-receipt'), format='json'))
        self.assertEqual(second['data']['request']['status'], 'PENDING')
        first_id = first['data']['request']['id']
        claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{first_id}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{first_id}/approve/',
                                       {'payout_reference': 'LOCAL-TEST-TRANSFER', 'expected_claimed_at': claim['claimed_at']}, format='json'))
        inbox = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        paid = next(message for message in inbox if message['title'] == '提现审核通过')
        self.assertIn('人民币 ¥196.00', paid['preview'])
        message = self.ok(self.provider_client.get(f"/api/messages/{paid['id']}/"))['data']
        self.assertIn('已登记打款人民币 ¥196.00', message['detail'])
        self.assertIn('折合 1960 兴安币', message['detail'])
        self.assertIn('代扣税', message['detail'])
        self.assertNotIn('将结算', message['detail'])
        rows = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        history = {row['id']: row for row in rows}
        self.assertEqual((history[first_id]['status'], history[first_id]['payee_account'], history[first_id]['payout_reference']),
                         ('APPROVED', 'first-account-1234', 'LOCAL-TEST-TRANSFER'))
        self.assertEqual((history[second['data']['request']['id']]['status'], history[second['data']['request']['id']]['payee_account']),
                         ('PENDING', 'second-account-5678'))
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (20000, 20000))
        replay = self.provider_client.post('/api/wallet/withdraw/', first_payload, format='json').data
        self.assertEqual(replay['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(replay['fundResult'], first)
        self.assertEqual(WithdrawRequest.objects.filter(account=self.account).count(), 2)

    def test_conflicting_and_historical_receipts_do_not_prove_a_new_application(self):
        row = self.bind('local-account-4321')
        payload = self.payload(row, 'withdraw-original-request')
        self.ok(self.provider_client.post('/api/wallet/withdraw/', payload, format='json'))
        conflict = self.provider_client.post('/api/wallet/withdraw/', {**payload, 'amount': 21000}, format='json').data
        self.assertEqual(conflict['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertNotIn('fundResult', conflict)
        IdempotencyKey.objects.create(account=self.account, scope='withdraw', request_id='withdraw-historical')
        unknown = self.provider_client.post('/api/wallet/withdraw/', {**payload, 'client_request_id': 'withdraw-historical'}, format='json').data
        self.assertEqual(unknown['errorCode'], 'FUND_RESULT_UNAVAILABLE')
        self.assertNotIn('fundResult', unknown)
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (40000, 20000))
        self.assertEqual(WithdrawRequest.objects.filter(account=self.account).count(), 1)

    def test_approval_message_preserves_actual_payout_cents(self):
        account = self.bind('local-account-1234')
        payload = {**self.payload(account, 'withdraw-cents'), 'amount': 20001}
        request = self.ok(self.provider_client.post('/api/wallet/withdraw/', payload, format='json'))['data']['request']
        self.assertEqual(request['actual_amount'], 19601)
        claim = self.ok(self.staff_client.post(f"/api/admin/withdrawals/{request['id']}/claim/"))['data']
        self.ok(self.staff_client.post(f"/api/admin/withdrawals/{request['id']}/approve/",
                                      {'payout_reference': 'LOCAL-CENTS-TEST', 'expected_claimed_at': claim['claimed_at']}, format='json'))
        inbox = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        paid = next(message for message in inbox if message['title'] == '提现审核通过')
        self.assertIn('人民币 ¥196.01', paid['preview'])
