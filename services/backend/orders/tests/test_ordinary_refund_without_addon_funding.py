from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_user
from wallet.models import ProviderReport
from wallet.services import get_wallet


@override_settings(
    NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
    NEGOTIATION_ZERO_EXECUTION_ENABLED=True, ORDINARY_ZERO_NO_ADDON_FUNDING_INTERNAL_TESTING=True,
    ORDINARY_ZERO_ADDON_FUNDING_INTERNAL_TESTING=True,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
    ADDON_HANDOVER_INTERNAL_TESTING=True, ORDINARY_ZERO_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING=True,
    ADDON_COMPLETION_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_ORDINARY_FUNDING_TESTING=True, ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
)
class OrdinaryRefundWithoutAddonFundingTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    @override_settings(ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=True)
    def test_full_refund_zero_net_settles_once_after_report(self):
        self.exercise_full_refund()

    def exercise_full_refund(self, *, preallocated=False, concurrent=False):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsSettlement, OrderPointsSettlementPerson
        from orders.points_funding_evidence import verify_order_funding
        from orders.settlement import SettlementError
        from stores.context import current_store, resolve_store, store_context
        from wallet.models import Transaction

        tenant = current_store() or resolve_store()
        other_alias = 'default' if tenant.slug == 'xinan' else 'store_b'
        other_store_before = (Order.objects.using(other_alias).count(), Transaction.objects.using(other_alias).count())
        if preallocated:
            inviter_user = make_user()
            inviter = get_or_create_account_for_legacy_user(inviter_user)
            boss_account = self.accounts[self.boss.pk]
            boss_account.inviter, boss_account.inviter_commission_rate = inviter, 20
            boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        self.ok(self.recharge())
        boss, player = self.accounts[self.boss.pk], self.accounts[self.provider.pk]
        player_wallet = get_wallet(user=self.provider, account=player)
        player_wallet.balance = 2000  # Isolated opening balance for the refund burden.
        player_wallet.save(update_fields=['balance'])
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'provider_id': player.pk, 'gift_balance_amount': 1000,
            'support_contact_id': self.card.pk, 'client_request_id': 'full-refund-zero-order',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        if preallocated:
            from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            tenant = current_store() or resolve_store()
            if tenant.slug == 'xinan':
                with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                    finance.setUp()
            else:
                finance.setUp()
            slug = tenant.slug
            registered = finance.post(slug, {**finance.data, 'amount': 1000})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            verified = finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=slug)
            self.assertEqual(verified.status_code, 200, verified.data)
            allocation_url = f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation'
            allocation = finance.client.post(allocation_url, {'request_id': str(uuid4()),
                'order_id': order_id, 'amount': 1000}, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(allocation.status_code, 200, allocation.data)
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
            'order_id': order_id, 'affected_provider_account_ids': [player.pk],
            'reason': '全部服务退款', 'client_request_id': 'full-refund-zero-case',
        }, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'full-refund-zero-proposal', 'expected_version': 0, 'refund_units': 10000,
            'provider_terms': [{'account_id': player.pk, 'retained_units': 0}], 'explanation': '全额退款',
        }, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                                     {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        url = f"/api/admin/aftersales/{case['id']}/negotiation-execution/"
        payload = {'proposal_id': proposal['id'], 'client_request_id': 'full-refund-zero-execute'}
        if preallocated:
            from orders.models import OrderAddonFundingReleaseRun
            with patch('orders.ordinary_refunded_addon_release.OrderAddonFundingRelease.objects.bulk_create',
                       side_effect=RuntimeError('refund release write failed')):
                with self.assertRaisesMessage(RuntimeError, 'refund release write failed'):
                    self.client.post(url, payload, format='json')
            self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
            self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(negotiation__refund__order_id=order_id).exists())
        if concurrent:
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            from django.db import close_old_connections
            from rest_framework.test import APIClient
            from club_accounts.tokens import issue_account_tokens
            with store_context(tenant):
                token = issue_account_tokens(self.accounts[self.admin.pk])[0]
            barrier = Barrier(2)
            def execute(_):
                close_old_connections()
                try:
                    with store_context(tenant):
                        client = APIClient(HTTP_HOST='admin.esports.club')
                        barrier.wait(timeout=20)
                        response = client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + token)
                        return response.status_code, response.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(execute, range(2)))
            self.assertEqual([row[0] for row in results], [200, 200], results)
        self.ok(self.client.post(url, payload, format='json'))
        self.ok(self.client.post(url, payload, format='json'))
        if preallocated:
            release = OrderAddonFundingReleaseRun.objects.get(negotiation__refund__order_id=order_id)
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (1000, 0, 1000))
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.payment_status, 'REFUNDED')
        self.assertEqual(order.refund_records.count(), 1)
        self.assertEqual(get_wallet(user=self.boss, account=boss).balance, 21000)
        player_wallet.refresh_from_db()
        self.assertEqual(player_wallet.balance, 0)
        share = order.refund_records.get().negotiation_execution.shares.get(account=player)
        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction), (8000, 2000, 0))
        report = ProviderReport.objects.get(order_id=order_id, provider_account=player)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        with store_context(current_store() or resolve_store()), patch(
                'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(verify_order_funding(order_id).fully_funded)
            from orders.points_checkout_test import ordinary_refund_production_order
            if not ordinary_refund_production_order(Order.objects.get(pk=order_id)):
                with override_settings(ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False):
                    with self.assertRaises(SettlementError):
                        process_order(order_id)
            if not OrderPointsSettlement.objects.filter(order=order).exists():
                # 旧延迟结算时序：结算仅由 process_order 触发，验证注入失败原子性。
                from orders.services import _credit_wallet

                def fail_store_credit(**kwargs):
                    if kwargs['tx_type'] == Transaction.TxType.SHOP_INCOME:
                        raise RuntimeError('injected settlement failure')
                    return _credit_wallet(**kwargs)

                with patch('orders.services._credit_wallet', side_effect=fail_store_credit):
                    with self.assertRaisesMessage(RuntimeError, 'injected settlement failure'):
                        process_order(order_id)
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(OrderPointsSettlementPerson.objects.filter(settlement__order=order).exists())
                self.assertEqual(get_wallet(user=self.boss, account=boss).balance, 21000)
            # 现行生产接线可能在报单审核时即完成零收益结算；两种时序都须幂等。
            process_order(order_id)
            process_order(order_id)
            order.refresh_from_db()
            settlement = OrderPointsSettlement.objects.get(order=order)
            self.assertEqual(settlement.paid_units, 0)
            self.assertIsNotNone(order.settled_at)
            self.assertEqual(OrderPointsSettlementPerson.objects.filter(settlement=settlement, amount=0).count(), 1)
            self.assertFalse(order.transactions.filter(tx_type=Transaction.TxType.INCOME,
                                                       wallet__account=player).exists())
            confirmation = ConsumptionConfirmation.objects.get(order=order)
            self.assertEqual((confirmation.amount, confirmation.refunded_amount), (10000, 10000))
            self.assertTrue(verify_order_funding(order_id, allow_finalized=True).fully_funded)
        self.assertEqual((Order.objects.using(other_alias).count(), Transaction.objects.using(other_alias).count()),
                         other_store_before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False)
    def test_production_full_refund_releases_funding_and_settles_zero(self):
        self.exercise_full_refund(preallocated=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False)
    def test_production_full_refund_stays_in_xinan(self):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_full_refund(preallocated=True)

    def test_refund_funding_and_settlement_through_real_interfaces(self):
        self.exercise_refund_funding()

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False)
    def test_production_partial_refund_funding_and_settlement(self):
        self.exercise_refund_funding()

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True,
        BOSS_FUNDING_SELECTION_ENABLED=False, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False)
    def test_default_principal_partial_refund_and_settlement(self):
        self.card.staff_account = self.accounts[self.admin.pk]
        self.card.save(update_fields=['staff_account'])
        self.exercise_refund_funding(principal_only=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, DEFAULT_PRINCIPAL_SERVICE_FUNDING_ENABLED=True,
        BOSS_FUNDING_SELECTION_ENABLED=False, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False)
    def test_default_principal_partial_refund_stays_in_xinan(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.models import Transaction
        first = resolve_store()
        with store_context(first):
            before = (Order.objects.count(), Transaction.objects.count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.card.staff_account = self.accounts[self.admin.pk]
            self.card.save(update_fields=['staff_account'])
            self.exercise_refund_funding(principal_only=True)
        with store_context(first):
            self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False)
    def test_production_double_partial_refund_settles_individually(self):
        self.exercise_refund_funding(double=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False)
    def test_production_partial_refund_stays_in_xinan(self):
        self.exercise_xinan(zero=False)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False)
    def test_production_refund_releases_preallocated_funding_atomically(self):
        self.exercise_refund_funding(refund_preallocated=True)

    def test_double_service_settles_each_provider_after_whole_order_is_funded(self):
        self.exercise_refund_funding(double=True)

    def test_zero_refund_termination_waits_for_whole_order_funding(self):
        self.exercise_refund_funding(double=True, zero=True)

    def test_zero_refund_with_other_provider_addon(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True)

    def test_zero_refund_after_addon_partially_refunded(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_refund=500)

    def test_zero_refund_after_addon_fully_refunded(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_refund=1000)

    def test_zero_refund_does_not_settle_unresolved_affected_provider_addon(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True)

    def test_refund_does_not_settle_unresolved_affected_provider_addon(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True)

    def test_zero_refund_then_addon_handover_preserves_both_earnings(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True, handover=True)

    def test_refund_then_addon_handover_preserves_both_earnings(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True, handover=True)

    def test_refund_and_handover_each_release_surplus_funding(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, refund_preallocated=True)

    def test_multiple_addons_can_be_handed_over_independently_after_refund(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, refund_preallocated=True,
                                     multiple_addons=True)

    def test_completed_addon_and_another_handover_after_refund(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, refund_preallocated=True,
                                     multiple_addons=True, mixed_addons=True)

    def test_addon_refund_after_main_refund_releases_surplus_and_settles(self):
        self.exercise_refund_funding(double=True, addon=True, preallocated=True,
                                     refund_preallocated=True, addon_after_main=True)

    def test_two_addon_refunds_after_main_refund(self):
        self.exercise_refund_funding(double=True, addon=True, preallocated=True,
                                     refund_preallocated=True, addon_after_main=True, split_addon_refund=True)

    def test_addon_refund_after_handover_and_main_refund(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, refund_preallocated=True,
                                     handover_addon_refund=True)

    def test_addon_refund_while_another_addon_handover_is_pending(self):
        self.exercise_refund_funding(double=True, addon=True, preallocated=True, refund_preallocated=True,
                                     multiple_addons=True, pending_other_addon_refund=True)

    def test_achievement_counts_confirmed_addon_with_vip_disabled(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, refund_preallocated=True,
                                     achievement_vip_disabled=True)

    def test_terminated_provider_completed_addon_after_both_confirm(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True, completion=True)

    def test_single_provider_completed_addon_after_both_confirm(self):
        self.exercise_refund_funding(zero=True, addon=True, addon_on_affected=True, completion=True)

    def test_completed_addon_after_partial_addon_refund(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                     addon_refund=500, completion=True)

    def test_completed_addon_after_main_order_partial_refund(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True, completion=True)

    def test_completed_addon_and_main_refunded_separately(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     addon_refund=500, completion=True)

    def test_single_provider_completed_addon_after_main_refund(self):
        self.exercise_refund_funding(addon=True, addon_on_affected=True, completion=True)

    def test_main_refund_releases_prefunding_before_addon_confirmation(self):
        self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True,
                                     completion=True, refund_preallocated=True)

    def test_rejected_completion_can_be_reissued(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                     completion=True, reject_completion=True)

    def test_pending_handover_blocks_completion_decision(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                     completion=True, completion_pending_handover=True)

    def test_handover_releases_surplus_preallocated_funding(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True)

    def test_handover_can_increase_the_remaining_funding_need(self):
        self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                     handover=True, preallocated=True, increasing=True)

    def test_zero_refund_funding_stays_in_xinan(self):
        self.exercise_xinan()

    def test_zero_refund_with_addon_stays_in_xinan(self):
        self.exercise_xinan(addon=True)

    def test_handover_release_stays_in_xinan(self):
        self.exercise_xinan(addon=True, preallocated=True)

    def test_refund_handover_releases_stay_in_xinan(self):
        self.exercise_xinan(addon=True, preallocated=True, zero=False, refund_preallocated=True)

    def test_addon_after_main_refund_stays_in_xinan(self):
        self.exercise_xinan(addon=True, preallocated=True, zero=False, refund_preallocated=True,
                            addon_after_main=True)

    def test_independent_addon_refund_with_pending_handover_stays_in_xinan(self):
        self.exercise_xinan(addon=True, preallocated=True, zero=False, refund_preallocated=True,
                            pending_other_addon_refund=True)

    def test_achievement_confirmed_addon_stays_in_xinan(self):
        self.exercise_xinan(addon=True, preallocated=True, zero=False, refund_preallocated=True,
                            achievement_vip_disabled=True)

    def test_completion_confirmation_stays_in_xinan(self):
        self.exercise_xinan(addon=True, completion=True)

    def test_completion_after_refund_stays_in_xinan(self):
        self.exercise_xinan(addon=True, completion=True, zero=False)

    def test_refund_prefunding_release_stays_in_xinan(self):
        self.exercise_xinan(addon=True, completion=True, zero=False, refund_preallocated=True)

    def exercise_xinan(self, *, addon=False, preallocated=False, completion=False, zero=True,
                       refund_preallocated=False, addon_after_main=False, pending_other_addon_refund=False,
                       achievement_vip_disabled=False):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.models import Transaction
        first = resolve_store()
        with store_context(first):
            before = (Order.objects.count(), Transaction.objects.count())
            if achievement_vip_disabled:
                from engagement.achievement_metrics import confirmed_service_metrics
                first_account = self.accounts[self.boss.pk]
                first_metrics = confirmed_service_metrics(first_account)
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login

            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'

            self.login = bound_login
            self.exercise_refund_funding(double=True, zero=zero, addon=addon,
                addon_refund=500 if addon and not preallocated and not completion else 0,
                addon_on_affected=(preallocated or completion) and not addon_after_main and not pending_other_addon_refund,
                handover=preallocated and not addon_after_main and not pending_other_addon_refund,
                preallocated=preallocated, completion=completion, refund_preallocated=refund_preallocated,
                addon_after_main=addon_after_main, multiple_addons=pending_other_addon_refund,
                pending_other_addon_refund=pending_other_addon_refund, achievement_vip_disabled=achievement_vip_disabled)
        with store_context(first):
            self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)
            if achievement_vip_disabled:
                self.assertEqual(confirmed_service_metrics(first_account), first_metrics)

    def exercise_refund_funding(self, *, double=False, zero=False, addon=False, addon_refund=0,
                                addon_on_affected=False, handover=False, preallocated=False, increasing=False,
                                completion=False, reject_completion=False, completion_pending_handover=False, refund_preallocated=False,
                                concurrent_completion=False, multiple_addons=False, mixed_addons=False,
                                race_completion_handover=False, addon_after_main=False, split_addon_refund=False,
                                handover_addon_refund=False, pending_other_addon_refund=False,
                                achievement_vip_disabled=False, concurrent_refund=False, expect_rejected=False,
                                concurrent_addon_refund=False, addon_after_partial=False, addon_after_partial_gift=False,
                                principal_only=False):
        from engagement.confirmation import process_order
        from orders.points_funding_evidence import verify_order_funding
        from stores.context import current_store, resolve_store, store_context
        from stores.test_platform_pass_repayment import PlatformPassRepaymentTests

        inviter_user = make_user()
        if achievement_vip_disabled:
            from users.models import Achievement
            Achievement.objects.create(code='confirmed-order-test', title='实销订单', metric='orders', target=1)
            Achievement.objects.create(code='confirmed-amount-test', title='实销金额', metric='amount', target=20000)
        inviter = get_or_create_account_for_legacy_user(inviter_user)
        boss = self.accounts[self.boss.pk]
        boss.inviter, boss.inviter_commission_rate = inviter, (15 if increasing else 30) if preallocated else 20
        boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        second = None
        if double:
            from orders.tests.factories import make_provider
            second = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
            second_account = get_or_create_account_for_legacy_user(second)
            self.accounts[second.pk] = second_account
            second.escort_profile.account = second_account
            if preallocated:
                from orders.tests.factories import make_escort_level
                second.escort_profile.level = make_escort_level(commission_rate=10 if increasing else 40)
            second.escort_profile.save(update_fields=['account'])
            if preallocated:
                second.escort_profile.save(update_fields=['level'])
            self.service.billing_provider_count = 2
            self.service.fulfillment_provider_count = 2
            if preallocated:
                self.service.commission_policy = 'FUN'
                self.service.save(update_fields=['commission_policy'])
            self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        if addon:
            from orders.models import GameCategory, ServiceAddonConfiguration, ServiceAddonPriceOption
            from orders.tests.factories import make_service
            game = GameCategory.objects.create(name='零退款加购测试')
            self.service.game_category = game
            self.service.save(update_fields=['game_category'])
            for player in (self.provider, second) if second else (self.provider,):
                player.escort_profile.service_items.add(self.service)
            extra = make_service(name='加购服务', game_category=game, is_addon=True)
            config = ServiceAddonConfiguration.objects.create(service=self.service, addon=extra, enabled=True,
                condition_mode='NONE', conditions='', version=1)
            ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        self.ok(self.recharge())
        if multiple_addons or addon_after_main or handover_addon_refund:
            self.ok(self.recharge(trade_no='receipt-second', request_id='recharge-second'))
        self.login(self.boss)
        selection = ({'provider_ids': [self.accounts[self.provider.pk].pk, second_account.pk]}
                     if double else {'provider_id': self.accounts[self.provider.pk].pk})
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, **selection,
            'gift_balance_amount': 0 if principal_only else 1000, 'support_contact_id': self.card.pk,
            'client_request_id': 'no-addon-refund-funding',
        }, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        if second:
            self.login(second)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        if addon:
            self.login(self.boss)
            path = f'/api/orders/orders/{order_id}/addons/intents/'
            intent = self.ok(self.client.post(path, {'addon_id': extra.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': (self.accounts[self.provider.pk].pk
                                                                    if addon_on_affected else second_account.pk),
                'request_id': 'zero-addon', 'expected_amount': 1000,
                'gift_balance_amount': 1000 if addon_after_partial_gift else 500 if addon_after_main or handover_addon_refund or pending_other_addon_refund else 0,
                'compensation_balance_amount': 0}, format='json'))
            addon_payment = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
            if multiple_addons:
                second_intent = self.ok(self.client.post(path, {'addon_id': extra.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': self.accounts[self.provider.pk].pk,
                    'request_id': 'second-addon', 'expected_amount': 1000, 'gift_balance_amount': 0,
                    'compensation_balance_amount': 0}, format='json'))
                second_payment = self.ok(self.client.post(path + str(second_intent['id']) + '/pay/', {}, format='json'))['payment_id']
            if preallocated or refund_preallocated:
                tenant = current_store() or resolve_store()
                finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
                if tenant.slug == 'xinan':
                    with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                        finance.setUp()
                else:
                    finance.setUp()
                slug = tenant.slug
                receipt = finance.post(slug, {**finance.data, 'amount': 2000 if addon_after_main or handover_addon_refund or pending_other_addon_refund else 1000})
                self.assertEqual(receipt.status_code, 200, receipt.data)
                claim = receipt.data['data']['claim_id']
                verified = finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=slug)
                self.assertEqual(verified.status_code, 200, verified.data)
                allocation_url = f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation'
                allocation = finance.client.post(allocation_url, {'request_id': str(uuid4()),
                    'order_id': order_id, 'amount': 1600 if addon_after_partial_gift else 900 if pending_other_addon_refund else 1300 if handover_addon_refund else 1100 if addon_after_main else 600 if multiple_addons else 800}, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
                self.assertEqual(allocation.status_code, 200, allocation.data)
            if addon_refund:
                addon_owner = self.provider if addon_on_affected else second
                addon_owner_account = self.accounts[addon_owner.pk]
                self.login(self.admin)
                addon_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
                    'order_id': order_id, 'affected_provider_account_ids': [addon_owner_account.pk],
                    'reason': '部分加购未履约', 'client_request_id': 'zero-addon-case'}, format='json'))
                url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/'
                addon_proposal = self.ok(self.client.post(url, {'request_id': 'zero-addon-refund',
                    'amount_raw': addon_refund, 'case_id': addon_case['id'], 'explanation': '加购退款'}, format='json'))
                decision = f"/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{addon_proposal['id']}/"
                for user in (self.boss, addon_owner):
                    self.login(user)
                    self.ok(self.client.post(decision, {'accepted': True}, format='json'))
                self.login(self.admin)
                self.ok(self.client.post(url + f"{addon_proposal['id']}/execute/", {}, format='json'))
                self.ok(self.client.post(f"/api/admin/aftersales/{addon_case['id']}/resolve/",
                                         {'resolution': '加购退款完成'}, format='json'))
        if refund_preallocated and not addon:
            tenant = current_store() or resolve_store()
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            finance.setUp()
            slug = tenant.slug
            receipt = finance.post(slug, {**finance.data, 'amount': 1000})
            self.assertEqual(receipt.status_code, 200, receipt.data)
            claim = receipt.data['data']['claim_id']
            verified = finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=slug)
            self.assertEqual(verified.status_code, 200, verified.data)
            allocation_url = f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation'
            allocation = finance.client.post(allocation_url, {'request_id': str(uuid4()),
                'order_id': order_id, 'amount': 1000}, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(allocation.status_code, 200, allocation.data)
        self.login(self.admin)
        account = self.accounts[self.provider.pk]
        income = OrderProvider.objects.get(order_id=order_id, provider_account=account).provider_income
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
            'order_id': order_id, 'affected_provider_account_ids': [account.pk],
            'reason': '协商终止', 'client_request_id': 'no-addon-case',
        }, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'no-addon-proposal', 'expected_version': 0, 'refund_units': 0 if zero else 1000,
            'provider_terms': [{'account_id': account.pk, 'retained_units': income - (0 if zero else 1000)}],
            'explanation': '协商退款',
        }, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                                     {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        execution_url = f"/api/admin/aftersales/{case['id']}/negotiation-execution/"
        execution_payload = {'proposal_id': proposal['id'], 'client_request_id': 'no-addon-execute'}
        if expect_rejected:
            before = get_wallet(user=self.boss, account=boss).balance
            rejected = self.client.post(execution_url, execution_payload, format='json')
            self.assertNotEqual(rejected.data.get('code'), 0, rejected.data)
            self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, before)
            return
        if refund_preallocated and not multiple_addons and not addon_after_main and not handover_addon_refund:
            from orders.models import OrderAddonFundingReleaseRun
            with patch('orders.ordinary_refunded_addon_release.OrderAddonFundingRelease.objects.bulk_create',
                       side_effect=RuntimeError('refund release write failed')):
                with self.assertRaisesMessage(RuntimeError, 'refund release write failed'):
                    self.client.post(execution_url, execution_payload, format='json')
            self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
            self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(negotiation__refund__order_id=order_id).exists())
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, 11000 if not addon else 10000 if not second else 0)
        if concurrent_refund:
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier
            from django.db import close_old_connections
            from rest_framework.test import APIClient
            from club_accounts.tokens import issue_account_tokens
            tenant_for_threads = current_store() or resolve_store()
            with store_context(tenant_for_threads):
                token = issue_account_tokens(self.accounts[self.admin.pk])[0]
            barrier = Barrier(2)
            def submit_refund(_):
                close_old_connections()
                try:
                    with store_context(tenant_for_threads):
                        client = APIClient(HTTP_HOST='admin.esports.club')
                        barrier.wait(timeout=20)
                        response = client.post(execution_url, execution_payload, format='json',
                                               HTTP_AUTHORIZATION='Bearer ' + token)
                        return response.status_code, response.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit_refund, range(2)))
            self.assertEqual([row[0] for row in results], [200, 200], results)
            self.assertEqual(Order.objects.get(pk=order_id).refund_records.count(), 1)
        execution_response = self.client.post(execution_url, execution_payload, format='json')
        if execution_response.status_code != 200:
            from orders.models import NegotiationProposal
            item = Order.objects.get(pk=order_id)
            agreement = NegotiationProposal.objects.get(pk=proposal['id'])
            self.fail(f'{execution_response.data}; now={timezone.now()} in_service={item.in_service_at} '
                      f'accepted={agreement.accepted_at} completed={item.completed_at} status={item.status} '
                      f'clock={(item.service_clock_version, item.service_aftersale_hours, item.service_freeze_hours, item.service_deposit_percent)} '
                      f'settlement_mode={item.settlement_mode}')
        self.ok(execution_response)
        if principal_only:
            self.ok(self.client.post(execution_url, execution_payload, format='json'))
        if zero:
            self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        if refund_preallocated:
            from orders.models import OrderAddonFundingReleaseRun
            release = OrderAddonFundingReleaseRun.objects.get(negotiation__refund__order_id=order_id)
            expected_release = ((1600, 1300, 300) if addon_after_partial_gift else (1000, 800, 200) if not addon else (900, 600, 300) if pending_other_addon_refund else (1300, 1000, 300) if handover_addon_refund else
                                (1100, 800, 300) if addon_after_main else (600, 300, 300) if multiple_addons else
                                (800, 500, 300) if preallocated else (800, 600, 200))
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), expected_release)
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance,
                             12000 if not addon else 11000 if not second else 22000 if addon_after_main or handover_addon_refund else 21000 if multiple_addons else 1000)
            self.assertEqual(self.client.post(execution_url, execution_payload, format='json').status_code, 200)
            self.assertEqual(OrderAddonFundingReleaseRun.objects.filter(negotiation__refund__order_id=order_id).count(), 1)
        if addon_after_main:
            if split_addon_refund:
                self.login(self.admin)
                first_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
                    'order_id': order_id, 'affected_provider_account_ids': [second_account.pk],
                    'reason': '加购部分未履约', 'client_request_id': 'after-main-first-addon-case'}, format='json'))
                first_url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/'
                first = self.ok(self.client.post(first_url, {'request_id': 'first-addon-refund',
                    'amount_raw': 500, 'case_id': first_case['id'], 'explanation': '加购部分退款'}, format='json'))
                first_decision = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{first["id"]}/'
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(first_decision, {'accepted': True}, format='json'))
                self.login(self.admin)
                self.ok(self.client.post(first_url + f'{first["id"]}/execute/', {}, format='json'))
                self.ok(self.client.post(f'/api/admin/aftersales/{first_case["id"]}/resolve/',
                                         {'resolution': '首笔加购退款完成'}, format='json'))
                from orders.models import OrderAddonRefund, OrderAddonFundingReleaseRun
                first_refund = OrderAddonRefund.objects.get(payment_id=addon_payment)
                self.assertEqual((first_refund.addon_pending_deduction, first_refund.main_pending_deduction), (500, 0))
                self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(refund=first_refund).exists())
            self.login(self.admin)
            addon_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
                'order_id': order_id, 'affected_provider_account_ids': [second_account.pk],
                'reason': '加购未履约', 'client_request_id': 'after-main-addon-case'}, format='json'))
            refund_url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/'
            addon_proposal = self.ok(self.client.post(refund_url, {'request_id': 'after-main-addon-refund',
                'amount_raw': 500 if split_addon_refund or addon_after_partial else 1000,
                'case_id': addon_case['id'], 'explanation': '加购退款'}, format='json'))
            decision = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{addon_proposal["id"]}/'
            if addon_after_partial:
                self.login(self.provider)
                self.assertNotEqual(self.client.post(decision, {'accepted': True}, format='json').status_code, 200)
                self.login(self.boss)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
                self.login(self.admin)
                self.assertNotEqual(self.client.post(refund_url + f'{addon_proposal["id"]}/execute/', {}, format='json').status_code, 200)
            for user in (self.boss, second):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
            self.login(self.admin)
            execute = refund_url + f'{addon_proposal["id"]}/execute/'
            from orders.models import OrderAddonRefund, OrderAddonFundingReleaseRun
            failing_path = ('orders.ordinary_addon_after_main_release.plan_release' if addon_after_partial and not addon_after_partial_gift else
                            'orders.ordinary_addon_after_main_release.OrderAddonFundingRelease.objects.bulk_create')
            with patch(failing_path,
                       side_effect=RuntimeError('addon release write failed')):
                with self.assertRaisesMessage(RuntimeError, 'addon release write failed'):
                    self.client.post(execute, {}, format='json')
            self.assertEqual(OrderAddonRefund.objects.filter(payment_id=addon_payment).count(), 1 if split_addon_refund else 0)
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, 22500 if split_addon_refund else 22000)
            if concurrent_addon_refund:
                from concurrent.futures import ThreadPoolExecutor
                from threading import Barrier
                from django.db import close_old_connections
                from rest_framework.test import APIClient
                from club_accounts.tokens import issue_account_tokens
                tenant = current_store() or resolve_store()
                with store_context(tenant):
                    token = issue_account_tokens(self.accounts[self.admin.pk])[0]
                barrier = Barrier(2)
                def submit_addon(_):
                    close_old_connections()
                    try:
                        with store_context(tenant):
                            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                            barrier.wait(timeout=20)
                            response = client.post(execute, {}, format='json')
                            return response.status_code, response.data
                    finally:
                        close_old_connections()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(submit_addon, range(2)))
                self.assertEqual([row[0] for row in results], [200, 200], results)
            self.ok(self.client.post(execute, {}, format='json'))
            self.ok(self.client.post(execute, {}, format='json'))
            addon_refund_row = OrderAddonRefund.objects.get(proposal_id=addon_proposal['id'])
            if addon_after_partial:
                from stores.db import transaction
                with transaction.atomic():
                    repeated = self.ok(self.client.post(refund_url, {'request_id': 'second-addon-proposal',
                        'amount_raw': 100, 'case_id': addon_case['id'], 'explanation': '禁止追加退款'}, format='json'))
                    again = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{repeated["id"]}/'
                    for user in (self.boss, second):
                        self.login(user)
                        self.ok(self.client.post(again, {'accepted': True}, format='json'))
                    self.login(self.admin)
                    self.assertNotEqual(self.client.post(refund_url + f'{repeated["id"]}/execute/', {}, format='json').status_code, 200)
                    transaction.set_rollback(True)
                self.assertEqual(OrderAddonRefund.objects.filter(payment_id=addon_payment).count(), 1)
            self.assertEqual((addon_refund_row.amount, addon_refund_row.addon_pending_deduction,
                              addon_refund_row.main_pending_deduction),
                             (500, 500, 0) if addon_after_partial else (500, 100, 400) if split_addon_refund else (1000, 600, 400))
            if not split_addon_refund:
                from orders.models import OrderAddonFundingAllocation
                self.assertEqual(dict(Order.objects.get(pk=order_id).funding_allocations.values_list(
                    'kind', 'refunded_amount')), {'PREPAID': 1000, 'GIFT': 0})
                self.assertEqual(dict(OrderAddonFundingAllocation.objects.filter(payment_id=addon_payment).values_list(
                    'kind', 'refunded_amount')), {'GIFT': 500} if addon_after_partial_gift else {'PREPAID': 500, 'GIFT': 0 if addon_after_partial else 500})
            addon_release = OrderAddonFundingReleaseRun.objects.filter(refund=addon_refund_row).first()
            if addon_after_partial and not addon_after_partial_gift:
                self.assertIsNone(addon_release)
            else:
                self.assertEqual((addon_release.allocated_before, addon_release.required_amount,
                                  addon_release.released_amount), (1300, 800, 500) if addon_after_partial_gift else (800, 300, 500))
            self.ok(self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/resolve/',
                                     {'resolution': '加购退款完成'}, format='json'))
            from orders.addon_funding_release import verify_run
            from rest_framework.exceptions import ValidationError
            from stores.db import transaction
            if addon_release:
                with store_context(current_store() or resolve_store()), transaction.atomic():
                    addon_release.ordinary_refund_hash = 'invalid'
                    addon_release.save(update_fields=['ordinary_refund_hash'])
                    with self.assertRaises(ValidationError):
                        verify_run(addon_release)
                    transaction.set_rollback(True)
        if pending_other_addon_refund:
            self.login(self.admin)
            second_url = f'/api/admin/orders/{order_id}/addons/payments/{second_payment}/handovers/'
            change = self.ok(self.client.post(second_url, {'request_id': 'other-addon-handover',
                'target_provider_account_id': second_account.pk, 'retained_basis_raw': 400,
                'completed_work': '原陪玩已完成部分', 'remaining_work': '接替剩余部分'}, format='json'))
            addon_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
                'order_id': order_id, 'affected_provider_account_ids': [second_account.pk],
                'reason': '独立加购未履约', 'client_request_id': 'independent-addon-case'}, format='json'))
            refund_url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/'
            proposal = self.ok(self.client.post(refund_url, {'request_id': 'independent-addon-refund',
                'amount_raw': 1000, 'case_id': addon_case['id'], 'explanation': '独立加购退款'}, format='json'))
            decision = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{proposal["id"]}/'
            for user in (self.boss, second):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(refund_url + f'{proposal["id"]}/execute/', {}, format='json'))
            from orders.models import OrderAddonRefund, OrderAddonFundingReleaseRun
            refund = OrderAddonRefund.objects.get(payment_id=addon_payment)
            self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction), (600, 400))
            release = OrderAddonFundingReleaseRun.objects.get(refund=refund)
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (600, 100, 500))
            self.ok(self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/resolve/',
                                     {'resolution': '独立加购退款完成'}, format='json'))
            handover_decision = f'/api/orders/orders/{order_id}/addons/payments/{second_payment}/handovers/{change["id"]}/decision/'
            for user in (self.boss, self.provider, second):
                self.login(user)
                self.ok(self.client.post(handover_decision, {'accepted': True}, format='json'))
            release = OrderAddonFundingReleaseRun.objects.get(handover_id=change['id'])
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (100, 0, 100))
        if completion:
            staff_url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/completion/'
            payload = {'request_id': 'addon-completed', 'completed_work': '原陪玩已完成全部加购服务'}
            item = self.ok(self.client.post(staff_url, payload, format='json'))
            self.assertEqual(item['completed_basis_raw'], 1000 - addon_refund)
            self.assertEqual(self.ok(self.client.post(staff_url, payload, format='json'))['id'], item['id'])
            self.assertNotEqual(self.client.post(staff_url, {**payload, 'completed_work': '不同确认内容'}, format='json').status_code, 200)
            decision_url = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/completion/{item["id"]}/decision/'
            if second:
                self.login(second)
                self.assertEqual(self.client.post(decision_url, {'accepted': True}, format='json').status_code, 404)
                self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/completion/')), [])
            self.login(self.boss)
            if reject_completion:
                self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': False}, format='json'))['status'], 'REJECTED')
                self.login(self.provider)
                self.assertNotEqual(self.client.post(decision_url, {'accepted': True}, format='json').status_code, 200)
                self.login(self.admin)
                item = self.ok(self.client.post(staff_url, {**payload, 'request_id': 'addon-completed-reissued'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/completion/{item["id"]}/decision/'
                self.login(self.boss)
            alias = (current_store() or resolve_store()).database_alias
            if concurrent_completion:
                self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))['status'], 'PENDING')
            else:
                with patch('engagement.confirmation.notify_order_update') as notification:
                    with self.captureOnCommitCallbacks(using=alias, execute=True):
                        self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))['status'], 'PENDING')
                    self.assertEqual(notification.call_args.args[0].pk, order_id)
            from orders.points_funding_plan import PointsFundingPlanError
            with store_context(current_store() or resolve_store()), self.assertRaises(PointsFundingPlanError):
                verify_order_funding(order_id)
            if completion_pending_handover:
                self.login(self.admin)
                url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/handovers/'
                change = self.ok(self.client.post(url, {'request_id': 'pending-change',
                    'target_provider_account_id': second_account.pk, 'retained_basis_raw': 400,
                    'completed_work': '部分完成', 'remaining_work': '待接替服务'}, format='json'))
                self.login(self.provider)
                self.assertNotEqual(self.client.post(decision_url, {'accepted': True}, format='json').status_code, 200)
                reject_url = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/handovers/{change["id"]}/decision/'
                self.assertEqual(self.ok(self.client.post(reject_url, {'accepted': False}, format='json'))['status'], 'REJECTED')
            self.login(self.provider)
            if concurrent_completion:
                from concurrent.futures import ThreadPoolExecutor
                from threading import Barrier
                from django.db import close_old_connections
                from rest_framework.test import APIClient
                from club_accounts.tokens import issue_account_tokens
                tenant_for_threads = current_store() or resolve_store()
                with store_context(tenant_for_threads):
                    token = issue_account_tokens(self.accounts[self.provider.pk])[0]
                barrier = Barrier(2)

                def submit():
                    close_old_connections()
                    try:
                        with store_context(tenant_for_threads):
                            client = APIClient(HTTP_HOST='sl.esports.club')
                            barrier.wait(timeout=20)
                            response = client.post(decision_url, {'accepted': True}, format='json',
                                                   HTTP_AUTHORIZATION='Bearer ' + token)
                            return response.status_code, response.data
                    finally:
                        close_old_connections()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: submit(), range(2)))
                self.assertEqual([row[0] for row in results], [200, 200], results)
                self.assertEqual([row[1]['data']['status'] for row in results], ['ACCEPTED', 'ACCEPTED'])
            else:
                self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))['status'], 'ACCEPTED')
            self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))['status'], 'ACCEPTED')
            from stores.db import transaction
            from orders.models import OrderAddonCompletion
            with store_context(current_store() or resolve_store()), transaction.atomic():
                OrderAddonCompletion.objects.filter(pk=item['id']).update(completed_work='被篡改的内容')
                with self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(order_id)
                transaction.set_rollback(True)
            with store_context(current_store() or resolve_store()), transaction.atomic():
                from console.models import AdminAuditLog
                AdminAuditLog.objects.filter(resource='addon-completion', object_id=str(item['id'])).delete()
                with self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(order_id)
                transaction.set_rollback(True)
        if handover:
            url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/handovers/'
            handover_payload = {'request_id': 'zero-handover',
                'target_provider_account_id': second_account.pk, 'retained_basis_raw': 400,
                'completed_work': '原陪玩已完成部分', 'remaining_work': '接替剩余部分'}
            if not zero:
                from stores.db import transaction
                from orders.models import OrderAddonHandover, OrderSegmentFinancialSnapshot
                with transaction.atomic():
                    snapshot = OrderSegmentFinancialSnapshot.objects.get(
                        segment__attribution__order_id=order_id, segment__provider_account_id=second_account.pk)
                    snapshot.commission_rate += 1
                    snapshot.save(update_fields=['commission_rate'])
                    self.assertNotEqual(self.client.post(url, handover_payload, format='json').status_code, 200)
                    self.assertFalse(OrderAddonHandover.objects.filter(payment_id=addon_payment).exists())
                    transaction.set_rollback(True)
            handover_response = self.client.post(url, handover_payload, format='json')
            if handover_response.status_code != 200:
                from orders.models import OrderParticipationSegment
                self.fail(f'{handover_response.data}; order={Order.objects.get(pk=order_id).status}; '
                    f'providers={list(OrderProvider.objects.filter(order_id=order_id).values("provider_account_id", "released_at"))}; '
                    f'segments={list(OrderParticipationSegment.objects.filter(attribution__order_id=order_id).values("provider_account_id", "released_at", "started_at", "attribution__status"))}')
            change = self.ok(handover_response)
            if multiple_addons and not mixed_addons:
                second_url = f'/api/admin/orders/{order_id}/addons/payments/{second_payment}/handovers/'
                second_change = self.ok(self.client.post(second_url,
                    {**handover_payload, 'request_id': 'second-addon-handover'}, format='json'))
            if mixed_addons:
                completion_url = f'/api/admin/orders/{order_id}/addons/payments/{second_payment}/completion/'
                completed = self.ok(self.client.post(completion_url, {'request_id': 'second-addon-completed',
                    'completed_work': '原陪玩已完成全部加购服务'}, format='json'))
                confirm = f'/api/orders/orders/{order_id}/addons/payments/{second_payment}/completion/{completed["id"]}/decision/'
                self.login(self.boss)
                self.ok(self.client.post(confirm, {'accepted': True}, format='json'))
                second_url = f'/api/admin/orders/{order_id}/addons/payments/{second_payment}/handovers/'
                second_payload = {**handover_payload, 'request_id': 'completed-addon-handover'}
                if race_completion_handover:
                    from concurrent.futures import ThreadPoolExecutor
                    from threading import Barrier
                    from django.db import close_old_connections
                    from rest_framework.test import APIClient
                    from club_accounts.tokens import issue_account_tokens
                    tenant_for_threads = current_store() or resolve_store()
                    with store_context(tenant_for_threads):
                        provider_token = issue_account_tokens(self.accounts[self.provider.pk])[0]
                        staff_token = issue_account_tokens(self.accounts[self.admin.pk])[0]
                    barrier = Barrier(2)

                    def submit(kind):
                        close_old_connections()
                        try:
                            with store_context(tenant_for_threads):
                                client = APIClient(HTTP_HOST='sl.esports.club' if kind == 'complete' else 'admin.esports.club')
                                barrier.wait(timeout=20)
                                response = client.post(confirm if kind == 'complete' else second_url,
                                    {'accepted': True} if kind == 'complete' else second_payload, format='json',
                                    HTTP_AUTHORIZATION='Bearer ' + (provider_token if kind == 'complete' else staff_token))
                                return response.status_code, response.data
                        finally:
                            close_old_connections()

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        results = list(pool.map(submit, ('complete', 'handover')))
                    self.assertEqual(sorted(result[0] for result in results), [200, 400], results)
                    if results[1][0] == 200:
                        pending = results[1][1]['data']['id']
                        self.login(self.provider)
                        rejection = (f'/api/orders/orders/{order_id}/addons/payments/{second_payment}/'
                                     f'handovers/{pending}/decision/')
                        self.ok(self.client.post(rejection, {'accepted': False}, format='json'))
                        self.ok(self.client.post(confirm, {'accepted': True}, format='json'))
                else:
                    self.login(self.provider)
                    self.ok(self.client.post(confirm, {'accepted': True}, format='json'))
                self.login(self.admin)
                if race_completion_handover and results[1][0] == 200:
                    second_payload = {**second_payload, 'request_id': 'completed-addon-handover-retry'}
                invalid = self.client.post(second_url, second_payload, format='json')
                self.assertEqual(invalid.status_code, 400, invalid.data)
                self.assertIn('已确认全部完成', str(invalid.data))
                from orders.models import OrderAddonHandover
                self.assertFalse(OrderAddonHandover.objects.filter(payment_id=second_payment, status='PENDING').exists())
            decision_url = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/handovers/{change["id"]}/decision/'
            for user in (self.boss, self.provider, second):
                self.login(user)
                if preallocated and not increasing and not multiple_addons and not handover_addon_refund and user == second:
                    from orders.models import OrderAddonFundingReleaseRun, OrderAddonHandoverDecision
                    with patch('orders.zero_addon_handover_release.OrderAddonFundingRelease.objects.bulk_create',
                               side_effect=RuntimeError('release write failed')):
                        with self.assertRaisesMessage(RuntimeError, 'release write failed'):
                            self.client.post(decision_url, {'accepted': True}, format='json')
                    self.assertIsNone(OrderAddonHandoverDecision.objects.get(
                        handover_id=change['id'], account=second_account).accepted)
                    self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(handover_id=change['id']).exists())
                response = self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))
                if user != second:
                    self.assertEqual(response['status'], 'PENDING')
                    from orders.points_funding_plan import PointsFundingPlanError
                    with store_context(current_store() or resolve_store()), self.assertRaises(PointsFundingPlanError):
                        verify_order_funding(order_id)
            self.assertEqual(response['status'], 'ACCEPTED')
            if preallocated:
                self.assertEqual(self.ok(self.client.post(decision_url, {'accepted': True}, format='json'))['status'], 'ACCEPTED')
            if multiple_addons:
                from orders.models import OrderAddonFundingReleaseRun
                first_release = OrderAddonFundingReleaseRun.objects.get(handover_id=change['id'])
                self.assertEqual((first_release.allocated_before, first_release.required_amount, first_release.released_amount),
                                 (300, 180, 120))
            if multiple_addons and not mixed_addons:
                from orders.points_funding_plan import PointsFundingPlanError
                with store_context(current_store() or resolve_store()), self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(order_id)
                second_decision = (f'/api/orders/orders/{order_id}/addons/payments/{second_payment}/'
                                   f'handovers/{second_change["id"]}/decision/')
                for user in (self.boss, self.provider, second):
                    self.login(user)
                    self.ok(self.client.post(second_decision, {'accepted': True}, format='json'))
                second_release = OrderAddonFundingReleaseRun.objects.get(handover_id=second_change['id'])
                self.assertEqual((second_release.allocated_before, second_release.required_amount, second_release.released_amount),
                                 (180, 60, 120))
            if handover_addon_refund:
                self.login(self.admin)
                addon_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {
                    'order_id': order_id, 'affected_provider_account_ids': [second_account.pk],
                    'reason': '接替加购未履约', 'client_request_id': 'after-handover-addon-case'}, format='json'))
                refund_url = f'/api/admin/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/'
                proposal = self.ok(self.client.post(refund_url, {'request_id': 'after-handover-addon-refund',
                    'amount_raw': 600, 'case_id': addon_case['id'], 'explanation': '接替加购退款',
                    'affected_provider_account_ids': [second_account.pk]}, format='json'))
                refund_decision = f'/api/orders/orders/{order_id}/addons/payments/{addon_payment}/refund-proposals/{proposal["id"]}/'
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(refund_decision, {'accepted': True}, format='json'))
                self.login(self.admin)
                execute = refund_url + f'{proposal["id"]}/execute/'
                from orders.models import OrderAddonRefund, OrderAddonFundingReleaseRun
                with patch('orders.ordinary_addon_after_main_release.OrderAddonFundingRelease.objects.bulk_create',
                           side_effect=RuntimeError('handover addon release failed')):
                    with self.assertRaisesMessage(RuntimeError, 'handover addon release failed'):
                        self.client.post(execute, {}, format='json')
                self.assertFalse(OrderAddonRefund.objects.filter(payment_id=addon_payment).exists())
                self.assertEqual(get_wallet(user=self.boss, account=boss).balance, 22000)
                self.ok(self.client.post(execute, {}, format='json'))
                self.ok(self.client.post(execute, {}, format='json'))
                addon_refund_row = OrderAddonRefund.objects.get(payment_id=addon_payment)
                self.assertEqual((addon_refund_row.amount, addon_refund_row.addon_pending_deduction,
                                  addon_refund_row.main_pending_deduction), (600, 360, 240))
                addon_release = OrderAddonFundingReleaseRun.objects.get(refund=addon_refund_row)
                self.assertEqual((addon_release.allocated_before, addon_release.required_amount,
                                  addon_release.released_amount), (880, 780, 100))
                self.ok(self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/resolve/',
                                         {'resolution': '加购退款完成'}, format='json'))
        if achievement_vip_disabled:
            from engagement.models import ClubOperationConfig
            ClubOperationConfig.objects.filter(pk=1).update(vip_enabled=False)
        if second:
            self.login(second)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image': flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider_account=account)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        if second:
            second_report = ProviderReport.objects.get(order_id=order_id, provider_account=second_account)
            self.login(second)
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{second_report.pk}/approve/', {}, format='json'))
            report.refresh_from_db()
            second_report.refresh_from_db()
            second_report.submitted_at = report.submitted_at + timedelta(hours=3)
            second_report.audited_at = second_report.submitted_at + timedelta(seconds=1)
            second_report.save(update_fields=['submitted_at', 'audited_at'])

        if achievement_vip_disabled:
            self.login(self.boss)
            before = {row['code']: row['current'] for row in self.ok(self.client.get('/api/users/achievements/'))}
            self.assertEqual((before['confirmed-order-test'], before['confirmed-amount-test']), (0, 0))

        tenant = current_store() or resolve_store()
        with store_context(tenant):
            if addon_on_affected and not handover and not completion:
                from orders.points_funding_plan import PointsFundingPlanError
                from orders.settlement import SettlementError
                with self.assertRaisesMessage(PointsFundingPlanError, '终止陪玩仍负责加购'):
                    verify_order_funding(order_id)
                report.refresh_from_db()
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    with self.assertRaises(SettlementError):
                        process_order(order_id)
                self.assertFalse(Order.objects.get(pk=order_id).settled_at)
                self.assertEqual(get_wallet(user=self.provider, account=account).balance, 0)
                return
            funding = verify_order_funding(order_id)
            if refund_preallocated and not addon:
                self.assertEqual((funding.required, funding.allocated, funding.remaining), (800, 800, 0))
                report.refresh_from_db()
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=77)):
                    process_order(order_id)
                    process_order(order_id)
                    self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
                    self.assertEqual(get_wallet(user=self.provider, account=account).balance, income - 1000)
                    self.assertTrue(verify_order_funding(order_id, allow_finalized=True).fully_funded)
                return
            if refund_preallocated:
                self.assertEqual((funding.required, funding.allocated, funding.remaining),
                                 (0, 0, 0) if pending_other_addon_refund else (780, 780, 0) if handover_addon_refund else ((800, 800, 0) if addon_after_partial else (300, 300, 0)) if addon_after_main else (180, 180, 0) if mixed_addons else (60, 60, 0) if multiple_addons else (380, 380, 0) if handover else (600, 600, 0))
                if handover:
                    from orders.addon_funding_release import verify_run
                    from stores.db import transaction
                    release = OrderAddonFundingReleaseRun.objects.get(handover_id=change['id'])
                    self.assertEqual((release.allocated_before, release.required_amount, release.released_amount),
                                     (1000, 880, 120) if handover_addon_refund else (300, 180, 120) if multiple_addons else (500, 380, 120))
                    with transaction.atomic():
                        verify_run(release)
                report.refresh_from_db()
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=77)):
                    process_order(order_id)
                    process_order(order_id)
                    self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
                    self.assertEqual(get_wallet(user=self.provider, account=account).balance,
                                     7000 if addon and not addon_on_affected else 7320 if pending_other_addon_refund else 7000 if addon_after_main else 8120 if mixed_addons else 7640 if multiple_addons else 7320 if handover else 7800)
                    if second:
                        self.assertEqual(get_wallet(user=second, account=second_account).balance,
                                         (6100 if addon_after_partial else 5600) if addon_after_main else 8800 if addon and not addon_on_affected else 5960 if pending_other_addon_refund else 5760 if handover_addon_refund else 6360 if mixed_addons else 6720 if multiple_addons else 6360 if handover else 8000)
                    self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 1800 if not second else 5700 if handover or addon_after_main or pending_other_addon_refund else 3800)
                    if multiple_addons and not pending_other_addon_refund:
                        from orders.models import OrderAddonSettlement
                        self.assertEqual(sorted(OrderAddonSettlement.objects.filter(payment_id=addon_payment)
                                                .values_list('amount', flat=True)), [320, 360])
                        self.assertEqual(sorted(OrderAddonSettlement.objects.filter(payment_id=second_payment)
                                                .values_list('amount', flat=True)), [800] if mixed_addons else [320, 360])
                    self.assertTrue(verify_order_funding(order_id, allow_finalized=True).fully_funded)
                    if achievement_vip_disabled:
                        item = Order.objects.get(pk=order_id)
                        self.assertIsNotNone(item.funding_benefits_resolved_at)
                        self.assertIsNone(item.consumption_confirmed_at)
                        self.login(self.boss)
                        achievements = self.ok(self.client.get('/api/users/achievements/'))
                        found = {row['code']: row for row in achievements}
                        self.assertEqual((found['confirmed-order-test']['current'],
                                          found['confirmed-order-test']['unlocked']), (1, True))
                        self.assertEqual((found['confirmed-amount-test']['current'],
                                          found['confirmed-amount-test']['unlocked']), (20000, True))
                        member = self.ok(self.client.get('/api/club/me/'))
                        titles = {row['title'] for row in member['tags']}
                        self.assertTrue({'实销订单', '实销金额'}.issubset(titles))
                        # Exercise the aggregate boundary with the actual
                        # confirmed add-on ledger while rolling back the
                        # simulated full-main-refund status afterwards.
                        from stores.db import transaction
                        with transaction.atomic():
                            Order.objects.filter(pk=order_id).update(payment_status=Order.PaymentStatus.REFUNDED)
                            from engagement.achievement_metrics import confirmed_service_metrics
                            self.assertEqual(confirmed_service_metrics(boss), {'amount': 1000, 'count': 1})
                            transaction.set_rollback(True)
                return
            if preallocated:
                from orders.addon_funding_release import verify_run
                from orders.models import OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
                from stores.db import transaction
                if increasing:
                    self.assertEqual((funding.required, funding.allocated, funding.remaining), (860, 800, 60))
                    self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(handover_id=change['id']).exists())
                else:
                    self.assertEqual((funding.required, funding.allocated, funding.remaining), (680, 680, 0))
                    release = OrderAddonFundingReleaseRun.objects.get(handover_id=change['id'])
                    self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (800, 680, 120))
                    with transaction.atomic():
                        verify_run(release)
                    with transaction.atomic():
                        from rest_framework.exceptions import ValidationError
                        release.ordinary_refund_hash = 'invalid'
                        release.save(update_fields=['ordinary_refund_hash'])
                        with self.assertRaises(ValidationError):
                            verify_run(release)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        from orders.models import OrderAddonHandoverShare
                        shares = list(OrderAddonHandoverShare.objects.filter(handover_id=change['id']).order_by('position'))
                        shares[0].income += 1
                        shares[1].income -= 1
                        for share in shares:
                            share.save(update_fields=['income'])
                        release.refresh_from_db()
                        with self.assertRaises(ValidationError):
                            verify_run(release)
                        transaction.set_rollback(True)
                self.assertEqual(OrderPointsFundingAllocation.objects.filter(service_order_id=order_id).count(), 1)
                report.refresh_from_db()
                later = report.submitted_at + timedelta(hours=77)
                if increasing:
                    with patch('django.utils.timezone.now', return_value=later):
                        process_order(order_id)
                        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
                        self.assertEqual(get_wallet(user=self.provider, account=account).balance, 0)
                    allocation = finance.client.post(allocation_url, {'request_id': str(uuid4()),
                        'order_id': order_id, 'amount': 60}, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
                    self.assertEqual(allocation.status_code, 200, allocation.data)
                with patch('django.utils.timezone.now', return_value=later):
                    process_order(order_id)
                    process_order(order_id)
                    self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
                    self.assertEqual(get_wallet(user=self.provider, account=account).balance, 8320)
                    self.assertEqual(get_wallet(user=second, account=second_account).balance, 9540 if increasing else 6360)
                    self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 3000 if increasing else 6000)
                    self.assertTrue(verify_order_funding(order_id, allow_finalized=True).fully_funded)
                return
            if principal_only:
                from engagement.models import DefaultPaymentSource
                item = Order.objects.get(pk=order_id)
                self.assertEqual((funding.required, funding.allocated, funding.remaining), (0, 0, 0))
                self.assertEqual(list(item.funding_allocations.values_list('kind', flat=True)), ['PREPAID'])
                self.assertFalse(DefaultPaymentSource.objects.filter(snapshot__order=item).exists())
                self.assertEqual(item.refund_records.count(), 1)
                report.refresh_from_db()
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    process_order(order_id)
                    process_order(order_id)
                item.refresh_from_db()
                self.assertIsNotNone(item.settled_at)
                self.assertIsNotNone(item.consumption_confirmed_at)
                self.assertEqual(get_wallet(user=self.provider, account=account).balance, income - 1000)
                return
            required = (800 if addon else 1000) if zero else (600 if addon else 800)
            self.assertEqual((funding.required, funding.allocated, funding.remaining), (required, 0, required))
            from orders.points_funding_plan import PointsFundingPlanError
            if zero:
                from console.models import AdminAuditLog
                from stores.db import transaction
                with transaction.atomic():
                    AdminAuditLog.objects.filter(path=f"/api/admin/aftersales/{case['id']}/negotiation-execution/").delete()
                    with self.assertRaises(PointsFundingPlanError):
                        verify_order_funding(order_id)
                    transaction.set_rollback(True)
            flag = ('ADDON_COMPLETION_INTERNAL_TESTING' if completion and not zero else
                    'ORDINARY_REFUNDED_ADDON_HANDOVER_FUNDING_INTERNAL_TESTING' if handover and not zero else
                    'ORDINARY_ZERO_ADDON_FUNDING_INTERNAL_TESTING' if addon
                    else 'ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING')
            from orders.points_checkout_test import ordinary_refund_production_order
            from orders.ordinary_addon_production import eligible as ordinary_addon_eligible
            current_order = Order.objects.get(pk=order_id)
            if not (ordinary_refund_production_order(current_order) or ordinary_addon_eligible(current_order)):
                with override_settings(**{flag: False}):
                    with self.assertRaises(PointsFundingPlanError):
                        verify_order_funding(order_id)
            report.refresh_from_db()
            later = report.submitted_at + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=later):
                process_order(order_id)
            self.assertEqual(get_wallet(user=self.provider, account=account).balance, 0)
            self.assertIsNone(Order.objects.get(pk=order_id).consumption_confirmed_at)

        finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
        if tenant.slug == 'xinan':
            with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                finance.setUp()
        else:
            finance.setUp()
        slug = tenant.slug
        queue_url = f'/api/admin/platform/points-funding/{slug}/orders'
        queue = finance.client.get(queue_url, HTTP_AUTHORIZATION='Bearer ' + finance.token)
        self.assertEqual(queue.status_code, 200, queue.data)
        item = next(row for row in queue.data['data']['list'] if row['order_id'] == order_id)
        self.assertEqual((item['funding_status'], item['remaining_units']), ('NEEDS_FUNDING', required))
        receipt = finance.post(slug, {**finance.data, 'amount': 1000})
        self.assertEqual(receipt.status_code, 200, receipt.data)
        claim = receipt.data['data']['claim_id']
        verified = finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=slug)
        self.assertEqual(verified.status_code, 200, verified.data)
        payload = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 300}
        url = f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation'
        allocated = finance.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
        self.assertEqual(allocated.status_code, 200, allocated.data)
        retry = finance.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
        self.assertEqual(retry.status_code, 200, retry.data)
        with store_context(tenant), patch('django.utils.timezone.now', return_value=later):
            self.assertEqual(verify_order_funding(order_id).remaining, required - 300)
            process_order(order_id)
            self.assertEqual(get_wallet(user=self.provider, account=account).balance, 0)
        payload = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': required - 300}
        allocated = finance.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + finance.token)
        self.assertEqual(allocated.status_code, 200, allocated.data)
        with store_context(tenant), patch('django.utils.timezone.now', return_value=later):
            self.assertEqual(verify_order_funding(order_id).remaining, 0)
            process_order(order_id)
            process_order(order_id)
            self.assertEqual(get_wallet(user=self.provider, account=account).balance,
                             income - (0 if zero else 1000) +
                             (320 if handover else max(800 - addon_refund, 0) if completion else 0))
            if second:
                self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
                self.assertIsNone(Order.objects.get(pk=order_id).consumption_confirmed_at)
                self.assertEqual(get_wallet(user=second, account=second_account).balance, 0)
                self.assertTrue(verify_order_funding(order_id).fully_funded)
                remaining_queue = finance.client.get(queue_url, HTTP_AUTHORIZATION='Bearer ' + finance.token)
                self.assertEqual(remaining_queue.status_code, 200, remaining_queue.data)
                self.assertNotIn(order_id, [row['order_id'] for row in remaining_queue.data['data']['list']])
                with patch('django.utils.timezone.now', return_value=later + timedelta(hours=4)):
                    process_order(order_id)
                addon_income = (480 if handover else 0 if completion else max(800 - addon_refund, 0)) if addon else 0
                main_deduction = max(addon_refund - 800, 0)
                self.assertEqual(get_wallet(user=second, account=second_account).balance, income + addon_income - main_deduction)
                if addon:
                    from orders.models import OrderAddonConsumption, OrderAddonSettlement
                    if completion:
                        self.assertEqual(OrderAddonSettlement.objects.get(payment_id=addon_payment).amount, 800 - addon_refund)
                    elif addon_income:
                        settled_addons = OrderAddonSettlement.objects.filter(payment_id=addon_payment).order_by('segment_id')
                        self.assertEqual(sorted(settled_addons.values_list('amount', flat=True)),
                                         [320, 480] if handover else [addon_income])
                    else:
                        self.assertFalse(OrderAddonSettlement.objects.filter(payment_id=addon_payment).exists())
                    addon_consumption = OrderAddonConsumption.objects.get(payment_id=addon_payment)
                    self.assertEqual((addon_consumption.paid_units, addon_consumption.principal_units,
                                      addon_consumption.refunded_units, addon_consumption.refunded_principal_units),
                                     (1000, 1000, addon_refund, addon_refund))
            if completion and not second:
                from orders.models import OrderAddonConsumption, OrderAddonSettlement
                self.assertEqual(OrderAddonSettlement.objects.get(payment_id=addon_payment).amount, 800)
                self.assertTrue(OrderAddonConsumption.objects.filter(payment_id=addon_payment).exists())
            self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
            from engagement.models import ConsumptionConfirmation
            confirmation = ConsumptionConfirmation.objects.get(order_id=order_id)
            self.assertEqual((confirmation.amount, confirmation.refunded_amount),
                             (20000 if double else 10000, 0 if zero else 1000))
            expected_referral = (4000 if zero else 3800) if double else (2000 if zero else 1800)
            self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, expected_referral)
            with patch('django.utils.timezone.now', return_value=later + timedelta(hours=4)):
                self.assertTrue(verify_order_funding(order_id, allow_finalized=True).fully_funded)


@override_settings(**OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
class AddonCompletionMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    exercise_refund_funding = OrdinaryRefundWithoutAddonFundingTest.exercise_refund_funding

    def test_concurrent_provider_confirmation_is_idempotent(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_refund_funding(double=True, zero=True, addon=True, addon_on_affected=True,
                                         completion=True, concurrent_completion=True)

    def test_completed_addon_and_handover_are_serialized(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_refund_funding(double=True, addon=True, addon_on_affected=True, handover=True,
                preallocated=True, refund_preallocated=True, multiple_addons=True, mixed_addons=True,
                race_completion_handover=True)


@override_settings(**OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
class OrdinaryRefundProductionMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    exercise_refund_funding = OrdinaryRefundWithoutAddonFundingTest.exercise_refund_funding
    exercise_full_refund = OrdinaryRefundWithoutAddonFundingTest.exercise_full_refund

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False,
        ORDINARY_FULL_REFUND_ZERO_SETTLEMENT_INTERNAL_TESTING=False)
    def test_concurrent_full_refund_and_funding_release(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_full_refund(preallocated=True, concurrent=True)

    @override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
        SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_NO_ADDON_FUNDING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
        ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False)
    def test_concurrent_same_refund_and_funding_release(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('Requires isolated MySQL')
        with patch('engagement.confirmation.notify_order_update'):
            self.exercise_refund_funding(refund_preallocated=True, concurrent_refund=True)
