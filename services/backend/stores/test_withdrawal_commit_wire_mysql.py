"""One-shot MySQL reply loss on an explicitly registered test shop connection."""
from select import select
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
from ssl import SSLSocket
from threading import Thread
from time import time
from unittest import skipUnless
from unittest.mock import patch

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
                           PlatformWithdrawalAuditReceipt, Store)
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
        self.finance = PlatformAccount(username='wire-platform-finance')
        self.finance.set_password(self.password)
        self.finance.save()
        PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='xinan')
        PlatformOtpCredential.objects.create(account=self.finance, secret_ciphertext=encrypt_secret(self.otp_secret))
        self.financial = self.sign_in('admin.esports.club', '/api/admin/auth/password-login', self.finance.username)

    def bind_otp(self, account):
        OtpCredential.objects.create(account=account, secret_ciphertext=encrypt_secret(self.otp_secret))

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

    def interrupt_commit(self, operation, *, after_commit, platform=False, encrypted=False):
        self.assertTrue(after_commit or not encrypted, 'The command-before-server control uses the plain test connection')
        connection = connections['store_b']
        require_disposable_mysql(connection)
        self.assertTrue(connection.get_autocommit())
        port = connection.settings_dict['PORT']
        options = connection.settings_dict['OPTIONS']
        original_commit = connection._commit
        client_errors = []
        commit_ready = False
        table = WithdrawalPlatformEvent._meta.db_table if platform else IdempotencyKey._meta.db_table
        verb = 'INSERT INTO' if platform else 'UPDATE'
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

            def observe_receipt_write(execute, sql, params, many, context):
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
                self.assertIsNone(relay.target_peer, 'Expected a single outer store COMMIT')
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
                with connection.execute_wrapper(observe_receipt_write), patch.object(connection, '_commit', side_effect=arm_native_commit):
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
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data['errorCode'], 'WITHDRAWAL_UNAVAILABLE')
        self.assertNotIn('fundResult', response.data)
        self.assertNotIn('data', response.data)
        print(f'wire cut {" ".join(relay.observed)} driver=2013 http=503 alias=store_b', flush=True)

    def submit(self, slug, request_id, amount):
        return self.clients[slug].post(self.path, {'client_request_id': request_id, 'amount': amount,
            'payee_method': 'WECHAT', 'payee_account': f'isolated-{slug}', 'payee_name': '隔离收款人'}, format='json')

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
        response = self.submit('xinan', apply_id, 20000)
        row_id = response.data['data']['request']['id']
        claim = self.staff.post(f'/api/admin/withdrawals/{row_id}/claim/').data
        self.assertEqual(claim['code'], 0, claim)
        reviewed = self.staff.post(f'/api/admin/withdrawals/{row_id}/approve/',
            {'expected_claimed_at': claim['data']['claimed_at']}, format='json')
        self.assertEqual(reviewed.data['code'], 0, reviewed.data)
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
                                      after_commit=True, platform=True, encrypted=encrypted)
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
