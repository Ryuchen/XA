"""Committed funds writes can be recovered without another debit."""
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_service_category, make_user
from users.models import ProviderPassPurchase
from wallet.models import IdempotencyKey, SystemConfig, Transaction, WithdrawRequest
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class FundReceiptTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.provider = make_provider(deposit_required=10000)
        self.accounts = {}
        self.clients = {}
        for user in (self.boss, self.provider):
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            self.accounts[user.pk] = account
            wallet = get_wallet(account=account, user=user)
            wallet.balance = 100000
            wallet.save(update_fields=['balance'])
            client = APIClient()
            token, _ = issue_account_tokens(account)
            client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
            self.clients[user.pk] = client
        profile = self.provider.escort_profile
        profile.account = self.accounts[self.provider.pk]
        profile.save(update_fields=['account'])
        self.service = make_service(price=1000)
        self.gift = make_service(price=500, service_category=make_service_category(is_gift=True))
        self.source = make_order(self.boss, self.service, self.provider, status=Order.Status.COMPLETED,
                                 customer_account=self.accounts[self.boss.pk],
                                 provider_account=self.accounts[self.provider.pk])
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch',
                     'notify_order_update', '_push_message_safe'):
            patcher = patch(f'orders.views.{name}')
            patcher.start()
            self.addCleanup(patcher.stop)

    def operations(self):
        return [
            (self.boss, '/api/orders/create/', 'order.create', {
                'service_id': self.service.pk, 'game_rounds': 1, 'game_region': '微信区',
                'game_nickname': '回执测试', 'expected_amount': 1000,
            }),
            (self.boss, f'/api/orders/orders/{self.source.pk}/tip/', 'order.tip', {
                'gift_service_id': self.gift.pk, 'expected_amount': 500,
            }),
            (self.provider, '/api/users/provider-pass/', 'provider.pass', {'tier': 'BRONZE', 'days': 1}),
            (self.provider, '/api/wallet/deposit/', 'deposit', {'amount': 10000}),
            (self.provider, '/api/wallet/withdraw/', 'withdraw', {
                'amount': 20000, 'payee_method': 'WECHAT', 'payee_account': 'receipt-test', 'payee_name': '测试',
            }),
        ]

    def post(self, user, path, payload):
        return self.clients[user.pk].post(path, payload, format='json')

    def test_all_five_operations_recover_the_exact_success_without_money_changes(self):
        for user, path, scope, payload in self.operations():
            with self.subTest(scope=scope):
                payload['client_request_id'] = f'receipt-{scope}'
                first = self.post(user, path, payload)
                self.assertEqual(first.data['code'], 0, first.data)
                before = list(Transaction.objects.order_by('pk').values_list('pk', 'amount', 'balance_after'))
                duplicate = self.post(user, path, dict(reversed(list(payload.items()))))
                self.assertEqual(duplicate.data['code'], 409)
                self.assertEqual(duplicate.data['errorCode'], 'FUND_ALREADY_PROCESSED')
                self.assertEqual(duplicate.data['fundResult'], first.data)
                self.assertEqual(before, list(Transaction.objects.order_by('pk').values_list('pk', 'amount', 'balance_after')))
                receipt = IdempotencyKey.objects.get(account=self.accounts[user.pk], request_id=payload['client_request_id'])
                self.assertEqual(receipt.response, first.data)
                self.assertEqual(len(receipt.request_fingerprint), 64)

    def test_recovery_precedes_changed_catalog_and_allows_new_intent_afterwards(self):
        user, path, _, payload = self.operations()[0]
        payload['client_request_id'] = 'order-lost'
        first = self.post(user, path, payload)
        self.service.is_active = False
        self.service.price = 3000
        self.service.save()
        self.assertEqual(self.post(user, path, payload).data['fundResult'], first.data)
        self.service.is_active = True
        self.service.save()
        new = self.post(user, path, {**payload, 'client_request_id': 'new-intent', 'expected_amount': 3000})
        self.assertEqual(new.data['code'], 0, new.data)
        self.assertNotEqual(first.data['data']['id'], new.data['data']['id'])
        wallet = get_wallet(account=self.accounts[user.pk])
        self.assertEqual(wallet.balance, 96000)

    def test_pass_recovery_precedes_price_change_and_does_not_extend_twice(self):
        user, path, _, payload = self.operations()[2]
        payload.update(client_request_id='pass-lost', expected_daily_price=200)
        first = self.post(user, path, payload)
        SystemConfig.objects.update_or_create(key='provider_pass_daily_price_BRONZE', defaults={'value': '500'})
        self.assertEqual(self.post(user, path, payload).data['fundResult'], first.data)
        self.assertEqual(ProviderPassPurchase.objects.count(), 1)

    def test_withdrawal_recovery_preserves_the_original_snapshot_after_rule_change(self):
        user, path, _, payload = self.operations()[4]
        payload['client_request_id'] = 'withdraw-lost'
        first = self.post(user, path, payload)
        SystemConfig.objects.update_or_create(key='min_withdraw_amount', defaults={'value': '90000'})
        self.assertEqual(self.post(user, path, payload).data['fundResult'], first.data)
        self.assertEqual(WithdrawRequest.objects.count(), 1)
        self.assertEqual(get_wallet(account=self.accounts[user.pk]).frozen_amount, 20000)

    def test_changed_parameters_or_endpoint_never_return_an_unrelated_success(self):
        user, path, _, payload = self.operations()[0]
        payload['client_request_id'] = 'same-id'
        self.assertEqual(self.post(user, path, payload).data['code'], 0)
        for target, body in ((path, {**payload, 'game_rounds': 2}),
                             (f'/api/orders/orders/{self.source.pk}/tip/', {'client_request_id': 'same-id', 'gift_service_id': self.gift.pk})):
            result = self.post(user, target, body)
            self.assertEqual(result.data['errorCode'], 'REQUEST_ID_CONFLICT')
            self.assertNotIn('fundResult', result.data)
        self.assertEqual(get_wallet(account=self.accounts[user.pk]).balance, 99000)

    def test_gift_request_id_is_bound_to_its_source_order_path(self):
        user, path, _, payload = self.operations()[1]
        payload['client_request_id'] = 'gift-lost'
        self.assertEqual(self.post(user, path, payload).data['code'], 0)
        wrong = self.post(user, '/api/orders/orders/999999/tip/', payload)
        self.assertEqual(wrong.data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertNotIn('fundResult', wrong.data)

    def test_receipts_are_scoped_to_authenticated_account(self):
        user, path, _, payload = self.operations()[0]
        payload['client_request_id'] = 'private-receipt'
        self.assertEqual(self.post(user, path, payload).data['code'], 0)
        other = self.post(self.provider, path, payload)
        self.assertNotIn('fundResult', other.data)
        self.assertNotEqual(other.data['code'], 0)
        anonymous = APIClient().post(path, payload, format='json')
        self.assertEqual(anonymous.status_code, 401)

    def test_receipt_failure_rolls_back_each_debit_and_idempotency_claim(self):
        for user, path, scope, payload in self.operations():
            with self.subTest(scope=scope):
                payload['client_request_id'] = f'rollback-{scope}'
                tx_count = Transaction.objects.count()
                with patch('wallet.receipts._fingerprint', side_effect=RuntimeError('receipt storage failed')):
                    with self.assertRaisesRegex(RuntimeError, 'receipt storage failed'):
                        self.post(user, path, payload)
                wallet = get_wallet(account=self.accounts[user.pk])
                self.assertEqual(wallet.balance, 100000)
                self.assertEqual(wallet.frozen_amount, 0)
                self.assertEqual(Transaction.objects.count(), tx_count)
                self.assertFalse(IdempotencyKey.objects.filter(request_id=payload['client_request_id']).exists())
        self.assertEqual(Order.objects.count(), 1)  # Only the source-order fixture.
        self.assertFalse(ProviderPassPurchase.objects.exists())
        self.assertFalse(WithdrawRequest.objects.exists())
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual(self.provider.escort_profile.deposit_paid, 0)

    def test_legacy_keys_without_receipts_remain_explicitly_unresolved(self):
        user, path, scope, payload = self.operations()[0]
        payload['client_request_id'] = 'historical'
        IdempotencyKey.objects.create(account=self.accounts[user.pk], request_id='historical', scope=scope)
        result = self.post(user, path, payload)
        self.assertEqual(result.data['errorCode'], 'FUND_RESULT_UNAVAILABLE')
        self.assertNotIn('fundResult', result.data)
        self.assertEqual(get_wallet(account=self.accounts[user.pk]).balance, 100000)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class FundReceiptConcurrencyTest(TransactionTestCase):
    setUp = FundReceiptTest.setUp
    operations = FundReceiptTest.operations

    def race(self, user, path, payload):
        from wallet.receipts import _replay
        barrier = Barrier(2)
        token, _ = issue_account_tokens(self.accounts[user.pk])

        def simultaneous_miss(request, scope):
            result = _replay(request, scope)
            if result is None:
                barrier.wait(timeout=5)
            return result

        def send(_):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
                return client.post(path, payload, format='json').data
            finally:
                connections.close_all()

        with patch('wallet.receipts._replay', side_effect=simultaneous_miss):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(send, range(2), timeout=20))
        self.assertEqual(sorted(item['code'] for item in results), [0, 409], results)
        success = next(item for item in results if item['code'] == 0)
        replay = next(item for item in results if item['code'] == 409)
        self.assertEqual(replay['errorCode'], 'FUND_ALREADY_PROCESSED')
        self.assertEqual(replay['fundResult'], success)
        return success

    def test_simultaneous_first_requests_share_a_receipt_for_each_funds_operation(self):
        for user, path, scope, payload in self.operations():
            with self.subTest(scope=scope):
                payload['client_request_id'] = f'race-{scope}'
                self.race(user, path, payload)
                self.assertEqual(IdempotencyKey.objects.filter(
                    account=self.accounts[user.pk], request_id=payload['client_request_id'],
                    response__isnull=False,
                ).count(), 1)
        self.assertEqual(Order.objects.count(), 3)  # Fixture + purchase + gift.
        self.assertEqual(ProviderPassPurchase.objects.count(), 1)
        self.assertEqual(WithdrawRequest.objects.count(), 1)

    def test_a_racing_withdrawal_recovers_even_when_first_debit_exhausts_balance(self):
        user, path, _, payload = self.operations()[4]
        wallet = get_wallet(account=self.accounts[user.pk])
        wallet.balance = payload['amount']
        wallet.save(update_fields=['balance'])
        self.race(user, path, {**payload, 'client_request_id': 'race-last-balance'})
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 0)
        self.assertEqual(wallet.frozen_amount, payload['amount'])
