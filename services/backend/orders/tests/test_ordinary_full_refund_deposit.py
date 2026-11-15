from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests import test_ordinary_refund_without_addon_funding as prior
from stores.context import current_store, resolve_store, store_context
from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit, Transaction
from wallet.services import get_wallet


@override_settings(**prior.OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
@override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
    SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
    ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False,
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
class OrdinaryFullRefundDepositTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    finish = flow.ReleaseFlowTest.finish

    def scenario(self, *, concurrent=False, partial=False, addon=False):
        from engagement.confirmation import process_order
        from wallet.models import ProviderReport
        tenant = current_store() or resolve_store()
        other = 'default' if tenant.slug == 'xinan' else 'store_b'
        other_before = (Order.objects.using(other).count(), Transaction.objects.using(other).count())
        player = self.accounts[self.provider.pk]
        profile = self.provider.escort_profile
        profile.deposit_mode, profile.deposit_paid = 'CASH', 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'escort:deposit_topup', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        target_half = 500 if addon else 1000
        if addon:
            from orders.models import GameCategory, ServiceAddonConfiguration, ServiceAddonPriceOption
            from orders.tests.factories import make_service
            game = GameCategory.objects.create(name='单陪退款加购押金')
            self.service.game_category = game
            self.service.save(update_fields=['game_category'])
            profile.service_items.add(self.service)
            extra = make_service(name='已完成加购', game_category=game, is_addon=True)
            config = ServiceAddonConfiguration.objects.create(service=self.service, addon=extra, enabled=True,
                condition_mode='NONE', conditions='', version=1)
            ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        self.login(self.admin)
        self.ok(self.client.put('/api/admin/config/deposit-targets', {
            'cash_target': target_half, 'earnings_target': target_half + 500, 'expected_version': 0}, format='json'))
        self.ok(self.recharge())
        def topup(amount, key):
            self.login(self.admin)
            return self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': amount, 'reason': '隔离线下押金', 'reference': key, 'client_request_id': key}, format='multipart'))
        topup(target_half, 'initial-deposit')
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_id': player.pk, 'support_contact_id': self.card.pk, 'client_request_id': 'deposit-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        if addon:
            self.login(self.boss)
            path = f'/api/orders/orders/{order_id}/addons/intents/'
            intent = self.ok(self.client.post(path, {'addon_id': extra.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': player.pk, 'request_id': 'deposit-addon',
                'expected_amount': 1000, 'gift_balance_amount': 1000, 'compensation_balance_amount': 0}, format='json'))
            payment_id = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [player.pk], 'reason': '全额退款', 'client_request_id': 'deposit-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'deposit-plan', 'expected_version': 0, 'refund_units': 9000 if addon else 10000,
            'provider_terms': [{'account_id': player.pk, 'retained_units': 0}], 'explanation': '全额退款'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        url = f"/api/admin/aftersales/{case['id']}/negotiation-execution/"
        payload = {'proposal_id': proposal['id'], 'client_request_id': 'deposit-execute'}
        before = get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance
        denied = self.client.post(url, payload, format='json')
        self.assertNotEqual(denied.data.get('code'), 0, denied.data)
        self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        self.assertFalse(RefundDepositDeficit.objects.exists())
        self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance, before)
        self.login(self.admin)
        self.ok(self.client.put('/api/admin/config/deposit-targets', {
            'cash_target': target_half * 2, 'earnings_target': target_half * 2 + 500, 'expected_version': 1}, format='json'))
        topup(target_half, 'second-deposit')
        self.login(self.admin)
        with patch('wallet.refund_deposit.RefundDepositDeficit.objects.create', side_effect=RuntimeError('gap failure')):
            with self.assertRaisesMessage(RuntimeError, 'gap failure'):
                self.client.post(url, payload, format='json')
        profile.refresh_from_db(); self.assertEqual(profile.deposit_paid, target_half * 2)
        self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        self.assertEqual(get_wallet(user=self.boss, account=self.accounts[self.boss.pk]).balance, before)
        self.ok(self.client.post(url, payload, format='json'))
        self.ok(self.client.post(url, payload, format='json'))
        order = Order.objects.get(pk=order_id)
        share = order.refund_records.get().negotiation_execution.shares.get(account=player)
        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction), (8000, 0, target_half * 2))
        gap = RefundDepositDeficit.objects.get(refund__order=order)
        self.assertEqual((gap.amount, gap.remaining), (target_half * 2, target_half * 2))
        profile.refresh_from_db(); self.assertEqual(profile.deposit_paid, 0)
        if addon:
            self.login(self.admin)
            staff_url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/completion/'
            item = self.ok(self.client.post(staff_url, {'request_id': 'deposit-addon-complete',
                'completed_work': '约定加购全部完成'}, format='json'))
            decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/completion/{item["id"]}/decision/'
            for user in (self.boss, self.provider):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        original_report = ProviderReport.objects.get(order=order, provider_account=player)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{original_report.pk}/images/',
                {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{original_report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{original_report.pk}/approve/', {}, format='json'))
        original_report.refresh_from_db()
        with store_context(tenant), patch('django.utils.timezone.now', return_value=original_report.submitted_at + timedelta(hours=73)):
            process_order(order_id)
            process_order(order_id)
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        gap.refresh_from_db(); self.assertEqual(gap.remaining, 200 if addon else 2000)
        if partial:
            self.service.price = 1000
            self.service.save(update_fields=['price'])
        self.login(self.boss)
        def new_order(key):
            return self.client.post('/api/orders/create/', {'service_id': self.service.pk,
                'provider_id': player.pk, 'support_contact_id': self.card.pk, 'client_request_id': key}, format='json')
        if concurrent:
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            from django.db import close_old_connections
            from rest_framework.test import APIClient
            from club_accounts.tokens import issue_account_tokens
            with store_context(tenant):
                token = issue_account_tokens(self.accounts[self.boss.pk])[0]
            barrier = Barrier(2)
            def create(key):
                close_old_connections()
                try:
                    with store_context(tenant):
                        client = APIClient(HTTP_HOST='sl.esports.club')
                        barrier.wait(timeout=20)
                        response = client.post('/api/orders/create/', {'service_id': self.service.pk,
                            'provider_id': player.pk, 'support_contact_id': self.card.pk, 'client_request_id': key},
                            format='json', HTTP_AUTHORIZATION='Bearer ' + token)
                        return response.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(create, ('recovery-race-1', 'recovery-race-2')))
            accepted = [row for row in results if row.get('code') == 0]
            self.assertEqual(len(accepted), 1, results)
            recovery_id = accepted[0]['data']['id']
        else:
            recovery_id = self.ok(new_order('deposit-recovery'))['id']
        self.assertNotEqual(new_order('deposit-blocked').data.get('code'), 0)
        self.assertEqual(RefundDepositRecoveryPermit.objects.filter(account=player, closed_at__isnull=True).count(), 1)
        report = self.finish(Order.objects.get(pk=recovery_id))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        with store_context(tenant), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            with patch('wallet.refund_deposit.RefundDepositRepayment.objects.create', side_effect=RuntimeError('repayment failure')):
                with self.assertRaisesMessage(RuntimeError, 'repayment failure'):
                    process_order(recovery_id)
            gap.refresh_from_db(); self.assertEqual(gap.remaining, 200 if addon else 2000)
            self.assertIsNone(Order.objects.get(pk=recovery_id).settled_at)
            process_order(recovery_id)
            process_order(recovery_id)
        gap.refresh_from_db(); profile.refresh_from_db()
        if partial:
            self.assertEqual((gap.remaining, profile.deposit_paid), (1200, 800))
            self.assertEqual(get_wallet(user=self.provider, account=player).balance, 0)
            self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=player, closed_at__isnull=True).exists())
            self.login(self.boss)
            next_id = self.ok(new_order('next-recovery'))['id']
            self.assertNotEqual(new_order('next-recovery-blocked').data.get('code'), 0)
            next_report = self.finish(Order.objects.get(pk=next_id))
            self.ok(self.client.post(f'/api/wallet/reports/{next_report.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{next_report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{next_report.pk}/approve/', {}, format='json'))
            next_report.refresh_from_db()
            with store_context(tenant), patch('django.utils.timezone.now', return_value=next_report.submitted_at + timedelta(hours=73)):
                process_order(next_id)
                process_order(next_id)
            gap.refresh_from_db(); profile.refresh_from_db()
            self.assertEqual((gap.remaining, profile.deposit_paid), (400, 1600))
            self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=player, closed_at__isnull=True).exists())
        else:
            self.assertEqual((gap.remaining, profile.deposit_paid), (0, 1000 if addon else 2000))
            self.assertEqual(get_wallet(user=self.provider, account=player).balance, 7800 if addon else 6000)
            self.assertFalse(RefundDepositRecoveryPermit.objects.filter(account=player, closed_at__isnull=True).exists())
        self.assertEqual((Order.objects.using(other).count(), Transaction.objects.using(other).count()), other_before)

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_partial_refund_deposit_and_income_recovery(self):
        self.scenario(addon=True)

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
        ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
    def test_single_addon_deposit_recovery_in_xinan(self):
        from stores.tests_hosts import xinan_store
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.scenario(addon=True)

    def test_partial_income_releases_one_next_recovery_opportunity(self):
        self.scenario(partial=True)

    def test_full_refund_deposit_and_recovery_shenglang(self):
        self.scenario()

    def test_full_refund_deposit_and_recovery_xinan(self):
        from stores.tests_hosts import xinan_store
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.scenario()


@override_settings(**prior.OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
@override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
    SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
    ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False,
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
class OrdinaryFullRefundDepositMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    finish = flow.ReleaseFlowTest.finish
    scenario = OrdinaryFullRefundDepositTest.scenario

    def test_two_recovery_orders_compete_for_one_permit(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.scenario(concurrent=True)
