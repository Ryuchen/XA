"""SMS transport failures must not roll back rate limits or code records."""
from unittest.mock import patch

from django.db import transaction
from django.test import TransactionTestCase

from club_accounts.models import ClubAccount
from stores.models import PlatformAccount, PlatformLoginChallenge, PlatformRoleGrant
from stores.platform_auth import PlatformAuthError, start_challenge
from users.models import PasswordLoginChallenge
from users.password_login import PasswordSmsError, start_password_sms
from users.sms_transport import SmsUnavailable


class SmsCommitTests(TransactionTestCase):
    def assert_persisted_then_fail(self, model):
        def fail(phone, code):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            self.assertEqual(model.objects.count(), 1)
            raise SmsUnavailable('发送结果不明')
        return fail

    def test_store_sms_failure_keeps_committed_cooldown(self):
        account = ClubAccount.objects.create_account(username='sms-owner', password='test-password',
            account_type='BOSS', phone='13800001111')
        with patch('users.password_login.send_verification_code', side_effect=self.assert_persisted_then_fail(PasswordLoginChallenge)) as send:
            with self.assertRaises(SmsUnavailable):
                start_password_sms(account, 'boss')
            self.assertTrue(PasswordLoginChallenge.objects.get(account=account).sms_hash)
            with self.assertRaises(PasswordSmsError) as result:
                start_password_sms(account, 'boss')
            self.assertEqual(result.exception.error_code, 'SMS_RATE_LIMITED')
            self.assertEqual(send.call_count, 1)

    def test_platform_sms_failure_keeps_committed_cooldown(self):
        account = PlatformAccount(username='sms-platform', phone='13800002222')
        account.set_password('test-password')
        account.save()
        PlatformRoleGrant.objects.create(account=account, role='SUPER_ADMIN')
        with patch('stores.platform_auth.send_verification_code', side_effect=self.assert_persisted_then_fail(PlatformLoginChallenge)) as send:
            with self.assertRaises(SmsUnavailable):
                start_challenge(account)
            self.assertTrue(PlatformLoginChallenge.objects.get(account=account).code_hash)
            with self.assertRaises(PlatformAuthError) as result:
                start_challenge(account)
            self.assertEqual(result.exception.code, 'SMS_RATE_LIMITED')
            self.assertEqual(send.call_count, 1)

    def test_stale_password_check_cannot_issue_a_challenge(self):
        account = ClubAccount.objects.create_account(username='sms-stale', password='old-password',
            account_type='BOSS', phone='13800001111')
        changed = ClubAccount.objects.get(pk=account.pk)
        changed.set_password('new-password')
        changed.save(update_fields=['password'])
        with patch('users.password_login.send_verification_code') as send:
            with self.assertRaises(PasswordSmsError) as result:
                start_password_sms(account, 'boss')
            self.assertEqual(result.exception.error_code, 'ACCOUNT_CHANGED')
            self.assertFalse(PasswordLoginChallenge.objects.exists())
            send.assert_not_called()
