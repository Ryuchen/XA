"""One-shot MySQL reply loss on explicitly registered test connections."""
from select import select
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
from ssl import SSLSocket
from threading import Thread
from time import time
from unittest import skipUnless
from unittest.mock import patch

from celery.contrib.testing.worker import start_worker
from django.conf import settings
from django.db import OperationalError, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from pymysql.constants import COMMAND
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount, OtpCredential
from club_accounts.otp import encrypt_secret, hotp
from club_accounts.services import get_or_create_account_for_legacy_user
from common.mysql_test_support import require_disposable_mysql
from orders.tests.factories import make_console_user
from site_messages.models import Message
from stores.context import store_context
from stores.models import (PlatformAccount, PlatformAuditEvent, PlatformOtpCredential, PlatformRoleGrant,
                           PlatformWithdrawalAuditDispatchCursor, PlatformWithdrawalAuditReceipt,
                           PlatformWithdrawalAuditScanCursor, Store)
from stores.test_certificate_admission_mysql import _store_task_app
from stores.withdrawal_audit_tasks import dispatch, scan_store
from wallet.models import IdempotencyKey, SystemConfig, Transaction, WithdrawalPlatformEvent, WithdrawalRelease, WithdrawRequest
from wallet.services import get_wallet


def _receive_exact(source, size):
    data = bytearray()
    while len(data) < size:
        part = source.recv(size - len(data))
        if not part:
            raise ConnectionError('Test MySQL closed mid-packet')
        data.extend(part)
    return bytes(data)


def _packet(source):
    header = _receive_exact(source, 4)
    length = int.from_bytes(header[:3], 'little')
    if not 1 <= length <= 1024:
        raise ValueError('Expected a short commit command or reply')
    return header, _receive_exact(source, length)


def _tls_record(source):
    header = _receive_exact(source, 5)
    length = int.from_bytes(header[3:], 'big')
    if header[:3] != b'\x17\x03\x03' or not 1 <= length <= 1024:
        raise ValueError('Expected a short TLS record on the armed connection')
    return header + _receive_exact(source, length)


class _CommitRelay(BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(self.server.upstream, timeout=3) as upstream:
                self.request.settimeout(3)
                while True:
                    readable, _, _ = select((self.request, upstream), (), (), 3)
                    for current in readable:
                        if current is self.request and self.client_address == self.server.armed_peer:
                            self.server.armed_peer = None
                            self.cut_commit(upstream)
                            return
                        data = current.recv(65536)
                        if not data:
                            return
                        peer = upstream if current is self.request else self.request
                        peer.sendall(data)
        except (OSError, ValueError) as cause:
            if self.client_address == self.server.target_peer:
                self.server.failure = type(cause).__name__

    def cut_commit(self, upstream):
        if self.server.encrypted:
            upstream.sendall(_tls_record(self.request))
            _tls_record(upstream)
            self.server.observed.append('TLS_SERVER_RECORD_WITHHELD')
            return
        header, command = _packet(self.request)
        if header[3] != 0 or command != bytes([COMMAND.COM_QUERY]) + b'COMMIT':
            raise ValueError('Armed packet was not the native COMMIT command')
        self.server.observed.append('COMMIT')
        if self.server.after_commit:
            upstream.sendall(header + command)
            header, reply = _packet(upstream)
            if header[3] != 1 or len(reply) < 7 or reply[0] != 0:
                raise ValueError('Test MySQL did not acknowledge COMMIT success')
            self.server.observed.append('OK_WITHHELD')
        else:
            self.server.observed.append('COMMIT_NOT_SENT')


@skipUnless(connections['default'].vendor == 'mysql' and {'store_a', 'store_b'}.issubset(connections),
            'Requires the registered control and two shop MySQL test schemas')
@override_settings(PLATFORM_IDENTITY_ENABLED=True, PROVIDER_LIFECYCLE_INTEGRATION_ENABLED=True,
                   WITHDRAWAL_NOTICE_RECOVERY_ENABLED=True)
class WithdrawalCommitWireMySQLTest(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}
    password = 'isolated-withdrawal-wire-password'
    otp_secret = 'JBSWY3DPEHPK3PXP'
    path = '/api/wallet/withdraw/'

    def setUp(self):
        for alias in self.databases:
            connection = connections[alias]
            require_disposable_mysql(connection)
            self.assertEqual(connection.settings_dict['HOST'], '127.0.0.1')
            self.assertFalse(connection.settings_dict['ATOMIC_REQUESTS'])
            with connection.cursor() as cursor:
                cursor.execute('SELECT DATABASE()')
                self.assertEqual(cursor.fetchone()[0], connection.settings_dict['NAME'])
        self.stores, self.owners, self.clients = {}, {}, {}
        for slug, alias, host, name, prefix in (
            ('shenglang', 'store_a', 'sl.esports.club', '声浪电竞', 'SL'),
            ('xinan', 'store_b', 'xa.esports.club', '兴安电竞', 'XA'),
        ):
            store, _ = Store.objects.update_or_create(slug=slug, defaults={'database_alias': alias, 'name': name,
                'entity_prefix': prefix, 'status': Store.Status.ACTIVE, 'initialized_at': timezone.now()})
            with store_context(store):
                owner = ClubAccount.objects.create_account(pk=6600220, username='wire-provider',
                    account_type='PROVIDER', password=self.password)
                self.bind_otp(owner)
                wallet = get_wallet(account=owner)
                wallet.balance = 100000
                wallet.save(update_fields=['balance'])
                for key, value in (('min_withdraw_amount', '100'), ('withdraw_balance_threshold', '0'), ('withdraw_tax_rate', '5')):
                    SystemConfig.objects.update_or_create(key=key, defaults={'value': value})
            self.stores[slug], self.owners[slug] = store, owner
            self.clients[slug] = self.sign_in(host, '/api/users/password-login/', owner.username, audience='provider')

        with store_context(self.stores['xinan']):
            staff = make_console_user(['withdraw:view', 'withdraw:audit'])
            account = get_or_create_account_for_legacy_user(staff)
            account.set_password(self.password)
            account.save(update_fields=['password'])
            staff.admin_membership.account = account
            staff.admin_membership.save(update_fields=['account'])
            self.bind_otp(account)
        self.staff = self.sign_in('admin.esports.club', '/api/admin/auth/password-login', f'xinan/{account.username}')
        self.finance, self.financial = self.sign_in_finance('wire-platform-finance')

    def bind_otp(self, account):
        OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(self.otp_secret))

    def sign_in_finance(self, username):
        account = PlatformAccount(username=username)
        account.set_password(self.password)
        account.save()
        PlatformRoleGrant.objects.create(account=account, role='FINANCE', scope_slug='xinan')
        PlatformOtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(self.otp_secret))
        return account, self.sign_in('admin.esports.club', '/api/admin/auth/password-login', account.username)

    def sign_in(self, host, path, username, **hints):
        client = APIClient(HTTP_HOST=host)
        credentials = {'username': username, 'password': self.password, **hints}
        challenge = client.post(path, credentials, format='json')
        self.assertEqual(challenge.status_code, 200)
        offered = challenge.data['data']
        self.assertTrue(offered['requiresOTP'])
        if offered.get('identity_kind'):
            credentials['identityKind'] = offered['identity_kind']
        response = client.post(path, {**credentials, 'otpChallenge': offered['otpChallenge'],
            'otpCode': hotp(self.otp_secret, int(time()) // 30)}, format='json')
        self.assertEqual(response.data['code'], 0, response.data.get('errorCode'))
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['data']['token'])
        return client

    def interrupt_commit(self, operation, *, after_commit, write_model=IdempotencyKey, encrypted=False):
        self.assertTrue(after_commit or not encrypted, 'The command-before-server control uses the plain test connection')
        cursor_writes = (PlatformWithdrawalAuditDispatchCursor, PlatformWithdrawalAuditScanCursor)
        control_writes = (PlatformWithdrawalAuditReceipt, *cursor_writes)
        self.assertIn(write_model, (IdempotencyKey, WithdrawalPlatformEvent, *control_writes))
        alias = 'default' if write_model in control_writes else 'store_b'
        connection = connections[alias]
        require_disposable_mysql(connection)
        self.assertTrue(connection.get_autocommit())
        port = connection.settings_dict['PORT']
        options = connection.settings_dict['OPTIONS']
        original_commit = connection._commit
        client_errors = []
        commit_ready = False
        table = write_model._meta.db_table
        verb = 'UPDATE' if write_model in (IdempotencyKey, *cursor_writes) else 'INSERT INTO'
        with ThreadingTCPServer(('127.0.0.1', 0), _CommitRelay) as relay:
            relay.daemon_threads = True
            relay.upstream = ('127.0.0.1', int(port))
            relay.target_peer = relay.armed_peer = relay.failure = None
            relay.after_commit = after_commit
            relay.encrypted = encrypted
            relay.observed = []
            thread = Thread(target=relay.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
            thread.start()
            connection.close()
            connection.settings_dict['PORT'] = str(relay.server_address[1])
            connection.settings_dict['OPTIONS'] = {**options, 'ssl_disabled': not encrypted}

            def observe_target_write(execute, sql, params, many, context):
                nonlocal commit_ready
                result = execute(sql, params, many, context)
                if sql.lstrip().upper().startswith(f'{verb} ') and f'`{table}`' in sql:
                    self.assertTrue(connection.in_atomic_block)
                    commit_ready = True
                return result

            def arm_native_commit():
                nonlocal commit_ready
                if not commit_ready:
                    return original_commit()
                commit_ready = False
                self.assertIsNone(relay.target_peer, 'Expected a single outer COMMIT for the target write')
                raw = connection.connection._sock
                self.assertEqual(isinstance(raw, SSLSocket), encrypted)
                self.assertEqual(raw.getpeername(), relay.server_address)
                relay.target_peer = relay.armed_peer = raw.getsockname()
                try:
                    return original_commit()
                except OperationalError as cause:
                    client_errors.append(cause.args[0])
                    raise

            try:
                with connection.execute_wrapper(observe_target_write), patch.object(connection, '_commit', side_effect=arm_native_commit):
                    response = operation()
            finally:
                try:
                    connection.close()
                finally:
                    connection.settings_dict['PORT'] = port
                    connection.settings_dict['OPTIONS'] = options
                    relay.shutdown()
                    thread.join(timeout=3)
            self.assertIsNone(relay.failure)
            expected = ['TLS_SERVER_RECORD_WITHHELD'] if encrypted else ['COMMIT', 'OK_WITHHELD' if after_commit else 'COMMIT_NOT_SENT']
            self.assertEqual(relay.observed, expected)
        self.assertEqual(client_errors, [2013])
        if write_model in cursor_writes:
            self.assertEqual(response['status'], 'CONTROL_UNAVAILABLE')
            outcome = 'task=CONTROL_UNAVAILABLE'
        else:
            self.assertEqual(response.status_code, 503, response.data)
            self.assertEqual(response.data['errorCode'], 'AUDIT_UNAVAILABLE' if alias == 'default' else 'WITHDRAWAL_UNAVAILABLE')
            self.assertNotIn('fundResult', response.data)
            self.assertNotIn('data', response.data)
            outcome = 'http=503'
        print(f'wire cut {" ".join(relay.observed)} driver=2013 {outcome} alias={alias}', flush=True)
        return response

    def submit(self, slug, request_id, amount):
        return self.clients[slug].post(self.path, {'client_request_id': request_id, 'amount': amount,
            'payee_method': 'WECHAT', 'payee_account': f'isolated-{slug}', 'payee_name': '隔离收款人'}, format='json')

    def reviewed_application(self, request_id):
        response = self.submit('xinan', request_id, 20000)
        self.assertEqual(response.data['code'], 0, response.data)
        row_id = response.data['data']['request']['id']
        claim = self.staff.post(f'/api/admin/withdrawals/{row_id}/claim/').data
        self.assertEqual(claim['code'], 0, claim)
        reviewed = self.staff.post(f'/api/admin/withdrawals/{row_id}/approve/',
            {'expected_claimed_at': claim['data']['claimed_at']}, format='json')
        self.assertEqual(reviewed.data['code'], 0, reviewed.data)
        return row_id

    def facts(self, slug, request_status, ledger_status, balance, frozen):
        with store_context(self.stores[slug]):
            owner = self.owners[slug]
            row = WithdrawRequest.objects.select_related('transaction__wallet').get(account=owner)
            wallet = row.transaction.wallet
            self.assertEqual((row.status, row.transaction.status, wallet.balance, wallet.frozen_amount),
                (request_status, ledger_status, balance, frozen))
            self.assertEqual((row.transaction.tx_type, row.transaction.amount), ('WITHDRAW', -row.amount))
            self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='WITHDRAW').count(), 1)
            self.assertFalse(Transaction.objects.filter(wallet=wallet, tx_type='WITHDRAW_TAX').exists())
            self.assertFalse(row.paid_at or row.payout_reference)
            return row

    def peer_application(self, request_id):
        response = self.submit('shenglang', request_id, 21000)
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data['data']['request']['id']

    def assert_peer(self, application, request_id):
        row = self.facts('shenglang', 'PENDING', 'PENDING', 79000, 21000)
        self.assertEqual(row.pk, application)
        with store_context(self.stores['shenglang']):
            self.assertEqual(IdempotencyKey.objects.get(account=self.owners['shenglang'], scope='withdraw').request_id, request_id)
            self.assertFalse(WithdrawalRelease.objects.filter(withdrawal=row).exists())
            self.assertFalse(WithdrawalPlatformEvent.objects.filter(withdrawal=row).exists())

    def assert_release(self, row):
        with store_context(self.stores['xinan']):
            release = WithdrawalRelease.objects.get(withdrawal=row)
            self.assertEqual((release.amount, release.original_transaction_id, release.balance_before, release.balance_after,
                release.frozen_before, release.frozen_after), (20000, row.transaction_id, 80000, 100000, 20000, 0))
        return release.pk

    def commit_unrecorded_audit_events(self, row_id, request_prefix):
        endpoint = f'/api/admin/platform/withdrawals/xinan/{row_id}'
        for action, fields in (('approve', {}), ('mark-unknown', {'reason': '隔离核对原付款结果'})):
            payload = {'client_request_id': f'{request_prefix}-{action}', **fields}
            self.interrupt_commit(lambda: self.financial.post(f'{endpoint}/{action}', payload, format='json'),
                                  after_commit=True, write_model=WithdrawalPlatformEvent)

    def test_owner_application_and_cancellation_recover_withheld_commit_acknowledgements(self):
        self.check_owner_recovery(encrypted=False)

    def test_owner_application_and_cancellation_recover_on_encrypted_connection(self):
        self.check_owner_recovery(encrypted=True)

    def check_owner_recovery(self, *, encrypted):
        apply_id = 'wire-owner-application'
        peer_id = self.peer_application(apply_id)
        self.interrupt_commit(lambda: self.submit('xinan', apply_id, 20000), after_commit=True, encrypted=encrypted)
        row = self.facts('xinan', 'PENDING', 'PENDING', 80000, 20000)
        replay = self.submit('xinan', apply_id, 20000).data
        self.assertEqual(replay['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(replay['fundResult']['data']['request']['id'], row.pk)
        self.assertEqual((replay['fundResult']['data']['balance'], replay['fundResult']['data']['frozen_amount']),
                         (80000, 20000))
        self.assertEqual(self.submit('xinan', apply_id, 19000).data['errorCode'], 'REQUEST_ID_CONFLICT')
        cancel_id = 'wire-owner-cancellation'

        def cancel():
            return self.clients['xinan'].post(f'{self.path}{row.pk}/cancel/', {'client_request_id': cancel_id}, format='json')

        self.interrupt_commit(cancel, after_commit=True, encrypted=encrypted)
        cancelled = self.facts('xinan', 'CANCELLED', 'FAILED', 100000, 0)
        release_id = self.assert_release(cancelled)
        for _ in range(2):
            recovered = cancel().data
            self.assertEqual(recovered['errorCode'], 'FUND_ALREADY_PROCESSED')
            self.assertEqual(recovered['fundResult']['data'], {'id': row.pk, 'status': 'CANCELLED', 'balance': 100000})
        self.assertEqual(self.assert_release(cancelled), release_id)
        with store_context(self.stores['xinan']):
            self.assertCountEqual(IdempotencyKey.objects.filter(account=self.owners['xinan']).values_list('request_id', flat=True),
                [apply_id, cancel_id])
            self.assertCountEqual(Message.objects.filter(recipient_account=self.owners['xinan']).values_list('title', flat=True),
                ['提现申请已提交', '提现申请已取消'])
        self.assert_peer(peer_id, apply_id)
        print(f'owner wire recovery store=xinan account=6600220 withdrawal={row.pk} tx={row.transaction_id}/FAILED '
              f'apply={apply_id} cancel={cancel_id} release={release_id} balance=100000 frozen=0 peer={peer_id}', flush=True)

    def test_commit_command_cut_before_mysql_leaves_no_application_or_receipt(self):
        request_id = 'wire-owner-application-not-sent'
        self.interrupt_commit(lambda: self.submit('xinan', request_id, 20000), after_commit=False)
        owner = self.owners['xinan']
        with store_context(self.stores['xinan']):
            wallet = get_wallet(account=owner)
            self.assertEqual((wallet.balance, wallet.frozen_amount), (100000, 0))
            self.assertFalse(WithdrawRequest.objects.filter(account=owner).exists())
            self.assertFalse(IdempotencyKey.objects.filter(account=owner, request_id=request_id).exists())
            self.assertFalse(Transaction.objects.filter(wallet=wallet).exists())
            self.assertFalse(Message.objects.filter(recipient_account=owner).exists())
        self.assertEqual(self.submit('xinan', request_id, 20000).data['code'], 0)
        row = self.facts('xinan', 'PENDING', 'PENDING', 80000, 20000)
        print(f'owner COMMIT not forwarded request={request_id} balance-before-retry=100000 '
              f'frozen-before-retry=0 withdrawal-after-retry={row.pk} tx={row.transaction_id}/PENDING', flush=True)

    def test_platform_approval_and_unpaid_rejection_recover_withheld_commit_acknowledgements(self):
        self.check_platform_recovery(encrypted=False)

    def test_platform_approval_and_unpaid_rejection_recover_on_encrypted_connection(self):
        self.check_platform_recovery(encrypted=True)

    def check_platform_recovery(self, *, encrypted):
        apply_id = 'wire-platform-source'
        peer_id = self.peer_application(apply_id)
        row_id = self.reviewed_application(apply_id)
        path = f'/api/admin/platform/withdrawals/xinan/{row_id}'
        store = self.stores['xinan']
        for action, status, ledger, balance, frozen, fields in (
            ('approve', 'PLATFORM_APPROVED', 'PENDING', 80000, 20000, {}),
            ('reject', 'REJECTED', 'FAILED', 100000, 0, {'reason': '隔离渠道确认未付款', 'confirmed_unpaid': True}),
        ):
            with self.subTest(action=action):
                request_id = f'wire-platform-{action}'
                payload = {'client_request_id': request_id, **fields}
                self.interrupt_commit(lambda: self.financial.post(f'{path}/{action}', payload, format='json'),
                                      after_commit=True, write_model=WithdrawalPlatformEvent, encrypted=encrypted)
                row = self.facts('xinan', status, ledger, balance, frozen)
                with store_context(store):
                    event = WithdrawalPlatformEvent.objects.get(withdrawal=row, request_id=request_id)
                    self.assertEqual((event.platform_actor_id, event.action, event.to_status), (self.finance.pk, action, status))
                    if action == 'approve':
                        self.assertFalse(WithdrawalRelease.objects.filter(withdrawal=row).exists())
                release_id = self.assert_release(row) if action == 'reject' else None
                self.assertFalse(PlatformWithdrawalAuditReceipt.objects.filter(store=store, tenant_event_id=event.pk).exists())
                audits = PlatformAuditEvent.objects.filter(target_store_slug='xinan', target_id=str(row_id), action=f'withdraw.{action}')
                self.assertFalse(audits.exists())
                lookup = self.financial.get(f'{path}/audit-recover')
                events = {row['request_id']: row for row in lookup.data['data']['events']}
                self.assertEqual(events[request_id]['audit_status'], 'MISSING')
                conflict = self.financial.post(f'{path}/{action}', {**payload, 'reason': '已变更的隔离测试内容'}, format='json')
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.data['errorCode'], 'REQUEST_ID_CONFLICT')
                for _ in range(2):
                    replay = self.financial.post(f'{path}/{action}', payload, format='json')
                    self.assertEqual(replay.status_code, 200, replay.data)
                    self.assertEqual(replay.data['data'], {'id': row.pk, 'status': status, 'replayed': True, 'audit_status': 'RECORDED'})
                audit = PlatformWithdrawalAuditReceipt.objects.get(store=store, tenant_event_id=event.pk)
                self.assertEqual(audits.get().pk, audit.audit_id)
                self.facts('xinan', status, ledger, balance, frozen)
                if release_id:
                    self.assertEqual(self.assert_release(row), release_id)
                print(f'platform wire recovery store=xinan account=6600220 action={action} request={request_id} '
                      f'withdrawal={row.pk} tx={row.transaction_id}/{ledger} event={event.pk} audit={audit.audit_id} '
                      f'balance={balance} frozen={frozen} release={release_id}', flush=True)
        self.assert_peer(peer_id, apply_id)
        self.assertEqual(self.financial.get(f'/api/admin/platform/withdrawals/shenglang/{peer_id}/audit-recover').status_code, 404)
        with store_context(store):
            self.assertEqual(WithdrawalPlatformEvent.objects.filter(withdrawal_id=row_id).count(), 2)
            titles = Message.objects.filter(recipient_account=self.owners['xinan']).values_list('title', flat=True)
            self.assertCountEqual(titles, ['提现申请已提交', '提现门店初审通过', '提现平台终审通过', '提现审核未通过'])

    def test_manual_audit_recovers_after_plain_control_commit_confirmation_is_lost(self):
        self.check_manual_audit_recovery(after_commit=True)

    def test_manual_audit_recovers_after_encrypted_control_commit_confirmation_is_lost(self):
        self.check_manual_audit_recovery(after_commit=True, encrypted=True)

    def test_manual_audit_creates_only_after_retry_if_control_commit_was_not_sent(self):
        self.check_manual_audit_recovery(after_commit=False)

    def check_manual_audit_recovery(self, *, after_commit, encrypted=False):
        apply_id = 'wire-manual-audit-source'
        peer_id = self.peer_application(apply_id)
        row_id = self.reviewed_application(apply_id)
        endpoint = f'/api/admin/platform/withdrawals/xinan/{row_id}'
        request_id = 'wire-manual-audit-original-approval'
        self.interrupt_commit(lambda: self.financial.post(f'{endpoint}/approve', {'client_request_id': request_id}, format='json'),
                              after_commit=True, write_model=WithdrawalPlatformEvent)
        store = self.stores['xinan']
        row = self.facts('xinan', 'PLATFORM_APPROVED', 'PENDING', 80000, 20000)
        with store_context(store):
            event = WithdrawalPlatformEvent.objects.get(withdrawal=row, request_id=request_id)
            notice_ids = list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('id', flat=True))
            self.assertEqual(len(notice_ids), 3)
        reviewer, recovery = self.sign_in_finance('wire-audit-recovery-finance')
        receipts = PlatformWithdrawalAuditReceipt.objects.filter(store=store, tenant_event_id=event.pk).select_related('audit')
        audits = PlatformAuditEvent.objects.filter(action='withdraw.approve', target_store_slug=store.slug, target_id=str(row_id))
        path = f'{endpoint}/audit-recover'
        payload = {'client_request_id': request_id, 'event_id': event.pk}

        def recover():
            return recovery.post(path, payload, format='json')

        def audit_status():
            response = recovery.get(path)
            self.assertEqual(response.status_code, 200, response.data)
            found, = response.data['data']['events']
            self.assertEqual((found['event_id'], found['request_id']), (event.pk, request_id))
            return found['audit_status']

        self.assertEqual(audit_status(), 'MISSING')
        self.assertFalse(receipts.exists() or audits.exists())
        self.interrupt_commit(recover, after_commit=after_commit, write_model=PlatformWithdrawalAuditReceipt, encrypted=encrypted)
        observed = receipts.get().audit_id if after_commit else None
        if not after_commit:
            self.assertFalse(receipts.exists() or audits.exists())
        self.assertEqual(audit_status(), 'RECORDED' if after_commit else 'MISSING')
        wrong = recovery.post(path, {**payload, 'client_request_id': 'other-wire-audit-operation'}, format='json')
        self.assertEqual(wrong.status_code, 409)
        self.assertEqual(receipts.count(), int(after_commit))
        for created in (not after_commit, False):
            reply = recover()
            receipt = receipts.get()
            self.assertEqual(reply.status_code, 200, reply.data)
            self.assertEqual(reply.data['data'], {'audit_recorded': True, 'audit_status': 'RECORDED', 'event_id': event.pk,
                'audit_id': receipt.audit_id, 'recovered': created})
            if observed is not None:
                self.assertEqual(receipt.audit_id, observed)
            observed = receipt.audit_id
        audit = audits.get()
        self.assertEqual((audit.pk, audit.account_id), (observed, self.finance.pk))
        self.assertEqual((audit.detail['event_id'], audit.detail['request_id'], audit.detail['request_fingerprint'], audit.detail['recovered_by']),
            (event.pk, request_id, event.request_fingerprint, str(reviewer.pk)))
        self.assertEqual(audit_status(), 'RECORDED')
        result = self.facts('xinan', 'PLATFORM_APPROVED', 'PENDING', 80000, 20000)
        self.assertEqual(result.transaction_id, row.transaction_id)
        with store_context(store):
            self.assertEqual(list(WithdrawalPlatformEvent.objects.filter(withdrawal=result).values_list('pk', flat=True)), [event.pk])
            self.assertFalse(WithdrawalRelease.objects.filter(withdrawal=result).exists())
            self.assertEqual(list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('id', flat=True)), notice_ids)
        self.assert_peer(peer_id, apply_id)
        peer_path = f'/api/admin/platform/withdrawals/shenglang/{peer_id}/audit-recover'
        self.assertEqual(recovery.get(peer_path).status_code, 404)
        self.assertEqual(recovery.post(peer_path, payload, format='json').status_code, 404)
        print(f'manual audit wire recovery store=xinan withdrawal={row_id} event={event.pk} request={request_id} '
              f'audit={audit.pk} original_actor={self.finance.pk} recovery_actor={reviewer.pk} committed={after_commit} '
              f'tls={encrypted} ledger={row.transaction_id}/PENDING balance=80000 frozen=20000 peer={peer_id}', flush=True)

    def test_auto_scan_resumes_after_plain_control_commit_confirmation_is_lost(self):
        self.check_automatic_scan_recovery(after_commit=True)

    def test_auto_scan_resumes_after_encrypted_control_commit_confirmation_is_lost(self):
        self.check_automatic_scan_recovery(after_commit=True, encrypted=True)

    def test_auto_scan_revisits_first_event_if_control_commit_was_not_sent(self):
        self.check_automatic_scan_recovery(after_commit=False)

    @override_settings(WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED=True)
    def check_automatic_scan_recovery(self, *, after_commit, encrypted=False):
        apply_id = 'wire-auto-scan-source'
        peer_id = self.peer_application(apply_id)
        row_id = self.reviewed_application(apply_id)
        store = self.stores['xinan']
        self.commit_unrecorded_audit_events(row_id, 'wire-auto-scan')
        row = self.facts('xinan', 'PAYMENT_UNKNOWN', 'PENDING', 80000, 20000)
        with store_context(store):
            first, second = WithdrawalPlatformEvent.objects.filter(withdrawal=row).order_by('pk')
            notice_ids = list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('pk', flat=True))
        self.assertEqual((first.action, second.action), ('approve', 'mark-unknown'))
        self.assertEqual(len(notice_ids), 4)
        cursor = PlatformWithdrawalAuditScanCursor.objects.create(store=store, last_event_id=0)
        original_updated_at = cursor.updated_at
        receipts = PlatformWithdrawalAuditReceipt.objects.filter(store=store).select_related('audit')
        audits = PlatformAuditEvent.objects.filter(target_store_slug=store.slug, target_id=str(row_id), action__startswith='withdraw.')
        self.assertFalse(receipts.exists() or audits.exists())
        registered_redis = bool(getattr(settings, 'SA14_TEST_REDIS', None))

        with _store_task_app(registered_redis=registered_redis) as (app, queue):
            app.finalize()
            task = app.tasks[scan_store.name]
            original_run = task.run
            cut_next = True

            def run_in_worker(store_id):
                nonlocal cut_next
                try:
                    if cut_next:
                        self.assertEqual(store_id, store.pk)
                        cut_next = False
                        # Django connections are local to the worker thread.
                        return self.interrupt_commit(lambda: original_run(store_id), after_commit=after_commit,
                                                     write_model=PlatformWithdrawalAuditScanCursor, encrypted=encrypted)
                    return original_run(store_id)
                finally:
                    connections.close_all()

            def check_page(expected_after, recovered, already_recorded):
                result = task.delay(store.pk).get(timeout=15, disable_sync_subtasks=False)
                self.assertEqual(result, {'status': 'OK', 'store_id': store.pk, 'scanned': 1,
                    'recovered': recovered, 'already_recorded': already_recorded, 'review': 0,
                    'review_events': [], 'next_after': expected_after})
                cursor.refresh_from_db()
                self.assertEqual(cursor.last_event_id, expected_after)

            with (patch.object(task, 'run', run_in_worker), patch('stores.withdrawal_audit_tasks.MAX_EVENTS', 1),
                  start_worker(app, pool='solo', queues=[queue], perform_ping_check=False,
                               without_heartbeat=True, loglevel='WARNING')):
                failed = task.delay(store.pk)
                self.assertEqual(failed.get(timeout=15, disable_sync_subtasks=False),
                                 {'status': 'CONTROL_UNAVAILABLE', 'store_id': store.pk})
                cursor.refresh_from_db()
                first_position = first.pk if after_commit else 0
                self.assertEqual(cursor.last_event_id, first_position)
                if after_commit:
                    initial_audit = receipts.get(tenant_event_id=first.pk).audit_id
                    self.assertEqual(list(audits.values_list('pk', flat=True)), [initial_audit])
                else:
                    self.assertEqual(cursor.updated_at, original_updated_at)
                    self.assertFalse(receipts.exists() or audits.exists())
                    check_page(first.pk, recovered=1, already_recorded=0)
                    initial_audit = receipts.get(tenant_event_id=first.pk).audit_id
                self.assertFalse(receipts.filter(tenant_event_id=second.pk).exists())
                check_page(0, recovered=1, already_recorded=0)
                check_page(first.pk, recovered=0, already_recorded=1)
                check_page(0, recovered=0, already_recorded=1)

        recorded = {receipt.tenant_event_id: receipt.audit for receipt in receipts}
        self.assertEqual(set(recorded), {first.pk, second.pk})
        self.assertEqual(recorded[first.pk].pk, initial_audit)
        self.assertEqual(audits.count(), 2)
        for event in (first, second):
            audit = recorded[event.pk]
            self.assertEqual((audit.account_id, audit.action), (self.finance.pk, f'withdraw.{event.action}'))
            self.assertEqual((audit.detail['event_id'], audit.detail['request_id'], audit.detail['request_fingerprint'], audit.detail['recovered_by']),
                (event.pk, event.request_id, event.request_fingerprint, None))
        result = self.facts('xinan', 'PAYMENT_UNKNOWN', 'PENDING', 80000, 20000)
        self.assertEqual(result.transaction_id, row.transaction_id)
        with store_context(store):
            self.assertEqual(list(WithdrawalPlatformEvent.objects.filter(withdrawal=result).order_by('id').values_list('pk', flat=True)),
                             [first.pk, second.pk])
            self.assertFalse(WithdrawalRelease.objects.filter(withdrawal=result).exists())
            self.assertEqual(list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('pk', flat=True)), notice_ids)
        self.assert_peer(peer_id, apply_id)
        self.assertFalse(PlatformWithdrawalAuditReceipt.objects.filter(store=self.stores['shenglang']).exists())
        broker = 'redis' if registered_redis else 'memory'
        print(f'auto audit wire recovery store=xinan withdrawal={row_id} events={first.pk},{second.pk} '
              f'audits={recorded[first.pk].pk},{recorded[second.pk].pk} actor={self.finance.pk} cursor={first_position}->0 '
              f'tls={encrypted} broker={broker} task={failed.id} ledger={row.transaction_id}/PENDING '
              f'balance=80000 frozen=20000 peer={peer_id}', flush=True)

    def test_dispatch_advances_after_plain_control_commit_confirmation_is_lost(self):
        self.check_dispatch_recovery(after_commit=True)

    def test_dispatch_advances_after_encrypted_control_commit_confirmation_is_lost(self):
        self.check_dispatch_recovery(after_commit=True, encrypted=True)

    def test_dispatch_resends_the_same_store_if_control_commit_was_not_sent(self):
        self.check_dispatch_recovery(after_commit=False)

    @override_settings(WITHDRAW_AUDIT_AUTO_RECOVERY_ENABLED=True)
    def check_dispatch_recovery(self, *, after_commit, encrypted=False):
        apply_id = 'wire-auto-dispatch-source'
        peer_id = self.peer_application(apply_id)
        row_id = self.reviewed_application(apply_id)
        shenglang, xinan = self.stores['shenglang'], self.stores['xinan']
        self.assertEqual(list(Store.objects.filter(initialized_at__isnull=False).order_by('pk').values_list('pk', flat=True)),
                         [shenglang.pk, xinan.pk])
        self.commit_unrecorded_audit_events(row_id, 'wire-auto-dispatch')
        row = self.facts('xinan', 'PAYMENT_UNKNOWN', 'PENDING', 80000, 20000)
        with store_context(xinan):
            events = list(WithdrawalPlatformEvent.objects.filter(withdrawal=row).order_by('id'))
            notice_ids = list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('pk', flat=True))
        self.assertEqual([event.action for event in events], ['approve', 'mark-unknown'])
        self.assertEqual(len(notice_ids), 4)
        cursor = PlatformWithdrawalAuditDispatchCursor.objects.create(pk=1, last_store_id=shenglang.pk)
        original_updated_at = cursor.updated_at
        receipts = PlatformWithdrawalAuditReceipt.objects.filter(store=xinan).order_by('tenant_event_id').select_related('audit')
        self.assertFalse(receipts.exists())
        registered_redis = bool(getattr(settings, 'SA14_TEST_REDIS', None))
        published, delivered = [], []

        with _store_task_app(registered_redis=registered_redis) as (app, queue):
            app.finalize()
            sender = app.tasks[dispatch.name]
            scanner = app.tasks[scan_store.name]
            original_run = sender.run
            original_publish = scanner.apply_async
            cut_next = True

            def run_in_worker():
                nonlocal cut_next
                try:
                    if cut_next:
                        cut_next = False
                        return self.interrupt_commit(original_run, after_commit=after_commit,
                                                     write_model=PlatformWithdrawalAuditDispatchCursor, encrypted=encrypted)
                    return original_run()
                finally:
                    connections.close_all()

            def observe_publication(*, args):
                result = original_publish(args=args)
                published.append((args[0], result))
                return result

            def check_scan(store, *, recovered, already_recorded):
                self.assertEqual(len(published), 1)
                store_id, task_result = published.pop()
                self.assertEqual(store_id, store.pk)
                self.assertEqual(task_result.get(timeout=15, disable_sync_subtasks=False),
                    {'status': 'OK', 'store_id': store_id, 'scanned': recovered + already_recorded,
                     'recovered': recovered, 'already_recorded': already_recorded, 'review': 0,
                     'review_events': [], 'next_after': 0})
                delivered.append((store.slug, task_result.id))

            def check_dispatch(store, *, expected_after, already_recorded):
                self.assertEqual(sender.delay().get(timeout=15, disable_sync_subtasks=False),
                                 {'status': 'OK', 'queued': 1, 'next_after': expected_after})
                check_scan(store, recovered=0, already_recorded=already_recorded)
                cursor.refresh_from_db()
                self.assertEqual(cursor.last_store_id, expected_after)

            # Solo execution on one private queue finishes the faulting dispatch
            # and restores its connection settings before the scan can start.
            with (patch.object(sender, 'run', run_in_worker), patch.object(scanner, 'apply_async', observe_publication),
                  patch('stores.withdrawal_audit_tasks.MAX_STORES', 1),
                  start_worker(app, pool='solo', queues=[queue], perform_ping_check=False,
                               without_heartbeat=True, loglevel='WARNING')):
                failed = sender.delay()
                self.assertEqual(failed.get(timeout=15, disable_sync_subtasks=False),
                                 {'status': 'CONTROL_UNAVAILABLE', 'queued': 1})
                check_scan(xinan, recovered=2, already_recorded=0)
                cursor.refresh_from_db()
                first_position = 0 if after_commit else shenglang.pk
                self.assertEqual(cursor.last_store_id, first_position)
                initial_audits = list(receipts.values_list('tenant_event_id', 'audit_id'))
                self.assertEqual([event_id for event_id, _ in initial_audits], [event.pk for event in events])
                if not after_commit:
                    self.assertEqual(cursor.updated_at, original_updated_at)
                    check_dispatch(xinan, expected_after=0, already_recorded=2)
                check_dispatch(shenglang, expected_after=shenglang.pk, already_recorded=0)
                check_dispatch(xinan, expected_after=0, already_recorded=2)

        self.assertEqual(len({task_id for _, task_id in delivered}), len(delivered))
        self.assertEqual(list(receipts.values_list('tenant_event_id', 'audit_id')), initial_audits)
        audits = PlatformAuditEvent.objects.filter(target_store_slug=xinan.slug, target_id=str(row_id), action__startswith='withdraw.')
        self.assertCountEqual(audits.values_list('pk', flat=True), [audit_id for _, audit_id in initial_audits])
        for event, receipt in zip(events, receipts, strict=True):
            audit = receipt.audit
            self.assertEqual((audit.account_id, audit.action), (self.finance.pk, f'withdraw.{event.action}'))
            self.assertEqual((audit.detail['request_id'], audit.detail['request_fingerprint'], audit.detail['recovered_by']),
                             (event.request_id, event.request_fingerprint, None))
        result = self.facts('xinan', 'PAYMENT_UNKNOWN', 'PENDING', 80000, 20000)
        self.assertEqual(result.transaction_id, row.transaction_id)
        with store_context(xinan):
            self.assertEqual(list(WithdrawalPlatformEvent.objects.filter(withdrawal=result).order_by('id').values_list('pk', flat=True)),
                             [event.pk for event in events])
            self.assertFalse(WithdrawalRelease.objects.filter(withdrawal=result).exists())
            self.assertEqual(list(Message.objects.filter(recipient_account=self.owners['xinan']).order_by('id').values_list('pk', flat=True)), notice_ids)
        self.assert_peer(peer_id, apply_id)
        self.assertFalse(PlatformWithdrawalAuditReceipt.objects.filter(store=shenglang).exists())
        broker = 'redis' if registered_redis else 'memory'
        print(f'audit dispatch wire recovery store=xinan withdrawal={row_id} audits={initial_audits} '
              f'cursor={shenglang.pk}->{first_position}->0 tls={encrypted} broker={broker} dispatch={failed.id} '
              f'scans={delivered} ledger={row.transaction_id}/PENDING balance=80000 frozen=20000 peer={peer_id}', flush=True)
