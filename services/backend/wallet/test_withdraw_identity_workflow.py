"""Withdrawal review uses the same provider identity as the account app."""
from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount, LegacyAccountMap
from wallet.models import WithdrawRequest
from wallet import test_withdraw_feedback_real as feedback


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'withdraw-workflow-tests'}})
class WithdrawalIdentityWorkflowTest(APITestCase):
    setUp = feedback.WithdrawalFeedbackRealTest.setUp
    client_for = staticmethod(feedback.WithdrawalFeedbackRealTest.client_for)
    ok = feedback.WithdrawalFeedbackRealTest.ok
    bind = feedback.WithdrawalFeedbackRealTest.bind
    payload = feedback.WithdrawalFeedbackRealTest.payload
    def test_staff_searches_and_audits_the_actual_business_account(self):
        payee = self.bind('identity-test-wechat')
        result = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'identity-withdrawal'), format='json'))
        withdraw_id = result['data']['request']['id']
        # Simulate a migrated historical identity: the legacy display fields
        # differ, while the actual account and wallet relationship remain intact.
        self.provider.username = 'obsolete-provider-username'
        self.provider.nickname = '历史旧昵称'
        self.provider.save(update_fields=['username', 'nickname'])
        self.account.username = 'current-provider-username'
        self.account.nickname = '现行陪玩昵称'
        self.account.save(update_fields=['username', 'nickname'])
        rows = self.ok(self.staff_client.get('/api/admin/withdrawals/', {'provider_nickname': '现行陪玩'}))['data']['list']
        row = next(item for item in rows if item['id'] == withdraw_id)
        self.assertEqual((row['user_name'], row['provider_username'], row['provider_nickname']),
                         ('现行陪玩昵称', 'current-provider-username', '现行陪玩昵称'))
        self.assertFalse(self.ok(self.staff_client.get('/api/admin/withdrawals/', {'provider_nickname': '历史旧昵称'}))['data']['list'])
        for key, value in [('provider_username', 'current-provider'), ('keyword', '现行陪玩')]:
            matched = self.ok(self.staff_client.get('/api/admin/withdrawals/', {key: value}))['data']['list']
            self.assertTrue(any(item['id'] == withdraw_id for item in matched))
        self.assertFalse(self.ok(self.staff_client.get('/api/admin/withdrawals/', {'keyword': 'obsolete-provider'}))['data']['list'])
        claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdraw_id}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{withdraw_id}/reject/',
                                       {'audit_remark': '隔离验收重新核对收款资料', 'expected_claimed_at': claim['claimed_at']}, format='json'))
        history = self.ok(self.provider_client.get('/api/wallet/withdraw/'))['data']['requests']
        self.assertEqual(next(item for item in history if item['id'] == withdraw_id)['status'], 'REJECTED')
        self.wallet.refresh_from_db()
        self.assertEqual((self.wallet.balance, self.wallet.frozen_amount), (60000, 0))
        messages = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        self.assertTrue(any(item['title'] == '提现审核未通过' for item in messages))

    def test_unbound_historical_withdrawal_uses_legacy_identity(self):
        payee = self.bind('legacy-test-wechat')
        result = self.ok(self.provider_client.post('/api/wallet/withdraw/',
            self.payload(payee, 'legacy-withdrawal'), format='json'))
        withdraw_id = result['data']['request']['id']
        WithdrawRequest.objects.filter(pk=withdraw_id).update(account=None)
        self.provider.nickname = '历史陪玩'
        self.provider.save(update_fields=['nickname'])
        rows = self.ok(self.staff_client.get('/api/admin/withdrawals/', {'provider_nickname': '历史陪玩'}))['data']['list']
        self.assertEqual(next(item for item in rows if item['id'] == withdraw_id)['user_name'], '历史陪玩')

    def test_audit_messages_reach_withdrawal_account_after_legacy_remapping(self):
        payee = self.bind('mapping-test-wechat')
        one = self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, 'mapping-approved'), format='json'))['data']['request']['id']
        two = self.ok(self.provider_client.post('/api/wallet/withdraw/', self.payload(payee, 'mapping-rejected'), format='json'))['data']['request']['id']
        # The withdrawal and wallet retain the original explicit account;
        # the legacy mapping alone is deliberately changed to another account.
        LegacyAccountMap.objects.filter(legacy_user_id=self.provider.pk).delete()
        other = ClubAccount.objects.create(username='mapping-other-provider', nickname='隔离旧映射', account_type='PROVIDER')
        LegacyAccountMap.objects.create(legacy_user_id=self.provider.pk, account=other)
        first_claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{one}/claim/'))['data']
        second_claim = self.ok(self.staff_client.post(f'/api/admin/withdrawals/{two}/claim/'))['data']
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{one}/approve/', {'payout_reference': 'LOCAL-TEST-PAYOUT', 'expected_claimed_at': first_claim['claimed_at']}, format='json'))
        self.ok(self.staff_client.post(f'/api/admin/withdrawals/{two}/reject/', {'audit_remark': '隔离验收', 'expected_claimed_at': second_claim['claimed_at']}, format='json'))
        messages = self.ok(self.provider_client.get('/api/messages/'))['data']['messages']
        titles = {item['title'] for item in messages}
        self.assertTrue({'提现审核通过', '提现审核未通过'}.issubset(titles))
        from site_messages.models import Message
        self.assertFalse(Message.objects.filter(recipient_account=other, title__in=['提现审核通过', '提现审核未通过']).exists())
