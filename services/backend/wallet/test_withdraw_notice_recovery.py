from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_console_user, make_provider
from site_messages.models import Message, WithdrawalNoticeDelivery
from site_messages.withdrawal_review import act, detail, list_reviews
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import Transaction, Wallet, WithdrawalPlatformEvent, WithdrawalRelease, WithdrawRequest
from wallet.services import get_wallet
from wallet.withdraw_notice_recovery import recover_withdrawal_messages
from wallet.withdraw_notifications import withdrawal_message


@override_settings(ALLOWED_HOSTS=['*'], WITHDRAWAL_NOTICE_RECOVERY_ENABLED=True,
                   PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=False)
class WithdrawalNoticeRecoveryTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()

    def make_withdrawal(self, store=None, *, username='recovery-provider'):
        store = store or self.second
        with store_context(store):
            provider = make_provider()
            provider.username = username
            provider.save(update_fields=['username'])
            account = get_or_create_account_for_legacy_user(provider)
            wallet = get_wallet(account=account, user=provider)
            wallet.balance = 80000
            wallet.frozen_amount = 20000
            wallet.save(update_fields=['balance', 'frozen_amount'])
            debit = Transaction.objects.create(wallet=wallet, amount=-20000,
                tx_type=Transaction.TxType.WITHDRAW, balance_before=100000, balance_after=80000,
                status=Transaction.Status.PENDING)
            row = WithdrawRequest.objects.create(user=provider, account=account,
                account_business_cycle=account.business_cycle, amount=20000, tax_rate=5,
                tax_amount=1000, actual_amount=19000, payee_method='WECHAT',
                payee_account='isolated-payee', payee_name='隔离收款', workflow_version=2,
                transaction=debit)
            return row.pk, account.pk, wallet.pk, debit.pk

    def mark_store_auditor(self, withdrawal_id):
        staff = make_console_user()
        account = get_or_create_account_for_legacy_user(staff)
        WithdrawRequest.objects.filter(pk=withdrawal_id).update(
            auditor_id=staff.pk, auditor_account_id=account.pk)
        return staff, account

    def messages(self, account_id, store=None):
        with store_context(store or self.second):
            return list(Message.objects.filter(recipient_account_id=account_id).order_by('pk'))

    @override_settings(WITHDRAWAL_NOTICE_RECOVERY_ENABLED=False)
    def test_disabled_recovery_does_not_project_or_change_financial_facts(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            self.assertEqual(recover_withdrawal_messages(), 0)
            self.assertFalse(Message.objects.exists())
            self.assertFalse(WithdrawalNoticeDelivery.objects.exists())
            row = WithdrawRequest.objects.get(pk=pk)
            wallet = Wallet.objects.get(pk=wallet_id)
            debit = Transaction.objects.get(pk=debit_id)
        self.assertEqual((row.status, row.transaction_id, debit.status,
                          wallet.balance, wallet.frozen_amount),
                         ('PENDING', debit_id, 'PENDING', 80000, 20000))

    def test_submission_is_recovered_once_without_changing_money(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertEqual(recover_withdrawal_messages(), 0)
            notices = self.messages(account_id)
            self.assertEqual([row.title for row in notices], ['提现申请已提交'])
            self.assertEqual(notices[0].recipient_business_cycle,
                             WithdrawRequest.objects.get(pk=pk).account_business_cycle)
            self.assertEqual(notices[0].action_url, f'/pages/wallet/index?withdraw_id={pk}')
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'owner:{pk}:submitted')
            self.assertEqual((delivery.message_id, delivery.skipped_reason), (notices[0].pk, ''))
            row = WithdrawRequest.objects.get(pk=pk)
            wallet = Wallet.objects.get(pk=wallet_id)
            debit = Transaction.objects.get(pk=debit_id)
        self.assertEqual((row.status, row.transaction_id, debit.status,
                          wallet.balance, wallet.frozen_amount),
                         ('PENDING', debit_id, 'PENDING', 80000, 20000))

    def test_shared_original_debit_is_an_explicit_review_for_every_withdrawal(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        with store_context(self.second):
            original = WithdrawRequest.objects.get(pk=pk)
            original.pk = None
            original.save(force_insert=True)
            self.assertEqual(recover_withdrawal_messages(), 2)
            self.assertFalse(Message.objects.filter(recipient_account_id=account_id).exists())
            deliveries = WithdrawalNoticeDelivery.objects.filter(
                event_key__endswith=':submitted', skipped_reason='提现原始申请或解冻凭证待核对')
            self.assertEqual(deliveries.count(), 2)
            self.assertEqual(WithdrawRequest.objects.filter(transaction_id=debit_id).count(), 2)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_broken_owner_source_waits_for_review_and_never_sends(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        with store_context(self.second):
            Transaction.objects.filter(pk=debit_id).update(balance_after=80001)
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertFalse(Message.objects.filter(recipient_account_id=account_id).exists())
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'owner:{pk}:submitted')
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)
            Transaction.objects.filter(pk=debit_id).update(balance_after=80000)
        self.assertEqual(len(self.messages(account_id)), 0)

    def test_changed_recipient_cycle_is_an_explicit_skip_not_a_new_identity_delivery(self):
        pk, account_id, _, _ = self.make_withdrawal()
        with store_context(self.second):
            ClubAccount.objects.filter(pk=account_id).update(business_cycle=2, license_membership_active=False)
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertFalse(Message.objects.filter(recipient_account_id=account_id).exists())
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'owner:{pk}:submitted')
            self.assertEqual(delivery.skipped_reason, '原提现账号或门店周期已变化')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)
            self.assertEqual(WithdrawRequest.objects.get(pk=pk).account_business_cycle, 1)

    def test_owner_cancellation_requires_the_original_failed_debit_and_release(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.CANCELLED)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertFalse(Message.objects.filter(recipient_account_id=account_id,
                title='提现申请已取消').exists())
            WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='OWNER', actor_account_id=account_id)
            self.assertEqual(recover_withdrawal_messages(), 1)
            notices = self.messages(account_id)
            self.assertEqual([row.title for row in notices], ['提现申请已提交', '提现申请已取消'])
            self.assertEqual(recover_withdrawal_messages(), 0)
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'owner:{pk}:owner_cancelled')
            self.assertEqual(delivery.message_id, notices[1].pk)

    @override_settings(WITHDRAWAL_NOTICE_RECOVERY_ENABLED=False)
    def test_legacy_submission_records_a_receipt_before_recovery_is_enabled(self):
        pk, account_id, _, _ = self.make_withdrawal()
        with store_context(self.second):
            self.assertIsNotNone(withdrawal_message(WithdrawRequest.objects.get(pk=pk), 'submitted'))
            self.assertEqual(WithdrawalNoticeDelivery.objects.filter(
                event_key=f'owner:{pk}:submitted', message__isnull=False).count(), 1)
        with override_settings(WITHDRAWAL_NOTICE_RECOVERY_ENABLED=True):
            with store_context(self.second):
                self.assertEqual(recover_withdrawal_messages(), 0)
                self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交'])

    def test_rejected_without_original_release_is_not_reported_as_store_approved(self):
        pk, account_id, _, _ = self.make_withdrawal()
        actor = uuid4()
        with store_context(self.second):
            staff = make_console_user()
            staff_account = get_or_create_account_for_legacy_user(staff)
            WithdrawRequest.objects.filter(pk=pk).update(
                status=WithdrawRequest.Status.REJECTED, audited_at=timezone.now(),
                auditor_id=staff.pk, auditor_account_id=staff_account.pk)
            WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='mark-unknown', request_id='broken-store-review',
                request_fingerprint='9' * 64, from_status='PLATFORM_APPROVED', to_status='PAYMENT_UNKNOWN')
            self.assertEqual(recover_withdrawal_messages(), 3)
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'store:{pk}:store_approved')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交'])

    def test_store_rejection_requires_the_original_release_before_claiming_money_returned(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            WithdrawRequest.objects.filter(pk=pk).update(
                status=WithdrawRequest.Status.REJECTED, audit_remark='隔离门店驳回', audited_at=timezone.now())
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            self.assertEqual(recover_withdrawal_messages(), 2)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交'])
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'store:{pk}:store_rejected')
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_store_rejection_review_rechecks_the_repaired_original_release(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            staff = make_console_user()
            staff_account = get_or_create_account_for_legacy_user(staff)
            WithdrawRequest.objects.filter(pk=pk).update(
                status=WithdrawRequest.Status.REJECTED, audit_remark='隔离门店驳回', audited_at=timezone.now(),
                auditor_id=staff.pk, auditor_account_id=staff_account.pk)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            self.assertEqual(recover_withdrawal_messages(), 2)
            delivery = WithdrawalNoticeDelivery.objects.get(event_key=f'store:{pk}:store_rejected')
            listed = list_reviews(SimpleNamespace(query_params={}))
            self.assertEqual(listed.data['data']['total'], 1)
            self.assertEqual(listed.data['data']['items'][0]['id'], str(delivery.pk))
            self.assertEqual(detail(delivery.pk).data['data']['event_key'], delivery.event_key)
            WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='STORE', actor_account_id=staff_account.pk)
            self.assertEqual(WithdrawalRelease.objects.get(withdrawal_id=pk).actor_account_id,
                             WithdrawRequest.objects.get(pk=pk).auditor_account_id)
            request = SimpleNamespace(data={'action': 'recheck', 'reason': '已补齐原解冻凭证',
                'expected_note': '提现原始申请或解冻凭证待核对'}, account=staff_account,
                legacy_user=staff, path='/api/admin/notice-reviews/withdrawal/1/', _request=SimpleNamespace())
            with self.captureOnCommitCallbacks(execute=False, using='store_b') as callbacks:
                response = act(request, delivery.pk)
            self.assertEqual(response.data['code'], 0)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            delivery.refresh_from_db()
            self.assertIsNotNone(delivery.message_id)
            self.assertEqual([row.title for row in self.messages(account_id)],
                             ['提现申请已提交', '提现审核未通过'])

    def test_store_review_and_each_platform_event_are_projected_once(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        actor = uuid4()
        reviewed_at = timezone.now()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            WithdrawRequest.objects.filter(pk=pk).update(
                status=WithdrawRequest.Status.STORE_APPROVED, audited_at=reviewed_at,
                platform_reviewer_id=actor, platform_reviewed_at=reviewed_at)
            approve = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='approve', request_id='approve-1',
                request_fingerprint='a' * 64, from_status='STORE_APPROVED', to_status='PLATFORM_APPROVED')
            unknown = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='mark-unknown', request_id='unknown-1',
                request_fingerprint='b' * 64, from_status='PLATFORM_APPROVED', to_status='PAYMENT_UNKNOWN',
                detail={'reason': '内部核实不得外发'})
            rejected = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='reject', request_id='reject-1',
                request_fingerprint='c' * 64, from_status='PAYMENT_UNKNOWN', to_status='REJECTED',
                detail={'reason': '已核实未打款'})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.REJECTED)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            release = WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='PLATFORM', platform_actor_id=actor)
            WithdrawalPlatformEvent.objects.filter(pk=rejected.pk).update(detail={
                'reason': '已核实未打款', 'confirmed_unpaid': True, 'release_id': release.pk})
            self.assertEqual(recover_withdrawal_messages(), 5)
            self.assertEqual(recover_withdrawal_messages(), 0)
            notices = self.messages(account_id)
            self.assertEqual([row.title for row in notices], ['提现申请已提交', '提现门店初审通过',
                '提现平台终审通过', '提现打款待核实', '提现审核未通过'])
            self.assertNotIn('内部核实不得外发', notices[3].detail)
            self.assertIn('已核实未打款', notices[4].detail)
            self.assertEqual(set(WithdrawalNoticeDelivery.objects.filter(
                platform_event_id__in=[approve.pk, unknown.pk, rejected.pk]).values_list('platform_event_id', flat=True)),
                {approve.pk, unknown.pk, rejected.pk})
            self.assertEqual(WithdrawalNoticeDelivery.objects.filter(
                event_key=f'store:{pk}:store_approved').count(), 1)

    def test_platform_cancellation_rejects_a_release_from_the_wrong_actor(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        actor = uuid4()
        with store_context(self.second):
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='cancel', request_id='cancel-1',
                request_fingerprint='d' * 64, from_status='PLATFORM_APPROVED', to_status='CANCELLED',
                detail={'reason': '隔离取消', 'confirmed_unpaid': True})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.CANCELLED,
                audited_at=timezone.now())
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            release = WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='OWNER', actor_account_id=account_id)
            WithdrawalPlatformEvent.objects.filter(pk=event.pk).update(detail={
                'reason': '隔离取消', 'confirmed_unpaid': True, 'release_id': release.pk})
            self.assertEqual(recover_withdrawal_messages(), 3)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交'])
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_paid_notice_requires_the_original_debit_to_be_successful(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        actor, paid_at = uuid4(), timezone.now()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='paid', request_id='paid-1',
                request_fingerprint='e' * 64, from_status='PLATFORM_APPROVED', to_status='PAID',
                detail={'payout_reference': 'ORIGINAL-RECEIPT', 'paid_at': paid_at.isoformat(), 'paid_amount_fen': 19000})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.PAID,
                audited_at=paid_at, platform_reviewer_id=actor, platform_reviewed_at=paid_at,
                platform_payer_id=actor, payout_recorded_at=paid_at, payout_reference='ORIGINAL-RECEIPT', paid_at=paid_at)
            self.assertEqual(recover_withdrawal_messages(), 3)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交', '提现门店初审通过'])
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_failed_message_write_is_retried_without_duplicating_the_financial_fact(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            with patch('site_messages.models.Message.objects.create', side_effect=DatabaseError('isolated message outage')):
                with self.assertLogs('wallet.withdraw_notifications', level='ERROR'):
                    self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertFalse(WithdrawalNoticeDelivery.objects.filter(event_key=f'owner:{pk}:submitted').exists())
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交'])
            self.assertEqual(recover_withdrawal_messages(), 0)
            row = WithdrawRequest.objects.get(pk=pk)
            wallet = Wallet.objects.get(pk=wallet_id)
            debit = Transaction.objects.get(pk=debit_id)
        self.assertEqual((row.status, row.transaction_id, debit.status,
                          wallet.balance, wallet.frozen_amount),
                         ('PENDING', debit_id, 'PENDING', 80000, 20000))

    def test_paid_notice_requires_the_exact_original_payout_receipt(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        actor, paid_at = uuid4(), timezone.now()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='paid', request_id='paid-wrong-receipt',
                request_fingerprint='f' * 64, from_status='PLATFORM_APPROVED', to_status='PAID',
                detail={'payout_reference': 'WRONG-RECEIPT', 'paid_at': paid_at.isoformat(), 'paid_amount_fen': 19000})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.PAID,
                audited_at=paid_at, platform_reviewer_id=actor, platform_reviewed_at=paid_at,
                platform_payer_id=actor, payout_recorded_at=paid_at, payout_reference='ORIGINAL-RECEIPT', paid_at=paid_at)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.SUCCESS)
            self.assertEqual(recover_withdrawal_messages(), 3)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交', '提现门店初审通过'])
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_duplicate_paid_events_do_not_create_duplicate_payout_notices(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        actor, paid_at = uuid4(), timezone.now()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            detail = {'payout_reference': 'ORIGINAL-RECEIPT', 'paid_at': paid_at.isoformat(), 'paid_amount_fen': 19000}
            for suffix in ('one', 'two'):
                WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                    platform_actor_id=actor, actor_name='隔离财务', action='paid', request_id='paid-' + suffix,
                    request_fingerprint=suffix[0] * 64, from_status='PLATFORM_APPROVED', to_status='PAID', detail=detail)
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.PAID,
                audited_at=paid_at, platform_reviewer_id=actor, platform_reviewed_at=paid_at,
                platform_payer_id=actor, payout_recorded_at=paid_at, payout_reference='ORIGINAL-RECEIPT', paid_at=paid_at)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.SUCCESS)
            self.assertEqual(recover_withdrawal_messages(), 4)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交', '提现门店初审通过'])
            deliveries = WithdrawalNoticeDelivery.objects.filter(event_key__startswith='platform:')
            self.assertEqual(deliveries.count(), 2)
            self.assertTrue(all(row.skipped_reason == '提现原始申请或解冻凭证待核对' for row in deliveries))
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_platform_rejection_requires_the_event_to_reference_the_actual_release(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        actor = uuid4()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            reviewed_at = timezone.now()
            WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='approve', request_id='approve-before-wrong-release',
                request_fingerprint='2' * 64, from_status='STORE_APPROVED', to_status='PLATFORM_APPROVED')
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='reject', request_id='reject-wrong-release',
                request_fingerprint='1' * 64, from_status='PAYMENT_UNKNOWN', to_status='REJECTED',
                detail={'reason': '隔离驳回', 'confirmed_unpaid': True, 'release_id': 999999})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.REJECTED,
                audited_at=timezone.now(), platform_reviewer_id=actor, platform_reviewed_at=reviewed_at)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='PLATFORM', platform_actor_id=actor)
            self.assertEqual(recover_withdrawal_messages(), 4)
            self.assertEqual([row.title for row in self.messages(account_id)],
                             ['提现申请已提交', '提现门店初审通过', '提现平台终审通过'])
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_store_review_does_not_reinterpret_an_original_approval_as_rejection(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            staff = make_console_user()
            staff_account = get_or_create_account_for_legacy_user(staff)
            WithdrawRequest.objects.filter(pk=pk).update(
                status=WithdrawRequest.Status.REJECTED, audit_remark='状态被改写', audited_at=timezone.now(),
                auditor_id=staff.pk, auditor_account_id=staff_account.pk)
            delivery = WithdrawalNoticeDelivery.objects.create(event_key=f'store:{pk}:store_approved',
                withdrawal_id=pk, skipped_reason='提现原始申请或解冻凭证待核对')
            WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='STORE', actor_account_id=staff_account.pk)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            request = SimpleNamespace(data={'action': 'recheck', 'reason': '按原门店事件重核',
                'expected_note': '提现原始申请或解冻凭证待核对'}, account=staff_account,
                legacy_user=staff, path='/api/admin/notice-reviews/withdrawal/1/', _request=SimpleNamespace())
            with self.captureOnCommitCallbacks(execute=False, using='store_b') as callbacks:
                response = act(request, delivery.pk)
            self.assertEqual(response.data['code'], 0)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            delivery.refresh_from_db()
            self.assertIsNone(delivery.message_id)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertEqual(self.messages(account_id), [])

    @override_settings(WITHDRAWAL_NOTICE_RECOVERY_ENABLED=False)
    def test_legacy_notification_path_still_requires_the_original_release(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        with store_context(self.second):
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.CANCELLED)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            delivery = withdrawal_message(WithdrawRequest.objects.get(pk=pk), 'owner_cancelled')
            self.assertIsNotNone(delivery)
            self.assertIsNone(delivery.message_id)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertEqual(self.messages(account_id), [])

    def test_paid_notice_rejects_an_empty_payout_receipt(self):
        pk, account_id, _, debit_id = self.make_withdrawal()
        actor, paid_at = uuid4(), timezone.now()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='paid', request_id='paid-empty-receipt',
                request_fingerprint='3' * 64, from_status='PLATFORM_APPROVED', to_status='PAID',
                detail={'payout_reference': '', 'paid_at': paid_at.isoformat(), 'paid_amount_fen': 19000})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.PAID,
                audited_at=paid_at, platform_reviewer_id=actor, platform_reviewed_at=paid_at,
                platform_payer_id=actor, payout_recorded_at=paid_at, payout_reference='', paid_at=paid_at)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.SUCCESS)
            self.assertEqual(recover_withdrawal_messages(), 3)
            self.assertEqual([row.title for row in self.messages(account_id)], ['提现申请已提交', '提现门店初审通过'])
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertEqual(delivery.skipped_reason, '提现原始申请或解冻凭证待核对')
            self.assertIsNone(delivery.message_id)
            self.assertEqual(recover_withdrawal_messages(), 0)

    def test_platform_review_rechecks_the_repaired_original_release_reference(self):
        pk, account_id, wallet_id, debit_id = self.make_withdrawal()
        actor = uuid4()
        with store_context(self.second):
            self.mark_store_auditor(pk)
            reviewed_at = timezone.now()
            WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='approve', request_id='approve-before-recheck',
                request_fingerprint='5' * 64, from_status='STORE_APPROVED', to_status='PLATFORM_APPROVED')
            event = WithdrawalPlatformEvent.objects.create(withdrawal_id=pk,
                platform_actor_id=actor, actor_name='隔离财务', action='reject', request_id='reject-recheck',
                request_fingerprint='4' * 64, from_status='PLATFORM_APPROVED', to_status='REJECTED',
                detail={'reason': '隔离驳回', 'confirmed_unpaid': True, 'release_id': 999999})
            WithdrawRequest.objects.filter(pk=pk).update(status=WithdrawRequest.Status.REJECTED,
                audited_at=timezone.now(), platform_reviewer_id=actor, platform_reviewed_at=reviewed_at)
            Transaction.objects.filter(pk=debit_id).update(status=Transaction.Status.FAILED)
            Wallet.objects.filter(pk=wallet_id).update(balance=100000, frozen_amount=0)
            release = WithdrawalRelease.objects.create(withdrawal_id=pk, wallet_id=wallet_id,
                original_transaction_id=debit_id, amount=20000, balance_before=80000,
                balance_after=100000, frozen_before=20000, frozen_after=0,
                actor_kind='PLATFORM', platform_actor_id=actor)
            self.assertEqual(recover_withdrawal_messages(), 4)
            delivery = WithdrawalNoticeDelivery.objects.get(platform_event_id=event.pk)
            self.assertIsNone(delivery.message_id)
            WithdrawalPlatformEvent.objects.filter(pk=event.pk).update(detail={
                'reason': '隔离驳回', 'confirmed_unpaid': True, 'release_id': release.pk})
            staff = make_console_user()
            staff_account = get_or_create_account_for_legacy_user(staff)
            request = SimpleNamespace(data={'action': 'recheck', 'reason': '已修正原解冻引用',
                'expected_note': '提现原始申请或解冻凭证待核对'}, account=staff_account,
                legacy_user=staff, path='/api/admin/notice-reviews/withdrawal/1/', _request=SimpleNamespace())
            with self.captureOnCommitCallbacks(execute=False, using='store_b') as callbacks:
                response = act(request, delivery.pk)
            self.assertEqual(response.data['code'], 0)
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            delivery.refresh_from_db()
            self.assertIsNotNone(delivery.message_id)
            self.assertEqual([row.title for row in self.messages(account_id)],
                             ['提现申请已提交', '提现门店初审通过', '提现平台终审通过', '提现审核未通过'])

    def test_same_numeric_withdrawal_ids_stay_in_their_own_store(self):
        first_pk, first_account, _, _ = self.make_withdrawal(self.first, username='first-provider')
        second_pk, second_account, _, _ = self.make_withdrawal(self.second, username='second-provider')
        self.assertEqual(first_pk, second_pk)
        with store_context(self.first):
            self.assertEqual(recover_withdrawal_messages(), 1)
        with store_context(self.second):
            self.assertEqual(recover_withdrawal_messages(), 1)
            self.assertEqual(recover_withdrawal_messages(), 0)
        self.assertEqual([row.title for row in self.messages(first_account, self.first)], ['提现申请已提交'])
        self.assertEqual([row.title for row in self.messages(second_account, self.second)], ['提现申请已提交'])
        with store_context(self.first):
            self.assertEqual(Message.objects.count(), 1)
            self.assertEqual(Message.objects.get().recipient_account.username, 'first-provider')
            self.assertEqual(WithdrawalNoticeDelivery.objects.get(withdrawal_id=first_pk).message.recipient_account.username,
                             'first-provider')
        with store_context(self.second):
            self.assertEqual(Message.objects.count(), 1)
            self.assertEqual(Message.objects.get().recipient_account.username, 'second-provider')
