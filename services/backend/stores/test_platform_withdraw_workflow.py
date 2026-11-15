from unittest.mock import patch

from django.db import DatabaseError, connections
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.models import PlatformAccount, PlatformRoleGrant
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store
from wallet.models import SystemConfig, Transaction, WithdrawalPlatformEvent, WithdrawRequest, get_platform_wallet
from wallet.services import get_wallet
from site_messages.models import Message


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False, PLATFORM_IDENTITY_ENABLED=True)
class PlatformWithdrawalWorkflowTest(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first, self.second = resolve_store(), xinan_store()
        self.finance = PlatformAccount(username='qa-finance', phone='13800001111', is_phone_verified=True)
        self.finance.set_password('test-only-password'); self.finance.save()
        PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='xinan')
        self.finance_token = issue_tokens(self.finance, 'SMS', self.finance.phone)[0]
        with store_context(self.second):
            self.provider = make_provider()
            self.account = get_or_create_account_for_legacy_user(self.provider)
            self.wallet = get_wallet(account=self.account, user=self.provider)
            self.wallet.balance = 100000; self.wallet.save(update_fields=['balance'])
            staff = make_console_user(['withdraw:view', 'withdraw:audit'])
            self.staff = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = self.staff; staff.admin_membership.save(update_fields=['account'])
            for key, value in [('min_withdraw_amount','100'), ('withdraw_balance_threshold','0'), ('withdraw_tax_rate','5')]:
                SystemConfig.objects.update_or_create(key=key, defaults={'value':value})
            self.ptoken = issue_account_tokens(self.account)[0]
            self.stoken = issue_account_tokens(self.staff)[0]
        self.p = APIClient(HTTP_HOST='xa.esports.club'); self.p.credentials(HTTP_AUTHORIZATION='Bearer ' + self.ptoken)
        self.s = APIClient(HTTP_HOST='admin.esports.club'); self.s.credentials(HTTP_AUTHORIZATION='Bearer ' + self.stoken)
        self.f = APIClient(HTTP_HOST='admin.esports.club'); self.f.credentials(HTTP_AUTHORIZATION='Bearer ' + self.finance_token)

    def apply(self, key='withdraw-one'):
        result = self.p.post('/api/wallet/withdraw/', {'amount':20000, 'payee_method':'WECHAT',
            'payee_name':'测试收款人', 'payee_account':'qa-only', 'client_request_id':key}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        return result.data['data']['request']['id']

    def initial(self, pk):
        claim = self.s.post(f'/api/admin/withdrawals/{pk}/claim/').data
        self.assertEqual(claim['code'], 0, claim)
        result = self.s.post(f'/api/admin/withdrawals/{pk}/approve/',
            {'expected_claimed_at':claim['data']['claimed_at']}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)

    def action(self, pk, action, **data):
        return self.f.post(f'/api/admin/platform/withdrawals/xinan/{pk}/{action}', data, format='json')

    def test_store_initial_and_platform_final_do_not_pay_until_recorded(self):
        pk = self.apply(); self.initial(pk)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status),
                             ('STORE_APPROVED', 80000, 20000, 'PENDING'))
        self.assertEqual(self.action(pk, 'approve', client_request_id='platform-approve').data['code'], 0)
        with store_context(self.second):
            row.refresh_from_db(); wallet.refresh_from_db()
            self.assertEqual((row.status, wallet.frozen_amount, row.transaction.status), ('PLATFORM_APPROVED', 20000, 'PENDING'))
        payload = {'client_request_id':'payment-one', 'payout_reference':'QA-BATCH-1', 'paid_at':timezone.now().isoformat()}
        result = self.action(pk, 'paid', **payload)
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertTrue(self.action(pk, 'paid', **payload).data['data']['replayed'])
        with store_context(self.second):
            row.refresh_from_db(); wallet.refresh_from_db(); row.transaction.refresh_from_db()
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status), ('PAID', 80000, 0, 'SUCCESS'))
            self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=1000).count(), 1)
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal=row).count(), 2)
        history = self.f.get('/api/admin/platform/withdrawals/xinan', {'scope':'history'}).data['data']
        self.assertEqual(history['results'][0]['payout_reference'], 'QA-BATCH-1')
        self.assertEqual(self.f.get('/api/admin/platform/withdrawals/shenglang').status_code, 404)

    def test_control_failure_returns_retryable_503_without_changing_frozen_withdrawal(self):
        pk = self.apply(); self.initial(pk)
        def fail_control(_execute, _sql, _params, _many, _context):
            raise DatabaseError('simulated control outage')
        with connections['default'].execute_wrapper(fail_control):
            down = self.action(pk, 'approve', client_request_id='offline-approve')
        self.assertEqual(down.status_code, 503, down.data)
        self.assertIn('平台身份服务暂不可用', str(down.data))
        with patch('stores.platform_withdrawals._authorized_store', side_effect=DatabaseError('control outage')):
            initial = self.action(pk, 'approve', client_request_id='offline-approve')
        self.assertEqual(initial.status_code, 503, initial.data)
        with patch('stores.platform_withdrawals.account_for_token', side_effect=DatabaseError('control outage')):
            recheck = self.action(pk, 'approve', client_request_id='offline-approve')
        self.assertEqual(recheck.status_code, 503, recheck.data)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk)
            wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status),
                             ('STORE_APPROVED', 80000, 20000, 'PENDING'))
            self.assertFalse(WithdrawalPlatformEvent.objects.filter(withdrawal=row).exists())
        restored = self.action(pk, 'approve', client_request_id='offline-approve')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(restored.data['code'], 0, restored.data)

    def test_committed_event_audit_is_recovered_by_original_request_retry(self):
        from stores.models import PlatformAuditEvent, PlatformWithdrawalAuditReceipt
        pk = self.apply(); self.initial(pk)
        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)
        with connections['default'].execute_wrapper(fail_audit_insert):
            with self.captureOnCommitCallbacks(using='store_b', execute=True):
                first = self.action(pk, 'approve', client_request_id='recover-approve')
        self.assertEqual(first.data['code'], 0, first.data)
        self.assertEqual(first.data['data']['audit_status'], 'PENDING')
        with store_context(self.second):
            event = WithdrawalPlatformEvent.objects.get(withdrawal_id=pk, action='approve')
            wallet = get_wallet(account=self.account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 20000))
        self.assertFalse(PlatformWithdrawalAuditReceipt.objects.filter(store=self.second, tenant_event_id=event.pk).exists())
        listing = self.f.get(f'/api/admin/platform/withdrawals/xinan/{pk}/audit-recover')
        self.assertEqual(listing.data['data']['events'][0]['audit_status'], 'MISSING')
        with self.captureOnCommitCallbacks(using='default', execute=True), self.captureOnCommitCallbacks(using='store_b', execute=True):
            repeat = self.action(pk, 'approve', client_request_id='recover-approve')
        self.assertTrue(repeat.data['data']['replayed'])
        self.assertEqual(repeat.data['data']['audit_status'], 'RECORDED')
        receipt = PlatformWithdrawalAuditReceipt.objects.get(store=self.second, tenant_event_id=event.pk)
        self.assertEqual((receipt.audit.account_id, receipt.audit.action, receipt.audit.detail['request_id']),
                         (self.finance.pk, 'withdraw.approve', 'recover-approve'))
        self.assertEqual(PlatformAuditEvent.objects.filter(action='withdraw.approve', target_id=str(pk)).count(), 1)
        with store_context(self.second):
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk).count(), 1)
            wallet.refresh_from_db(); self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 20000))

    def test_paid_event_audit_recovery_preserves_actor_and_never_repays(self):
        from stores.models import PlatformAuditEvent, PlatformWithdrawalAuditReceipt
        pk = self.apply(); self.initial(pk)
        with self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(self.action(pk, 'approve', client_request_id='paid-audit-approve').data['code'], 0)
        with self.captureOnCommitCallbacks(using='store_b', execute=True):
            self.assertEqual(self.action(pk, 'mark-unknown', client_request_id='paid-audit-unknown', reason='等待核对').data['code'], 0)
        payload = {'client_request_id': 'paid-audit-paid', 'payout_reference': 'synthetic-receipt', 'paid_at': timezone.now().isoformat()}
        def fail_audit_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('INSERT') and 'stores_platformauditevent' in sql.lower():
                raise DatabaseError('simulated control audit write outage')
            return execute(sql, params, many, context)
        with connections['default'].execute_wrapper(fail_audit_insert):
            with self.captureOnCommitCallbacks(using='store_b', execute=True):
                first = self.action(pk, 'paid', **payload)
        self.assertEqual(first.data['code'], 0, first.data)
        with store_context(self.second):
            event = WithdrawalPlatformEvent.objects.get(withdrawal_id=pk, action='paid')
            wallet = get_wallet(account=self.account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 0))
            self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=1000).count(), 1)
        other = PlatformAccount(username='audit-recovery-finance', phone='13800002222', is_phone_verified=True)
        other.set_password('synthetic-password'); other.save()
        PlatformRoleGrant.objects.create(account=other, role='FINANCE', scope_slug='xinan')
        token, _ = issue_tokens(other, 'SMS', other.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        url = f'/api/admin/platform/withdrawals/xinan/{pk}/audit-recover'
        self.assertEqual(client.post(url, {'event_id': event.pk, 'client_request_id': 'wrong'}, format='json').status_code, 409)
        with self.captureOnCommitCallbacks(using='default', execute=True):
            recovered = client.post(url, {'event_id': event.pk, 'client_request_id': payload['client_request_id']}, format='json')
        self.assertEqual(recovered.status_code, 200, recovered.data)
        self.assertTrue(recovered.data['data']['recovered'])
        with self.captureOnCommitCallbacks(using='default', execute=True):
            repeat = client.post(url, {'event_id': event.pk, 'client_request_id': payload['client_request_id']}, format='json')
        self.assertFalse(repeat.data['data']['recovered'])
        audit = PlatformWithdrawalAuditReceipt.objects.get(store=self.second, tenant_event_id=event.pk).audit
        self.assertEqual((audit.account_id, audit.detail['recovered_by']), (self.finance.pk, str(other.pk)))
        self.assertEqual(PlatformAuditEvent.objects.filter(action='withdraw.paid', target_id=str(pk)).count(), 1)
        with store_context(self.second):
            wallet.refresh_from_db()
            self.assertEqual((wallet.balance, wallet.frozen_amount), (80000, 0))
            self.assertEqual(Transaction.objects.filter(tx_type='WITHDRAW_TAX', amount=1000).count(), 1)
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal_id=pk, action='paid').count(), 1)

    def test_owner_can_cancel_after_claim_but_before_store_review(self):
        pk = self.apply()
        self.assertEqual(self.s.post(f'/api/admin/withdrawals/{pk}/claim/').data['code'], 0)
        result = self.p.post(f'/api/wallet/withdraw/{pk}/cancel/', {'client_request_id':'cancel-1'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount, row.transaction.status), ('CANCELLED', 100000, 0, 'FAILED'))
        self.assertEqual(self.s.post(f'/api/admin/withdrawals/{pk}/approve/', {}).status_code, 409)

    def test_unknown_payment_cannot_be_rejected_without_unpaid_confirmation(self):
        pk = self.apply(); self.initial(pk)
        self.action(pk, 'approve', client_request_id='approve')
        self.assertEqual(self.action(pk, 'mark-unknown', client_request_id='unknown', reason='等待核实').data['code'], 0)
        self.assertEqual(self.action(pk, 'reject', client_request_id='reject1', reason='核查').status_code, 409)
        with store_context(self.second):
            self.assertEqual(get_wallet(account=self.account).frozen_amount, 20000)
        result = self.action(pk, 'reject', client_request_id='reject2', reason='确认未付', confirmed_unpaid=True)
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            wallet = get_wallet(account=self.account)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (100000, 0))

    def test_old_pending_record_cannot_be_mistaken_for_unpaid(self):
        pk = self.apply()
        with store_context(self.second):
            WithdrawRequest.objects.filter(pk=pk).update(workflow_version=1)
        self.assertEqual(self.s.post(f'/api/admin/withdrawals/{pk}/claim/').status_code, 409)
        self.assertEqual(self.p.post(f'/api/wallet/withdraw/{pk}/cancel/', {'client_request_id':'legacy'}, format='json').data['code'], 409)
        with store_context(self.second):
            self.assertEqual(get_wallet(account=self.account).frozen_amount, 20000)

    def test_platform_can_inspect_legacy_facts_without_guessing_payment_or_changing_funds(self):
        pk = self.apply()
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk)
            row.workflow_version = 1
            row.payout_reference = 'HISTORICAL-EVIDENCE'
            row.save(update_fields=['workflow_version', 'payout_reference'])
            wallet = get_wallet(account=self.account)
            before = (wallet.balance, wallet.frozen_amount, row.status, row.transaction.status)
        summary = self.f.get('/api/admin/platform/withdrawals')
        self.assertEqual(summary['Cache-Control'], 'no-store')
        self.assertEqual([(item['slug'], item['legacy_pending']) for item in summary.data['data']['stores']], [('xinan', 1)])
        result = self.f.get('/api/admin/platform/withdrawals/xinan', {'scope': 'legacy'})
        self.assertEqual(result['Cache-Control'], 'no-store')
        record = result.data['data']['results'][0]
        self.assertEqual((record['id'], record['status'], record['payout_reference']), (pk, 'PENDING', 'HISTORICAL-EVIDENCE'))
        self.assertEqual((record['legacy_ledger']['transaction_status'], record['legacy_ledger']['transaction_amount'],
                          record['legacy_ledger']['wallet_frozen_total'], record['legacy_ledger']['owner_match']),
                         ('PENDING', -20000, 20000, True))
        self.assertEqual(self.f.get('/api/admin/platform/withdrawals/shenglang', {'scope':'legacy'}).status_code, 404)
        with store_context(self.second):
            row.refresh_from_db(); wallet.refresh_from_db(); row.transaction.refresh_from_db()
            self.assertEqual((wallet.balance, wallet.frozen_amount, row.status, row.transaction.status), before)
            self.assertFalse(WithdrawalPlatformEvent.objects.filter(withdrawal=row).exists())

    def test_legacy_missing_transaction_is_reported_without_fabrication(self):
        pk = self.apply()
        with store_context(self.second):
            WithdrawRequest.objects.filter(pk=pk).update(workflow_version=1, transaction=None)
        result = self.f.get('/api/admin/platform/withdrawals/xinan', {'scope':'legacy'}).data['data']['results'][0]
        self.assertEqual(result['legacy_ledger'], {'transaction_present': False})
        with store_context(self.second):
            self.assertEqual(get_wallet(account=self.account).frozen_amount, 20000)

    def test_review_and_payment_notices_follow_real_transitions_without_duplicates(self):
        pk = self.apply(); self.initial(pk)
        self.assertEqual(self.action(pk, 'approve', client_request_id='notices-approve').data['code'], 0)
        self.assertEqual(self.action(pk, 'mark-unknown', client_request_id='notices-unknown',
                                     reason='财务内部核实').data['code'], 0)
        payload = {'client_request_id':'notices-paid', 'payout_reference':'QA-NOTICE', 'paid_at':timezone.now().isoformat()}
        self.assertEqual(self.action(pk, 'paid', **payload).data['code'], 0)
        self.assertTrue(self.action(pk, 'paid', **payload).data['data']['replayed'])
        with store_context(self.second):
            messages = list(Message.objects.filter(recipient_account=self.account).order_by('id'))
            self.assertEqual([msg.title for msg in messages], ['提现门店初审通过', '提现平台终审通过',
                             '提现打款待核实', '提现已登记打款'])
            self.assertTrue(all(msg.action_url == f'/pages/wallet/index?withdraw_id={pk}' for msg in messages))
            self.assertNotIn('财务内部核实', messages[2].detail)
        with store_context(self.first):
            self.assertFalse(Message.objects.filter(title='提现已登记打款').exists())
        self.assertEqual(self.p.get('/api/messages/').data['data']['total'], 4)

    def test_message_failure_does_not_change_committed_payment_outcome(self):
        pk = self.apply(); self.initial(pk)
        self.action(pk, 'approve', client_request_id='notice-failure-approve')
        with patch('site_messages.models.Message.objects.create', side_effect=DatabaseError('isolated failure')):
            with self.assertLogs('wallet.withdraw_notifications', level='ERROR'):
                result = self.action(pk, 'paid', client_request_id='notice-failure-paid',
                                     payout_reference='QA-FAIL-NOTICE', paid_at=timezone.now().isoformat())
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, row.transaction.status, wallet.balance, wallet.frozen_amount),
                             ('PAID', 'SUCCESS', 80000, 0))

    def test_finance_rejection_and_cancellation_notify_after_unpaid_confirmation(self):
        for action, title in [('reject', '提现审核未通过'), ('cancel', '提现已取消')]:
            with self.subTest(action=action):
                pk = self.apply('notice-' + action); self.initial(pk)
                self.assertEqual(self.action(pk, action, client_request_id='notice-' + action,
                    reason='收款信息需核对', confirmed_unpaid=True).data['code'], 0)
                with store_context(self.second):
                    message = Message.objects.get(recipient_account=self.account, title=title,
                        action_url=f'/pages/wallet/index?withdraw_id={pk}')
                    self.assertIn('收款信息需核对', message.detail)
                    wallet = get_wallet(account=self.account)
                    self.assertEqual((wallet.balance, wallet.frozen_amount), (100000, 0))

    def test_rollback_removes_new_notice_and_leaves_funds_and_state(self):
        pk = self.apply(); self.initial(pk)
        with store_context(self.second):
            before = Message.objects.filter(recipient_account=self.account).count()
            with self.assertRaisesRegex(RuntimeError, 'rollback fixture'):
                with transaction.atomic():
                    self.assertEqual(self.action(pk, 'approve', client_request_id='notice-rollback').data['code'], 0)
                    raise RuntimeError('rollback fixture')
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.balance, wallet.frozen_amount), ('STORE_APPROVED', 80000, 20000))
            self.assertEqual(Message.objects.filter(recipient_account=self.account).count(), before)

    def test_legacy_one_step_store_payout_request_is_rejected(self):
        pk = self.apply()
        claim = self.s.post(f'/api/admin/withdrawals/{pk}/claim/').data['data']
        result = self.s.post(f'/api/admin/withdrawals/{pk}/approve/', {
            'payout_reference':'OLD-CLIENT-PAYOUT', 'expected_claimed_at':claim['claimed_at'],
        }, format='json')
        self.assertEqual(result.data['code'], 400)
        with store_context(self.second):
            row = WithdrawRequest.objects.get(pk=pk); wallet = get_wallet(account=self.account)
            self.assertEqual((row.status, wallet.frozen_amount, row.transaction.status), ('PENDING', 20000, 'PENDING'))

    def test_reused_batch_reference_requires_explicit_acknowledgement(self):
        for suffix in ('one', 'two'):
            pk = self.apply('request-' + suffix); self.initial(pk)
            self.assertEqual(self.action(pk, 'approve', client_request_id='approve-' + suffix).data['code'], 0)
            payload = {'client_request_id':'paid-' + suffix, 'payout_reference':'QA-SHARED-BATCH',
                       'paid_at':timezone.now().isoformat()}
            result = self.action(pk, 'paid', **payload)
            if suffix == 'two':
                self.assertEqual(result.data['errorCode'], 'DUPLICATE_PAYOUT_REFERENCE')
                with store_context(self.second):
                    self.assertEqual(WithdrawRequest.objects.get(pk=pk).status, 'PLATFORM_APPROVED')
                result = self.action(pk, 'paid', **payload, acknowledge_duplicate_reference=True)
            self.assertEqual(result.data['code'], 0, result.data)
