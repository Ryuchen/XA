from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_provider, make_user
from wallet.models import IdempotencyKey, Transaction, WithdrawalAccount, WithdrawRequest
from wallet.services import get_wallet
from wallet.views import claim_fund_write

URL = '/api/wallet/withdraw-accounts/'


class PayeeFixture:
    def setUp(self):
        self.provider = make_provider()
        self.account = get_or_create_account_for_legacy_user(self.provider)
        self.token, _ = issue_account_tokens(self.account)
        self.client = self.client_for(self.account)
        wallet = get_wallet(account=self.account)
        wallet.balance = 50000
        wallet.save(update_fields=['balance'])

    def client_for(self, account):
        token, _ = issue_account_tokens(account)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        return client

    def bind(self, method='WECHAT', **changes):
        return self.client.put(URL + method + '/', {
            'payee_account': ' original-account ', 'payee_name': ' 张三 ',
            'expected_id': 0, 'expected_version': 0, **changes,
        }, format='json')

    def withdrawal(self, row, **changes):
        return self.client.post('/api/wallet/withdraw/', {
            'amount': 20000, 'withdrawal_account_id': row['id'], 'expected_payee_version': row['version'],
            'client_request_id': 'saved-payee-withdraw', **changes,
        }, format='json')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class WithdrawalAccountTest(PayeeFixture, APITestCase):
    def test_three_methods_have_separate_destinations_and_same_method_updates_in_place(self):
        self.assertEqual(self.client.get(URL).data['data'], [])
        for method in WithdrawRequest.PayeeMethod.values:
            result = self.bind(method)
            self.assertEqual(result.data['code'], 0, result.data)
        row = self.client.get(URL).data['data'][0]
        edited = self.bind(row['payee_method'], expected_id=row['id'], expected_version=row['version'],
                           payee_account='changed-account', payee_name='李四').data['data']
        self.assertEqual(edited['id'], row['id'])
        self.assertEqual(edited['version'], row['version'] + 1)
        self.assertEqual(WithdrawalAccount.objects.filter(account=self.account).count(), 3)
        unchanged = WithdrawalAccount.objects.exclude(pk=row['id'])
        self.assertTrue(all(item.payee_name == '张三' and item.payee_account == 'original-account' for item in unchanged))

    def test_only_authenticated_providers_can_manage_accounts(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
        boss = get_or_create_account_for_legacy_user(make_user())
        client = self.client_for(boss)
        for result in (client.get(URL), client.put(URL + 'WECHAT/', {}, format='json'),
                       client.delete(URL + 'WECHAT/', {}, format='json')):
            self.assertEqual(result.data['code'], 403)
        self.assertFalse(WithdrawalAccount.objects.exists())

    def test_binding_list_and_deletion_are_owner_scoped(self):
        own = self.bind().data['data']
        other = get_or_create_account_for_legacy_user(make_provider())
        foreign = WithdrawalAccount.objects.create(account=other, payee_method='WECHAT', payee_account='secret', payee_name='其他人')
        self.assertEqual([row['id'] for row in self.client.get(URL).data['data']], [own['id']])
        result = self.client.delete(URL + 'WECHAT/', {'expected_id': foreign.pk, 'expected_version': foreign.version}, format='json')
        self.assertEqual(result.data['code'], 409)
        result = self.bind('BANK', account=other.pk, payee_name='本人')
        self.assertEqual(result.data['code'], 0)
        self.assertEqual(WithdrawalAccount.objects.get(pk=result.data['data']['id']).account_id, self.account.pk)
        self.assertTrue(WithdrawalAccount.objects.filter(pk=foreign.pk, payee_account='secret').exists())

    def test_invalid_values_do_not_create_or_replace_accounts(self):
        for changes in ({'payee_account': ''}, {'payee_account': 'a' * 101}, {'payee_name': []},
                        {'payee_name': 'a' * 51}, {'payee_account': 123}, {'expected_version': True}, {'expected_id': -1}):
            with self.subTest(changes=changes):
                self.assertEqual(self.bind(**changes).data['code'], 400)
        self.assertEqual(self.bind('INVALID').data['code'], 400)
        self.assertFalse(WithdrawalAccount.objects.exists())

    def test_stale_edits_and_delete_cannot_replace_a_new_binding(self):
        old = self.bind().data['data']
        updated = self.bind(expected_id=old['id'], expected_version=1, payee_account='updated').data['data']
        self.assertEqual(self.bind(expected_id=old['id'], expected_version=1).data['errorCode'], 'PAYEE_CHANGED')
        deleted = self.client.delete(URL + 'WECHAT/', {'expected_id': updated['id'], 'expected_version': updated['version']}, format='json')
        self.assertEqual(deleted.data['code'], 0)
        new = self.bind(payee_account='new-binding').data['data']
        self.assertNotEqual(new['id'], old['id'])
        stale = self.bind(expected_id=old['id'], expected_version=1)
        self.assertEqual(stale.data['errorCode'], 'PAYEE_CHANGED')
        self.assertEqual(WithdrawalAccount.objects.get(pk=new['id']).payee_account, 'new-binding')

    def test_submitted_snapshot_and_receipt_survive_account_edit_and_unbind(self):
        row = self.bind().data['data']
        result = self.withdrawal(row)
        self.assertEqual(result.data['code'], 0, result.data)
        updated = self.bind(expected_id=row['id'], expected_version=1, payee_account='changed', payee_name='李四').data['data']
        self.client.delete(URL + 'WECHAT/', {'expected_id': updated['id'], 'expected_version': updated['version']}, format='json')
        saved = WithdrawRequest.objects.get(account=self.account)
        self.assertEqual((saved.payee_method, saved.payee_account, saved.payee_name), ('WECHAT', 'original-account', '张三'))
        replay = self.withdrawal(row)
        self.assertEqual(replay.data['fundResult'], result.data)
        self.assertEqual(WithdrawRequest.objects.count(), 1)
        self.assertEqual(get_wallet(account=self.account).frozen_amount, 20000)

    def test_changed_or_removed_or_foreign_destination_never_freezes_money(self):
        row = self.bind().data['data']
        self.bind(expected_id=row['id'], expected_version=1, payee_account='new-account')
        result = self.withdrawal(row)
        self.assertEqual(result.data['errorCode'], 'PAYEE_CHANGED')
        other = get_or_create_account_for_legacy_user(make_provider())
        foreign = WithdrawalAccount.objects.create(account=other, payee_method='BANK', payee_account='private', payee_name='其他人')
        self.assertEqual(self.withdrawal({'id': foreign.pk, 'version': 1}).data['errorCode'], 'PAYEE_CHANGED')
        WithdrawalAccount.objects.filter(account=self.account).delete()
        self.assertEqual(self.withdrawal(row).data['errorCode'], 'PAYEE_CHANGED')
        self.assertEqual(get_wallet(account=self.account).balance, 50000)
        self.assertFalse(WithdrawRequest.objects.exists())
        self.assertFalse(IdempotencyKey.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_bound_account_requires_version_and_cannot_mix_manual_fields(self):
        row = self.bind().data['data']
        for changes in ({'expected_payee_version': None}, {'withdrawal_account_id': True}, {'payee_name': '伪造姓名'}):
            self.assertEqual(self.withdrawal(row, **changes).data['code'], 400)
        self.assertFalse(WithdrawRequest.objects.exists())


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class WithdrawalAccountConcurrencyTest(PayeeFixture, TransactionTestCase):
    def assert_destination_change_during_submission(self, method):
        row = self.bind().data['data']
        withdrawal_ready, editor_waiting = Event(), Event()

        def pause_before_receipt(**kwargs):
            # The withdrawal already owns its wallet and destination locks.
            # Overlap the edit before the receipt inserts its account FK.
            withdrawal_ready.set()
            if not editor_waiting.wait(timeout=10):
                raise AssertionError('Destination change did not reach its row lock')
            return claim_fund_write(**kwargs)

        def observe_destination_lock(execute, sql, params, many, context):
            if 'FOR UPDATE' in sql.upper() and any(
                name in sql.lower() for name in ('wallet_wallet', 'wallet_withdrawalaccount')
            ):
                editor_waiting.set()
            return execute(sql, params, many, context)

        def submit():
            close_old_connections()
            try:
                return self.client_for(self.account).post('/api/wallet/withdraw/', {
                    'amount': 20000, 'withdrawal_account_id': row['id'],
                    'expected_payee_version': row['version'], 'client_request_id': 'overlap-payee',
                }, format='json').data
            finally:
                connections.close_all()

        def change():
            close_old_connections()
            try:
                with connections['default'].execute_wrapper(observe_destination_lock):
                    return getattr(self.client_for(self.account), method)(URL + 'WECHAT/', {
                        'expected_id': row['id'], 'expected_version': row['version'],
                        'payee_account': 'changed', 'payee_name': '李四',
                    }, format='json').data
            finally:
                connections.close_all()

        with patch('wallet.views.claim_fund_write', side_effect=pause_before_receipt):
            with ThreadPoolExecutor(max_workers=2) as pool:
                submitted = pool.submit(submit)
                try:
                    self.assertTrue(withdrawal_ready.wait(timeout=10))
                    changed = pool.submit(change)
                    self.assertEqual(submitted.result(timeout=20)['code'], 0)
                    self.assertEqual(changed.result(timeout=20)['code'], 0)
                finally:
                    editor_waiting.set()

        saved = WithdrawRequest.objects.get(account=self.account)
        self.assertEqual((saved.payee_account, saved.payee_name), ('original-account', '张三'))
        wallet = get_wallet(account=self.account)
        self.assertEqual((wallet.balance, wallet.frozen_amount), (30000, 20000))
        self.assertEqual(Transaction.objects.filter(wallet=wallet).count(), 1)
        if method == 'delete':
            self.assertFalse(WithdrawalAccount.objects.filter(pk=row['id']).exists())
        else:
            current = WithdrawalAccount.objects.get(pk=row['id'])
            self.assertEqual((current.payee_account, current.payee_name, current.version), ('changed', '李四', 2))

    def test_edit_during_submission_preserves_snapshot_without_deadlock(self):
        self.assert_destination_change_during_submission('put')

    def test_unbind_during_submission_preserves_snapshot_without_deadlock(self):
        self.assert_destination_change_during_submission('delete')

    def race(self, operations):
        barrier = Barrier(2)
        def send(item):
            close_old_connections()
            try:
                client = self.client_for(self.account)
                barrier.wait(timeout=5)
                return getattr(client, item[0])(item[1], item[2], format='json').data
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(send, operations, timeout=20))

    def test_concurrent_first_binding_creates_only_one_destination(self):
        base = {'payee_name': '张三', 'expected_id': 0, 'expected_version': 0}
        results = self.race([('put', URL + 'WECHAT/', {**base, 'payee_account': name}) for name in ('first', 'second')])
        self.assertEqual(sorted(row['code'] for row in results), [0, 409], results)
        self.assertEqual(WithdrawalAccount.objects.filter(account=self.account, payee_method='WECHAT').count(), 1)

    def test_edit_and_withdraw_compete_without_mixing_payee_details(self):
        row = self.bind().data['data']
        results = self.race([
            ('put', URL + 'WECHAT/', {'expected_id': row['id'], 'expected_version': 1, 'payee_account': 'changed', 'payee_name': '李四'}),
            ('post', '/api/wallet/withdraw/', {'amount': 20000, 'withdrawal_account_id': row['id'], 'expected_payee_version': 1, 'client_request_id': 'race-payee'}),
        ])
        self.assertEqual(results[0]['code'], 0, results)
        if results[1]['code'] == 0:
            withdrawal = WithdrawRequest.objects.get(account=self.account)
            self.assertEqual((withdrawal.payee_account, withdrawal.payee_name), ('original-account', '张三'))
            self.assertEqual(get_wallet(account=self.account).frozen_amount, 20000)
        else:
            self.assertEqual(results[1]['errorCode'], 'PAYEE_CHANGED', results)
            self.assertFalse(WithdrawRequest.objects.exists())
            self.assertEqual(get_wallet(account=self.account).balance, 50000)
