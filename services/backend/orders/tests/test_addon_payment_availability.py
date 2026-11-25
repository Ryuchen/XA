from stores.db import transaction
from django.test import override_settings
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.models import PointFundingLot
from orders.models import (AfterSale, GameCategory, OrderAddonIntent, OrderAddonPayment, OrderParticipationSegment,
                           ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider, make_service
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
    ADDON_SETTLEMENT_INTERNAL_TESTING=False, SERVICE_POINTS_CHECKOUT_TESTING=False,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AddonPaymentAvailabilityTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def prepare(self, *, disabled_on_create=False, prefund=False):
        second = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        second.escort_profile.account = second_account
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.fulfillment_provider_count = 2
        game = GameCategory.objects.create(name='加购支付状态')
        self.service.game_category = game
        self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count', 'game_category'])
        for player in (self.provider, second):
            player.escort_profile.service_items.add(self.service)
        addon = make_service(name='状态加购', game_category=game, is_addon=True)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        option = ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        if prefund:
            from orders.tests.factories import make_user
            inviter = get_or_create_account_for_legacy_user(make_user())
            account = self.accounts[self.boss.pk]
            account.inviter, account.inviter_commission_rate = inviter, 20
            account.save(update_fields=['inviter', 'inviter_commission_rate'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, second_account.pk],
            'support_contact_id': self.card.pk, 'gift_balance_amount': 1000 if prefund else 0,
            'client_request_id': 'availability-order'}, format='json'))['id']
        for player in (self.provider, second):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        if prefund:
            from uuid import uuid4
            from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            from stores.context import current_store, resolve_store
            tenant = current_store() or resolve_store()
            if tenant.slug == 'xinan':
                from unittest.mock import patch
                with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                    finance.setUp()
            else:
                finance.setUp()
            receipt = finance.post(tenant.slug, {**finance.data, 'amount': 1000})
            self.assertEqual(receipt.status_code, 200, receipt.data)
            claim = receipt.data['data']['claim_id']
            self.assertEqual(finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=tenant.slug).status_code, 200)
            result = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{tenant.slug}/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 1000}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(result.status_code, 200, result.data)
            self.funding_allocation_id = result.data['data']['allocation_id']
            from orders.financial_lock import lock_order_money
            with transaction.atomic():
                lock_order_money()
        self.login(self.boss)
        self.path = f'/api/orders/orders/{order_id}/addons/intents/'
        payload = {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1, 'configuration_version': 1,
                   'provider_account_id': second_account.pk, 'request_id': 'availability-addon',
                   'expected_amount': 1000, 'gift_balance_amount': 0 if prefund else 1000, 'compensation_balance_amount': 0}
        from orders.models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun
        self.before_intent = (Transaction.objects.count(), OrderAddonFundingRelease.objects.count(),
                              OrderAddonFundingReleaseRun.objects.count())
        from contextlib import nullcontext
        switched = override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False) if disabled_on_create else nullcontext()
        with switched:
            item = self.ok(self.client.post(self.path, payload, format='json'))
        self.item_id, self.config, self.option = item['id'], config, option
        self.player, self.order_id = second_account, order_id
        return item

    def state(self):
        self.login(self.boss)
        items = self.ok(self.client.get(self.path))['items']
        return next(item for item in items if item['id'] == self.item_id)

    def test_existing_funding_preview_is_scoped_to_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            self.client.defaults['HTTP_HOST'] = 'xa.esports.club'
            item = self.prepare(prefund=True)
            self.assertTrue(item['payment_available'])
            self.assertTrue(self.state()['payment_available'])
            paid = self.ok(self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json'))
            self.assertTrue(paid['paid'])
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

    def test_missing_money_mutex_stays_unknown_without_creating_it(self):
        from orders.models import OrderFinancialLock
        item = self.prepare(prefund=True)
        self.assertTrue(item['payment_available'])
        # Intent creation is a write path and legitimately creates the mutex.
        # Remove it afterwards so both preview reads exercise the real
        # missing-lock state rather than the creation transaction's lock.
        OrderFinancialLock.objects.filter(pk=1).delete()
        self.assertFalse(self.state()['payment_available'])
        self.assertFalse(OrderFinancialLock.objects.exists())
        paid = self.ok(self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json'))
        self.assertTrue(paid['paid'])
        self.assertTrue(OrderFinancialLock.objects.filter(pk=1).exists())

    def test_missing_money_mutex_without_extra_platform_funding_stays_unknown(self):
        from orders.models import OrderFinancialLock
        item = self.prepare()
        self.assertTrue(item['payment_available'])
        OrderFinancialLock.objects.filter(pk=1).delete()
        self.assertFalse(self.state()['payment_available'])
        self.assertFalse(OrderFinancialLock.objects.exists())

    def test_existing_funding_is_verified_without_posting_or_releasing(self):
        from orders.models import OrderAddonFundingRelease, OrderAddonFundingReleaseRun, OrderFinancialLock, OrderPointsFundingAction
        item = self.prepare(prefund=True)
        self.assertTrue(item['payment_available'])
        self.assertEqual((Transaction.objects.count(), OrderAddonFundingRelease.objects.count(),
                          OrderAddonFundingReleaseRun.objects.count()), self.before_intent)
        before = (Transaction.objects.count(), OrderAddonFundingRelease.objects.count(),
                  OrderAddonFundingReleaseRun.objects.count(), OrderFinancialLock.objects.count())
        self.assertTrue(self.state()['payment_available'])
        self.assertEqual((Transaction.objects.count(), OrderAddonFundingRelease.objects.count(),
                          OrderAddonFundingReleaseRun.objects.count(), OrderFinancialLock.objects.count()), before)
        from django.db import DatabaseError
        from unittest.mock import patch
        with patch('orders.points_funding_evidence._verified_allocations_for_locked_order',
                   side_effect=DatabaseError('control plane unavailable')):
            self.assertFalse(self.state()['payment_available'])
        self.assertEqual((Transaction.objects.count(), OrderAddonFundingRelease.objects.count(),
                          OrderAddonFundingReleaseRun.objects.count(), OrderFinancialLock.objects.count()), before)
        with transaction.atomic():
            OrderPointsFundingAction.objects.filter(allocation_id=self.funding_allocation_id).update(result={})
            self.assertFalse(self.state()['payment_available'])
            rejected = self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json')
            self.assertNotEqual(rejected.status_code, 200, rejected.data)
            self.assertFalse(OrderAddonPayment.objects.exists())
            transaction.set_rollback(True)
        self.assertTrue(self.state()['payment_available'])
        self.client.defaults['HTTP_HOST'] = 'xa.esports.club'
        self.assertNotEqual(self.client.get(self.path).status_code, 200)
        self.client.defaults['HTTP_HOST'] = 'sl.esports.club'
        paid = self.ok(self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json'))
        self.assertTrue(paid['paid'])
        self.assertEqual(OrderAddonFundingRelease.objects.count(), 1)
        self.assertEqual(OrderAddonFundingRelease.objects.get().amount, 200)


    def test_disabled_when_intent_is_created(self):
        item = self.prepare(disabled_on_create=True)
        self.assertFalse(item['payment_available'])
        self.assertTrue(self.state()['payment_available'])

    def test_live_availability_rechecks_and_payment_is_idempotent(self):
        item = self.prepare()
        self.assertTrue(item['payment_available'])
        wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk])
        before = (wallet.balance, list(PointFundingLot.objects.filter(account=self.accounts[self.boss.pk])
                  .order_by('pk').values_list('pk', 'remaining', 'gift_remaining')))
        self.assertTrue(self.state()['payment_available'])
        wallet.refresh_from_db()
        self.assertEqual((wallet.balance, list(PointFundingLot.objects.filter(account=self.accounts[self.boss.pk])
            .order_by('pk').values_list('pk', 'remaining', 'gift_remaining'))), before)
        with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False):
            self.assertFalse(self.state()['payment_available'])
        for change in ('balance', 'source', 'config', 'price', 'provider', 'aftersale'):
            with self.subTest(change=change), transaction.atomic():
                if change == 'balance':
                    wallet.balance = 0; wallet.save(update_fields=['balance'])
                elif change == 'source':
                    PointFundingLot.objects.filter(account=self.accounts[self.boss.pk], kind='RECHARGE').update(gift_remaining=0)
                elif change == 'config':
                    self.config.version += 1; self.config.save(update_fields=['version'])
                elif change == 'price':
                    self.option.price += 10; self.option.save(update_fields=['price'])
                elif change == 'provider':
                    OrderParticipationSegment.objects.filter(attribution__order_id=self.order_id,
                        provider_account=self.player).update(released_at=self.config.updated_at)
                else:
                    AfterSale.objects.create(order_id=self.order_id, account=self.accounts[self.boss.pk], reason='核对')
                self.assertFalse(self.state()['payment_available'])
                rejected = self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json')
                self.assertNotEqual(rejected.status_code, 200, rejected.data)
                self.assertFalse(OrderAddonPayment.objects.exists())
                transaction.set_rollback(True)
            self.config.refresh_from_db(); self.option.refresh_from_db(); wallet.refresh_from_db()
        from engagement.models import ClubOperationConfig
        with transaction.atomic():
            ClubOperationConfig.objects.filter(pk=1).update(base_points_rate=1001)
            self.assertFalse(self.state()['payment_available'])
            result = self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json')
            self.assertNotEqual(result.status_code, 200, result.data)
            self.assertFalse(OrderAddonPayment.objects.exists())
            transaction.set_rollback(True)
        with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True):
            from stores.db import connection
            if connection.vendor != 'mysql':
                self.assertFalse(self.state()['payment_available'])
        with transaction.atomic():
            role = self.admin.admin_membership.roles.first()
            role.permissions = [permission for permission in role.permissions if permission != 'order:dispatch']
            role.save(update_fields=['permissions'])
            self.login(self.admin)
            admin_items = self.ok(self.client.get(f'/api/admin/orders/{self.order_id}/addons/intents/'))['items']
            self.assertFalse(admin_items[0]['payment_available'])
            transaction.set_rollback(True)
        self.login(self.boss)
        self.client.defaults['HTTP_HOST'] = 'xa.esports.club'
        cross_store = self.client.get(self.path)
        self.assertNotEqual(cross_store.status_code, 200)
        self.client.defaults['HTTP_HOST'] = 'sl.esports.club'
        self.assertTrue(self.state()['payment_available'])
        first = self.ok(self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json'))
        second = self.ok(self.client.post(self.path + str(self.item_id) + '/pay/', {}, format='json'))
        self.assertEqual(first['payment_id'], second['payment_id'])
        self.assertTrue(first['paid']); self.assertFalse(first['payment_available'])
        self.assertFalse(self.state()['payment_available'])
        self.assertEqual(OrderAddonPayment.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(remark__contains='加购支付', tx_type='PAY').count(), 1)


@override_settings(**AddonPaymentAvailabilityTest._overridden_settings)
class AddonFundedPreviewMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    prepare = AddonPaymentAvailabilityTest.prepare

    def test_preview_and_payment_serialize_without_extra_release(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connection
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderAddonFundingRelease
        from stores.context import resolve_store, store_context
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        item = self.prepare(prefund=True)
        self.assertTrue(item['payment_available'])
        store = resolve_store()
        with store_context(store):
            token = issue_account_tokens(self.accounts[self.boss.pk])[0]
        barrier = Barrier(2)
        def run(kind):
            close_old_connections()
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    barrier.wait(timeout=20)
                    if kind == 'read':
                        response = client.get(self.path)
                    else:
                        response = client.post(self.path + str(self.item_id) + '/pay/', {}, format='json')
                    return response.status_code, response.data
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            read, paid = list(pool.map(run, ('read', 'pay')))
        self.assertEqual(read[0], 200, read)
        self.assertEqual(paid[0], 200, paid)
        state = read[1]['data']['items'][0]
        if state['paid']:
            self.assertFalse(state['payment_available'])
        self.assertEqual(OrderAddonPayment.objects.count(), 1)
        self.assertEqual(Transaction.objects.filter(remark__contains='加购支付', tx_type='PAY').count(), 1)
        self.assertEqual(list(OrderAddonFundingRelease.objects.values_list('amount', flat=True)), [200])
