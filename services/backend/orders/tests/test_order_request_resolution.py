from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_service, make_user, make_wallet
from wallet.models import IdempotencyKey, Transaction, Wallet
from wallet.services import get_wallet

CREATE = '/api/orders/create/'
RESOLVE = '/api/orders/create/resolve/'


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class OrderRequestResolutionTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.account = get_or_create_account_for_legacy_user(self.boss)
        make_wallet(self.boss, balance=100000)
        self.service = make_service(price=10000)
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        for name in ('_schedule_auto_cancel', 'enqueue_kook_dispatch', 'enqueue_wecom_dispatch', 'notify_order_update'):
            patcher = patch('orders.views.' + name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def payload(self, request_id='resolve-test'):
        return {'client_request_id': request_id, 'service_id': self.service.pk, 'dispatch_route': 'STAFF'}

    def resolve(self, payload):
        return self.client.post(RESOLVE, {'client_request_id': payload['client_request_id'], 'payload': payload}, format='json')

    def test_sealed_request_is_permanent_and_repeated_resolution_is_idempotent(self):
        payload = self.payload()
        self.assertEqual(self.resolve(payload).data['data']['status'], 'SEALED')
        self.assertEqual(self.resolve(payload).data['data']['status'], 'SEALED')
        late = self.client.post(CREATE, payload, format='json')
        self.assertEqual(late.data['errorCode'], 'FUND_REQUEST_SEALED')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)
        altered = {**payload, 'dispatch_route': 'PUBLIC'}
        self.assertEqual(self.resolve(altered).data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(self.client.post(CREATE, altered, format='json').data['errorCode'], 'REQUEST_ID_CONFLICT')

    def test_committed_request_returns_matching_receipt_only(self):
        payload = self.payload()
        created = self.client.post(CREATE, payload, format='json').data
        result = self.resolve(payload).data
        self.assertEqual(result['data']['status'], 'COMMITTED')
        self.assertEqual(result['data']['fundResult'], created)
        self.assertEqual(self.resolve({**payload, 'dispatch_route': 'PUBLIC'}).data['errorCode'], 'REQUEST_ID_CONFLICT')
        self.assertEqual(Order.objects.count(), 1)

    def test_old_receipt_and_foreign_scope_remain_unresolved(self):
        for scope, expected in (('order.create', 'FUND_RESULT_UNAVAILABLE'), ('withdraw', 'REQUEST_ID_CONFLICT')):
            payload = self.payload(scope)
            IdempotencyKey.objects.create(account=self.account, request_id=scope, scope=scope)
            response = self.resolve(payload)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['errorCode'], expected)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 100000)

    def test_requires_original_request_id_and_authenticated_boss(self):
        payload = self.payload()
        for bad in ('', 'auto:reserved', 'x' * 65):
            result = self.client.post(RESOLVE, {'client_request_id': bad, 'payload': {**payload, 'client_request_id': bad}}, format='json')
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.post(RESOLVE, {'client_request_id': 'different', 'payload': payload}, format='json').status_code, 400)
        anonymous = APIClient().post(RESOLVE, {'client_request_id': payload['client_request_id'], 'payload': payload}, format='json')
        self.assertEqual(anonymous.status_code, 401)
        self.assertFalse(IdempotencyKey.objects.exists())

    def test_another_account_cannot_recover_this_accounts_receipt(self):
        payload = self.payload()
        created = self.client.post(CREATE, payload, format='json').data
        other = make_user()
        other_account = get_or_create_account_for_legacy_user(other)
        make_wallet(other, balance=100000)
        token, _ = issue_account_tokens(other_account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        response = self.resolve(payload).data
        self.assertEqual(response['data']['status'], 'SEALED')
        self.assertNotIn('fundResult', response['data'])
        self.assertEqual(Order.objects.get(pk=created['data']['id']).customer_account_id, self.account.pk)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnlessDBFeature('has_select_for_update')
class OrderResolutionRaceTest(TransactionTestCase):
    def run_race(self, owner):
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        make_wallet(boss, balance=100000)
        service = make_service(price=10000)
        token, _ = issue_account_tokens(account)
        payload = {'client_request_id': 'race-' + owner, 'service_id': service.pk, 'dispatch_route': 'STAFF'}
        locked, waiting, release = Event(), Event(), Event()
        other = 'resolve' if owner == 'create' else 'create'

        def observe_wallet(*args, **kwargs):
            if kwargs.get('for_update') and current_thread().name == other:
                waiting.set()
            wallet = get_wallet(*args, **kwargs)
            if kwargs.get('for_update') and current_thread().name == owner:
                locked.set()
                if not release.wait(10):
                    raise AssertionError('wallet lock was not released')
            return wallet

        def post(path, data):
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                return client.post(path, data, format='json').data
            finally:
                close_old_connections()

        def run_as(name, path, data):
            current_thread().name = name
            return post(path, data)

        requests = {'create': (CREATE, payload), 'resolve': (RESOLVE, {'client_request_id': payload['client_request_id'], 'payload': payload})}
        with patch('orders.views.get_wallet', side_effect=observe_wallet), \
             patch('orders.views._schedule_auto_cancel'), patch('orders.views.notify_order_update'), \
             ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(run_as, owner, *requests[owner])
            try:
                self.assertTrue(locked.wait(10), 'first request failed to acquire wallet lock')
                second = workers.submit(run_as, other, *requests[other])
                self.assertTrue(waiting.wait(10), 'second request never attempted the locked wallet')
            finally:
                release.set()
            first_data, second_data = first.result(timeout=20), second.result(timeout=20)
        results = {owner: first_data, other: second_data}
        wallet = Wallet.objects.get(user=boss)
        if owner == 'create':
            self.assertEqual(results['create']['code'], 0, results)
            self.assertEqual(results['resolve']['data']['status'], 'COMMITTED', results)
            self.assertEqual(results['resolve']['data']['fundResult'], results['create'])
            self.assertEqual(wallet.balance, 90000)
            self.assertEqual(Order.objects.filter(customer=boss).count(), 1)
        else:
            self.assertEqual(results['resolve']['data']['status'], 'SEALED', results)
            self.assertEqual(results['create']['errorCode'], 'FUND_REQUEST_SEALED', results)
            self.assertEqual(wallet.balance, 100000)
            self.assertFalse(Order.objects.filter(customer=boss).exists())
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PAY').count(), 1 if owner == 'create' else 0)

    def test_creation_holds_wallet_before_resolution(self):
        self.run_race('create')

    def test_resolution_holds_wallet_before_creation(self):
        self.run_race('resolve')
