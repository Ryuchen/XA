from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile, PointLedger
from orders.models import (AfterSale, Order, OrderApplication, OrderPointsFundingAction, OrderPointsFundingAllocation,
                           OrderPointsSettlement, OrderPointsSettlementFunding, OrderProvider, PointsNegotiationTerms)
from orders.points_negotiation_preview import funding_after_refund, preview_locked
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_points_checkout_integration import PointsFundingPlatformRecoveryTests
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction
from wallet.models import Transaction
from wallet.services import get_wallet


class RefundFundingCalculationTests(TestCase):
    def test_followup_before_any_payout_keeps_partial_funding_and_release_separate(self):
        from dataclasses import replace
        from orders.points_funding_allocation import PointsFundingAllocationError
        from orders.points_funding_revision import plan_unpaid_funding_revision
        # After the first refund the order needs 170, but only 50 has arrived.
        # A second 100 refund lowers its requirement to 140, leaving 90 unfunded.
        projected = funding_after_refund(principal_units=600, gift_units=400, compensation_units=0,
            returned_prepaid_units=140, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=800, pending_deduction_units=200, provider_external_units=0,
            inviter_rate=0, allocated_units=50)
        result = plan_unpaid_funding_revision(allocations=[(11, 21, 50, 0)], previous_required=170,
                                              original_reservation=None, projected=projected)
        self.assertEqual((result.paid_provider_units, result.remaining_provider_units, result.allocated_units,
                          result.required_units, result.release_units, result.shortfall_units), (0, 600, 50, 140, 0, 90))
        # If 160 was already assigned, return only 20 to that verified receipt.
        projected = replace(projected, allocated_units=160, release_units=20, remaining_units=0)
        released = plan_unpaid_funding_revision(allocations=[(11, 21, 160, 0)], previous_required=170,
                                                original_reservation=None, projected=projected)
        self.assertEqual(released.releases, ((11, 21, 20),))
        for changes in ({'previous_required': 159}, {'original_reservation': [(11, 160)]},
                        {'projected': replace(projected, store_income_units=1)},
                        {'allocations': [(11, 21, 160, 161)]}):
            kwargs = dict(allocations=[(11, 21, 160, 0)], previous_required=170,
                          original_reservation=None, projected=projected)
            with self.subTest(changes=changes), self.assertRaises(PointsFundingAllocationError):
                plan_unpaid_funding_revision(**{**kwargs, **changes})
        # A reservation is valid only if the first order was fully funded.
        reserved = replace(projected, allocated_units=170, release_units=30)
        self.assertEqual(plan_unpaid_funding_revision(allocations=[(11, 21, 170, 0)], previous_required=170,
            original_reservation=[(11, 170)], projected=reserved).releases, ((11, 21, 30),))

    def test_followup_funding_revision_preserves_paid_income_and_receipt_releases(self):
        from dataclasses import replace
        from orders.points_funding_allocation import PointsFundingAllocationError
        from orders.points_funding_revision import plan_funding_revision
        projected = funding_after_refund(principal_units=11000, gift_units=0, compensation_units=0,
            returned_prepaid_units=1090, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=11250, pending_deduction_units=1500, provider_external_units=0,
            inviter_rate=0, allocated_units=110)
        data = {'allocations': [(11, 21, 80, 50), (12, 22, 80, 0)],
                'original_reservation': [(11, 30), (12, 80)], 'previous_required': 110,
                'paid_provider_units': 5500, 'projected': projected}
        result = plan_funding_revision(**data)
        self.assertEqual((result.paid_provider_units, result.remaining_provider_units,
                          result.release_units, result.shortfall_units), (5500, 4250, 110, 0))
        self.assertEqual(result.releases, ((12, 22, 80), (11, 21, 30)))
        for change in ({'original_reservation': [(11, 80), (12, 80)]}, {'previous_required': 100},
                       {'paid_provider_units': 9751}, {'allocations': [(11, 21, 80, 50), (11, 22, 80, 0)]},
                       {'allocations': [(11, 21, 80, 81), (12, 22, 80, 0)]},
                       {'projected': replace(projected, net_coin_units=9911)},
                       {'projected': replace(projected, store_income_units=161)},
                       {'projected': replace(projected, release_units=109)}):
            with self.subTest(change=change), self.assertRaises(PointsFundingAllocationError):
                plan_funding_revision(**{**data, **change})

    def test_followup_funding_revision_identifies_new_shortfall(self):
        from orders.points_funding_revision import plan_funding_revision
        projected = funding_after_refund(principal_units=100, gift_units=0, compensation_units=0,
            returned_prepaid_units=0, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=200, pending_deduction_units=0, provider_external_units=0,
            inviter_rate=0, allocated_units=30)
        result = plan_funding_revision(allocations=[(1, 2, 30, 0)], original_reservation=[(1, 30)],
            previous_required=30, paid_provider_units=50, projected=projected)
        self.assertEqual((result.remaining_provider_units, result.release_units, result.shortfall_units), (150, 0, 70))
        self.assertEqual(result.releases, ())

    def test_formal_followup_checks_projected_funding_if_prior_provider_was_paid(self):
        from types import SimpleNamespace
        from orders.points_followup_termination import formal_followup_funding_ready

        proposal = {'prior_refund_id': 8, 'prior_paid': True,
                    'conditional_funding': SimpleNamespace(remaining_units=70)}
        with patch('orders.points_followup_termination.endpoint_available', return_value=True) as available:
            self.assertFalse(formal_followup_funding_ready(proposal))
            self.assertTrue(formal_followup_funding_ready({**proposal, 'prior_paid': False}))
            available.assert_not_called()
            proposal['conditional_funding'].remaining_units = 0
            self.assertTrue(formal_followup_funding_ready(proposal))
            available.assert_called_once_with(8, prior_paid=True)
            available.return_value = False
            self.assertFalse(formal_followup_funding_ready(proposal))

    def test_user_confirmed_partial_refund_recomputes_unpaid_commission(self):
        # RMB amounts converted to ledger units: 1 yuan = 10 coins = 100 units.
        # The 7.5-yuan points returned do not reduce the 127.5-yuan net coin paid.
        result = funding_after_refund(principal_units=17000, gift_units=0, compensation_units=0,
            returned_prepaid_units=4250, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=16000, pending_deduction_units=5000, provider_external_units=0,
            inviter_rate=10, allocated_units=0)
        self.assertEqual((result.net_coin_units, result.provider_income_units, result.inviter_base_units,
                          result.inviter_income_units), (12750, 11000, 12750, 1275))

    def test_referral_uses_post_refund_net_paid_and_unused_funding_is_released(self):
        # 1,700 paid, of which 1,100 principal and 600 gift/compensation;
        # the proportional refund returns 420 principal, retained income 1,100.
        plan = funding_after_refund(principal_units=1100, gift_units=400, compensation_units=200,
            returned_prepaid_units=420, returned_gift_units=0, returned_compensation_units=0,
            provider_income_units=1600, pending_deduction_units=500, provider_external_units=0,
            inviter_rate=10, allocated_units=510)
        self.assertEqual((plan.net_coin_units, plan.net_principal_units, plan.provider_income_units,
                          plan.inviter_base_units, plan.inviter_income_units), (1280, 680, 1100, 1280, 128))
        self.assertEqual((plan.required_units, plan.release_units, plan.remaining_units), (548, 0, 38))
        self.assertEqual(plan.net_principal_units + plan.planned_provider_external_units + plan.required_units,
                         plan.provider_income_units + plan.inviter_income_units + plan.store_income_units)

    def test_external_recovery_is_conditional_and_invalid_values_fail_closed(self):
        kwargs = dict(principal_units=600, gift_units=400, compensation_units=0,
                      returned_prepaid_units=600, returned_gift_units=400, returned_compensation_units=0,
                      provider_income_units=800, pending_deduction_units=800, provider_external_units=200,
                      inviter_rate=10, allocated_units=0)
        result = funding_after_refund(**kwargs)
        self.assertEqual((result.net_coin_units, result.provider_income_units, result.store_income_units), (0, 0, 200))
        for changes in ({'pending_deduction_units': 801}, {'returned_gift_units': 401},
                        {'inviter_rate': 101}, {'provider_external_units': True}, {'returned_prepaid_units': 425}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                funding_after_refund(**{**kwargs, **changes})

    def test_missing_agreement_evidence_storage_is_not_treated_as_valid(self):
        from django.db import DatabaseError
        from orders.points_negotiation_preview import verified_asset_terms
        with patch('orders.points_negotiation_preview.PointsNegotiationTerms.objects.select_related',
                   side_effect=DatabaseError('missing evidence table')):
            with self.assertRaisesMessage(ValidationError, '凭证暂不可用'):
                verified_asset_terms(object())


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class PointsNegotiationPreviewTwoStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = PointsFundingPlatformRecoveryTests.setUp
    payload = PointsFundingPlatformRecoveryTests.payload
    quoted_payload = PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = PointsFundingPlatformRecoveryTests.verify
    fund = PointsFundingPlatformRecoveryTests.fund

    def test_main_refund_funding_preview_includes_paid_addon_and_preserves_commission_basis(self):
        self._main_refund_with_addon(preallocated=True)

    def test_main_refund_with_addon_waits_for_whole_order_funding(self):
        self._main_refund_with_addon(preallocated=False)

    def test_addon_refund_then_main_refund_releases_only_remaining_funding(self):
        self._main_refund_with_addon(preallocated=True, addon_refund_first=True)

    def test_addon_refund_deducting_main_then_main_refund_settles_remaining_income(self):
        self._main_refund_with_addon(preallocated=True, addon_refund_first=True, addon_main_burden=True)

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def _main_refund_with_addon(self, *, preallocated, addon_refund_first=False, addon_main_burden=False):
        from orders.models import GameCategory, ServiceAddonConfiguration, ServiceAddonPriceOption
        with store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/',
                                  {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            game = GameCategory.objects.create(name='主单退款加购核算')
            self.service.game_category = game
            self.service.save(update_fields=['game_category'])
            addon = make_service(name='特殊加购', game_category=game, is_addon=True)
            config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
                condition_mode='NONE', conditions='', version=1)
            ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
            if addon_refund_first:
                from engagement.models import PointFundingLot
                wallet = get_wallet(account=order.customer_account, user_id=order.customer_id)
                wallet.balance += 1000
                wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=order.customer_account, kind='GIFT',
                                               source_key='addon-first-gift', remaining=1000)
            path = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': assignment.provider_account_id,
                'request_id': 'main-refund-addon-preview', 'expected_amount': 1000,
                'gift_balance_amount': 1000 if addon_refund_first else 0, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = boss.post(path + f"{intent.data['data']['id']}/pay/", {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            staff_user = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle', 'roi:view'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            if addon_refund_first:
                from uuid import uuid4
                from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
                registered = PlatformPassRepaymentTests.post(self, 'xinan', {
                    **self.data, 'request_id': str(uuid4()), 'external_reference': 'ADDON-FIRST', 'amount': 1600})
                self.assertEqual(registered.status_code, 200, registered.data)
                self.claim = registered.data['data']['claim_id']
                self.url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/points-allocation'
                self.assertEqual(self.verify().status_code, 200)
                funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1600})
                self.assertEqual(funded.status_code, 200, funded.data)
                first_case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk, 'reason': '加购部分退款',
                    'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'addon-first-case'}, format='json')
                self.assertEqual(first_case.status_code, 200, first_case.data)
                payment_id = paid.data['data']['payment_id']
                refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
                addon_proposal = admin.post(refund_url, {'request_id': 'addon-first',
                    'amount_raw': 1000 if addon_main_burden else 100,
                    'case_id': first_case.data['data']['id'], 'explanation': '加购部分未履行'}, format='json')
                self.assertEqual(addon_proposal.status_code, 200, addon_proposal.data)
                addon_id = addon_proposal.data['data']['id']
                user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{addon_id}/'
                for actor in (boss, player):
                    self.assertEqual(actor.post(user_url, {'accepted': True}, format='json').status_code, 200)
                result = admin.post(refund_url + f'{addon_id}/execute/', {}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                if addon_main_burden:
                    self.assertEqual(result.data['data']['participants'][0]['main_pending_deduction_raw'], 200)
                resolved = admin.post(f'/api/admin/aftersales/{first_case.data["data"]["id"]}/resolve/',
                                      {'resolution': '加购退款完成'}, format='json')
                self.assertEqual(resolved.status_code, 200, resolved.data)
                from orders.models import OrderAddonFundingReleaseRun
                self.assertEqual(OrderAddonFundingReleaseRun.objects.get(refund__proposal_id=addon_id).released_amount,
                                 1000 if addon_main_burden else 100)
            case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '主单部分退款，保留加购', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'main-refund-addon-preview-case'}, format='json')
            self.assertEqual(case.data['code'], 0, case.data)
            result = admin.get(f'/api/admin/aftersales/{case.data["data"]["id"]}/negotiation-funding/',
                               {'refund_units': 1000})
            self.assertEqual(result.status_code, 200, result.data)
            combined = result.data['data']['conditional_combined_funding']
            self.assertEqual((combined['net_coin_units'], combined['net_principal_units'],
                              combined['provider_income_units'], combined['inviter_base_units'],
                              combined['inviter_income_units'], combined['required_units']),
                             (3340, 3340, 3600, 3340, 0, 260) if addon_main_burden else
                             (4240, 3340, 4500, 3340, 0, 1160) if addon_refund_first
                             else (4340, 4340, 4600, 3340, 0, 260))
            self.assertTrue(result.data['data']['read_only_points'])
            case_id = case.data['data']['id']
            if preallocated and not addon_refund_first:
                from orders.points_business_access import _bind
                case_path = f'/api/admin/aftersales/{case_id}/'
                amount = {'refund_units': 1000}
                self.assertNotIn('execution_check_required', result.data['data'])
                with (patch('orders.points_business_access.configured', return_value=True), transaction.atomic(),
                      _bind(phase='refund', kind='addon', order_id=order.pk)):
                    preview = admin.get(case_path + 'negotiation-preview/', amount)
                    self.assertTrue(preview.data['data']['execution_available'], preview.data)
                    funding_result = admin.get(case_path + 'negotiation-funding/', amount)
                    funding = funding_result.data['data']
                    self.assertIs(funding['execution_check_required'], True)
                    self.assertTrue(funding['book_amounts_sufficient'])
                    self.assertFalse(funding['executable'])
                    self.assertFalse(funding['read_only_points'])
                    self.assertEqual(funding['conditional_combined_funding'], combined)
                    self.assertGreater(combined['remaining_units'], 0)
                    self.assertIn('到账补资不足', funding_result.data['msg'])
                    with override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
                        preview = admin.get(case_path + 'negotiation-preview/', amount)
                        self.assertFalse(preview.data['data']['execution_available'])
                        funding = admin.get(case_path + 'negotiation-funding/', amount).data['data']
                        self.assertIs(funding['execution_check_required'], True)
                        self.assertFalse(funding['executable'])
                        self.assertTrue(funding['read_only_points'])
                    self.assertFalse(order.refund_records.exists())
            proposal = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'main-refund-addon-proposal', 'expected_version': 0, 'refund_units': 1000,
                'provider_terms': [{'account_id': assignment.provider_account_id,
                                    'retained_units': 3600 if addon_main_burden else 3800}],
                'explanation': '主单部分退款，加购保留'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            status = admin.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/')
            self.assertEqual(status.status_code, 200, status.data)
            self.assertFalse(status.data['data']['execution_available'])
            for actor in (boss, player):
                decision = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                    {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(decision.data['code'], 0, decision.data)
            executed = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'main-refund-addon-execute'}, format='json')
            self.assertNotEqual(executed.status_code, 200, executed.data)
            self.assertIn('补资释放及结算尚未接通', str(executed.data))
            self.assertFalse(Transaction.objects.filter(order=order, tx_type__in=['REFUND', 'INCOME']).exists())
            if not addon_refund_first:
                self.assertEqual(self.verify().status_code, 200)
            if preallocated and not addon_refund_first:
                funded = self.fund({'request_id': '00000000-0000-4000-8000-000000000119',
                                    'order_id': order.pk, 'amount': 400})
                self.assertEqual(funded.status_code, 200, funded.data)
                with (patch('orders.points_business_access.configured', return_value=True), transaction.atomic(),
                      _bind(phase='refund', kind='addon', order_id=order.pk)):
                    funding = admin.get(case_path + 'negotiation-funding/', amount).data['data']
                    self.assertIs(funding['execution_check_required'], True)
                    self.assertEqual(funding['conditional_combined_funding']['remaining_units'], 0)
                    self.assertTrue(funding['executable'])
                    self.assertFalse(order.refund_records.exists())
            # The isolated-only switch exercises the real refund and release;
            # the public path stays closed until refunded add-on settlement exists.
            with override_settings(SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True):
                execution = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                    'proposal_id': proposal_id, 'client_request_id': 'main-refund-addon-execute'}, format='json')
                self.assertEqual(execution.status_code, 200, execution.data)
                again = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                    'proposal_id': proposal_id, 'client_request_id': 'main-refund-addon-execute'}, format='json')
                self.assertEqual(again.data['data'], execution.data['data'])
                if preallocated and not addon_refund_first:
                    from django.utils import timezone
                    from orders.grab_original_facts import original_amount_facts
                    from orders.models import OrderParticipationSegment
                    segment = OrderParticipationSegment.objects.get(attribution__order=order,
                        provider_account_id=assignment.provider_account_id, started_at__isnull=False)
                    fact = original_amount_facts([assignment.provider_account_id], as_of=timezone.now())[assignment.provider_account_id]
                    # The 1,000-equivalent main refund is offset by the paid 1,000 add-on.
                    self.assertEqual(fact.amount, segment.original_share)
                    with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING=True):
                        AfterSale.objects.filter(pk=case_id).update(
                            status='OPEN', addon_payment_id=paid.data['data']['payment_id'])
                        for suffix in ('negotiation-preview/', 'negotiation-funding/'):
                            response = admin.get(case_path + suffix, {'refund_units': 1000})
                            self.assertEqual(response.status_code, 400, response.data)
                            self.assertIn('该售后仅适用于指定加购', response.data['msg'])
                        transaction.set_rollback(True)
                    from engagement.models import PointLedger
                    with transaction.atomic():
                        refund_ledger = PointLedger.objects.get(order=order, kind='ORDER_POINTS_RETURN')
                        refund_ledger.amount += 1
                        refund_ledger.save(update_fields=['amount'])
                        damaged = original_amount_facts([assignment.provider_account_id], as_of=timezone.now())
                        self.assertIsNone(damaged[assignment.provider_account_id].amount)
                        self.assertIn(order.pk, damaged[assignment.provider_account_id].review_order_ids)
                        transaction.set_rollback(True)
                from orders.models import OrderPointsFundingReleaseEvidence
                from orders.points_funding_release import verify_release_locked
                evidence = OrderPointsFundingReleaseEvidence.objects.get(posting__proposal_id=proposal_id)
                if addon_main_burden:
                    self.assertEqual(evidence.posting.participants[0]['prior_addon_deduction'], 200)
                    share = evidence.posting.shares.get(account_id=assignment.provider_account_id)
                    self.assertEqual((share.original_income, share.retained_income), (4600, 3600))
                self.assertEqual((evidence.expected_release, evidence.addon_funding_snapshot[0]['paid_units']),
                                 (340 if addon_refund_first else 140 if preallocated else 0,
                                  0 if addon_main_burden else 900 if addon_refund_first else 1000))
                if addon_refund_first:
                    self.assertEqual(sum(row[3] for row in evidence.prior_addon_release_snapshot),
                                     1000 if addon_main_burden else 100)
                from console.test_operating_workflow import seed_payroll_fixture
                from django.utils import timezone
                seed_payroll_fixture()
                day = timezone.localdate().isoformat()
                roi = admin.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day})
                self.assertEqual(roi.status_code, 200, roi.data)
                operating = roi.data['data']['operating']
                self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                                  operating['gift_compensation_cost_fen'], operating['referral_cost_fen']),
                                 (3340, 3600, 0, 0) if addon_main_burden else
                                 (4240, 4500, 900, 0) if addon_refund_first else (4340, 4600, 0, 0), roi.data)
                with transaction.atomic():
                    verified = verify_release_locked(evidence)
                self.assertEqual((verified.required_units, verified.release_units),
                                 (260, 340) if addon_main_burden else
                                 (1160, 340) if addon_refund_first else (260, 140 if preallocated else 0))
                self.assertEqual(Transaction.objects.filter(order=order, tx_type='REFUND', amount=660).count(), 1)
                with transaction.atomic():
                    # MySQL may return object keys in another order after a JSON round trip.
                    reordered = [dict(sorted(item.items())) for item in evidence.addon_funding_snapshot]
                    OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).update(addon_funding_snapshot=reordered)
                    self.assertEqual(verify_release_locked(evidence).release_units, verified.release_units)
                    transaction.set_rollback(True)
                with transaction.atomic():
                    from orders.points_funding_release import _digest
                    legacy = _digest(evidence.posting, evidence.allocations, evidence.original_provider_income,
                        evidence.original_settlement_id, evidence.expected_release,
                        evidence.addon_funding_snapshot, evidence.prior_addon_release_snapshot)
                    self.assertNotEqual(legacy, evidence.evidence_hash)
                    OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).update(evidence_hash=legacy)
                    self.assertEqual(verify_release_locked(evidence).release_units, verified.release_units)
                    transaction.set_rollback(True)
                with transaction.atomic():
                    altered = [dict(item) for item in evidence.addon_funding_snapshot]
                    altered[0]['principal_units'] += 1
                    OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).update(addon_funding_snapshot=altered)
                    with self.assertRaises(ValidationError):
                        verify_release_locked(evidence)
                    transaction.set_rollback(True)
                if addon_refund_first:
                    with transaction.atomic():
                        altered = [list(item) for item in evidence.prior_addon_release_snapshot]
                        altered[0][3] += 10
                        OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).update(prior_addon_release_snapshot=altered)
                        with self.assertRaises(ValidationError):
                            verify_release_locked(evidence)
                        transaction.set_rollback(True)
                from datetime import timedelta
                from django.utils import timezone
                from engagement.confirmation import process_order
                from engagement.models import ConsumptionConfirmation
                from orders.models import OrderAddonConsumption, OrderAddonSettlement, OrderPointsRefundSettlement
                from wallet.models import ProviderReport
                report = ProviderReport.objects.get(order=order, provider_account=assignment.provider_account)
                for kind in ('COMPLETION', 'RESULT'):
                    uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                        {'kind': kind, 'image': screenshot()}, format='multipart')
                    self.assertEqual(uploaded.data['code'], 0, uploaded.data)
                self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                reviewer_user = make_console_user(['report:view', 'report:audit'])
                reviewer = get_or_create_account_for_legacy_user(reviewer_user)
                reviewer_user.admin_membership.account = reviewer
                reviewer_user.admin_membership.save(update_fields=['account'])
                review_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
                self.assertEqual(review_client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
                report.refresh_from_db()
                if not preallocated:
                    with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                        self.assertFalse(process_order(order.pk))
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                    funded = self.fund({'request_id': '00000000-0000-4000-8000-000000000120',
                                        'order_id': order.pk, 'amount': 260})
                    self.assertEqual(funded.status_code, 200, funded.data)
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    self.assertTrue(process_order(order.pk))
                    self.assertFalse(process_order(order.pk))
                settlement = OrderPointsRefundSettlement.objects.get(posting__proposal_id=proposal_id)
                self.assertEqual((settlement.paid_units, settlement.provider_units, settlement.addon_paid_units,
                                  settlement.addon_provider_units, settlement.funding_units),
                                 (3340, 3600, 0, 0, 260) if addon_main_burden else
                                 (3340, 3800, 900, 700, 1160) if addon_refund_first else (3340, 3800, 1000, 800, 260))
                addon_settlement = OrderAddonSettlement.objects.filter(refund_settlement=settlement).first()
                if addon_main_burden:
                    self.assertIsNone(addon_settlement)
                else:
                    self.assertEqual((addon_settlement.amount, addon_settlement.income_transaction.amount),
                                     (700, 700) if addon_refund_first else (800, 800))
                order.refresh_from_db()
                self.assertEqual((order.settlement_store_funding, order.settlement_addon_contribution),
                                 (260, 0) if addon_main_burden else (1160, 0) if addon_refund_first else (260, 200))
                self.assertEqual((ConsumptionConfirmation.objects.get(order=order).amount,
                                  ConsumptionConfirmation.objects.get(order=order).refunded_amount), (4000, 660))
                addon_consumption = OrderAddonConsumption.objects.get(payment_id=paid.data['data']['payment_id'])
                self.assertEqual((addon_consumption.paid_units, addon_consumption.refunded_units),
                                 (1000, 1000 if addon_main_burden else 100 if addon_refund_first else 0))

    def _refund_for_later_funding(self, initial, *, finish_funding=True):
        import tempfile
        from uuid import uuid4
        from orders.points_funding_evidence import verify_order_funding
        self.assertEqual(self.verify().status_code, 200)
        if initial:
            funded = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': initial})
            self.assertEqual(funded.status_code, 200, funded.data)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            response = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            opened = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '退款后继续核对补资', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'refund-funding-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'refund-funding-proposal', 'expected_version': 0, 'refund_units': 1000,
                'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                'explanation': '保留服务并按构成退款'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            for actor in (boss, player):
                response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                    {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
            posted = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'refund-funding-execute'}, format='json')
            self.assertEqual(posted.data['code'], 0, posted.data)
            result = verify_order_funding(order.pk)
            self.assertEqual((result.required, result.allocated, result.remaining, result.fully_funded),
                             (460, initial, 460 - initial, False))
        if not finish_funding:
            return order.pk, player
        allocation = {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 460 - initial}
        response = self.fund(allocation)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.fund(allocation).data, response.data)
        with store_context(self.second), transaction.atomic():
            result = verify_order_funding(self.order.pk)
            self.assertEqual((result.required, result.allocated, result.remaining, result.fully_funded), (460, 460, 0, True))
            from orders.models import OrderPointsFundingReleaseEvidence
            from orders.points_funding_plan import PointsFundingPlanError
            with override_settings(SERVICE_POINTS_REFUNDED_FUNDING_TESTING=False):
                with self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(self.order.pk)
            evidence = OrderPointsFundingReleaseEvidence.objects.get(posting__proposal__case_id=case_id)
            OrderPointsFundingReleaseEvidence.objects.filter(pk=evidence.pk).update(expected_release=1)
            with self.assertRaises(PointsFundingPlanError):
                verify_order_funding(self.order.pk)
            transaction.set_rollback(True)
        denied = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 1})
        self.assertNotEqual(denied.status_code, 200)
        crossed = self.client.post(f'/api/admin/platform/pass-refund-repayments/shenglang/{self.claim}/points-allocation',
            {'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 1}, format='json',
            HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(crossed.status_code, 404)
        return order.pk, player

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True)
    def test_after_refund_platform_can_fund_initial_shortfall(self):
        self._refund_for_later_funding(0)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True)
    def test_after_refund_platform_can_top_up_previously_allocated_funds(self):
        self._refund_for_later_funding(300)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
                       SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                       REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_refund_settles_retained_income_only_after_whole_funding_and_clocks(self):
        import tempfile
        from datetime import timedelta
        from uuid import uuid4
        from django.utils import timezone
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsRefundSettlementPerson, OrderRefund
        from orders.services import settle_order
        from orders.tests.factories import make_completed_order
        from wallet.models import ProviderReport, RefundDepositRecoveryPermit, RefundDepositIncomeApplication
        from wallet.refund_deposit import charge_refund_deposit
        order_id, player = self._refund_for_later_funding(300, finish_funding=False)
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=order_id)
            report = ProviderReport.objects.get(order=order)
            for kind in ('COMPLETION', 'RESULT'):
                uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': kind, 'image': screenshot()}, format='multipart')
                self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            reviewer_user = make_console_user(['report:view', 'report:audit'])
            reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
            reviewer_user.admin_membership.account = reviewer_account
            reviewer_user.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
            audited = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(audited.data['code'], 0, audited.data)
            report.refresh_from_db(); self.assertEqual(report.payout_amount, 3800)
            assignment = OrderProvider.objects.get(order=order)
            profile = assignment.provider.escort_profile
            profile.deposit_mode = 'CASH'; profile.deposit_paid = 0; profile.deposit_required = 10000
            profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
            operator = make_console_user(['order:refund', 'escort:view', 'escort:deposit_topup'])
            operator_account = get_or_create_account_for_legacy_user(operator)
            operator.admin_membership.account = operator_account
            operator.admin_membership.save(update_fields=['account'])
            clerk = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(operator_account)[0])
            topup = clerk.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 600, 'reason': '隔离测试线下押金', 'reference': 'TEST-POSITIVE-001',
                'client_request_id': 'test-positive-recovery'}, format='multipart')
            self.assertEqual(topup.data['code'], 0, topup.data)
            get_wallet(account_id=assignment.provider_account_id, user_id=assignment.provider_id)
            previous = make_completed_order(order.customer, assignment.provider, amount=10000, payment_status='PAID')
            previous_refund = OrderRefund.objects.create(order=previous, amount=200, reason='历史退款测试凭证',
                                                         operator=operator, operator_account=operator_account)
            with transaction.atomic():
                deficit = charge_refund_deposit(refund=previous_refund, profile=profile, amount=200,
                                                operator=operator, operator_account=operator_account)
                permit = RefundDepositRecoveryPermit.objects.create(profile=profile,
                    account=assignment.provider_account, order=order)
            from orders.points_settlement_test import verified_person_settlement_display
            self.assertEqual(verified_person_settlement_display(report)[0], 'WAITING')
            now = max(order.completed_at, report.submitted_at) + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=now), transaction.atomic():
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order_id)), [])
                from engagement.confirmation import process_order
                with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                    self.assertFalse(process_order(order_id))
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        funded = self.fund({'request_id': str(uuid4()), 'order_id': order_id, 'amount': 160})
        self.assertEqual(funded.status_code, 200, funded.data)
        with store_context(self.second), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=71)):
            with transaction.atomic():
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order_id)), [])
            self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
        with store_context(self.second), patch('django.utils.timezone.now', return_value=now):
            order = Order.objects.get(pk=order_id)
            before = Transaction.objects.count()
            with transaction.atomic():
                with patch.object(OrderPointsRefundSettlementPerson.objects, 'create', side_effect=RuntimeError('结算凭证写入失败')):
                    with self.assertRaisesMessage(RuntimeError, '结算凭证写入失败'):
                        settle_order(Order.objects.select_for_update().get(pk=order_id))
                self.assertEqual(Transaction.objects.count(), before)
                self.assertFalse(OrderPointsRefundSettlementPerson.objects.exists())
                deficit.refresh_from_db(); permit.refresh_from_db()
                self.assertEqual(deficit.remaining, 200)
                self.assertIsNone(permit.closed_at)
                settled = settle_order(Order.objects.select_for_update().get(pk=order_id))
                self.assertEqual(settled, [report.provider_id])
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order_id)), [])
            order.refresh_from_db()
            self.assertEqual(order.settled_at, now)
            person = OrderPointsRefundSettlementPerson.objects.get()
            self.assertEqual((person.amount, person.income_transaction.amount), (3800, 3800))
            deficit.refresh_from_db(); permit.refresh_from_db()
            application = RefundDepositIncomeApplication.objects.get(source_transaction=person.income_transaction)
            self.assertEqual((deficit.remaining, application.applied_amount, permit.close_reason), (0, 200, 'GAP_PAID'))
            self.assertEqual(verified_person_settlement_display(report), ('SETTLED', now))
            with transaction.atomic():
                Transaction.objects.filter(pk=person.income_transaction_id).update(balance_after=1)
                self.assertEqual(verified_person_settlement_display(report)[0], 'REVIEW')
                from orders.settlement import SettlementError
                with self.assertRaises(SettlementError):
                    settle_order(Order.objects.select_for_update().get(pk=order_id))
                transaction.set_rollback(True)
            self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME',
                remark=f'订单收入：{order.order_no}').count(), 1)
            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            from engagement.confirmation import process_order
            with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True):
                self.assertFalse(process_order(order.pk))
            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            from engagement.models import RefundedPointsConsumptionEvidence
            from engagement.services import config
            member = MemberProfile.objects.get(account=order.customer_account)
            member.refresh_from_db(); points_before = member.points_balance
            with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                   SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                from orders.models import OrderPointsBenefitSnapshot
                with transaction.atomic():
                    OrderPointsBenefitSnapshot.objects.filter(redemption__order=order).delete()
                    with self.assertRaises(ValidationError):
                        process_order(order.pk)
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    transaction.set_rollback(True)
                with transaction.atomic():
                    changed = {**order.funding_terms, 'points_rate': order.funding_terms['points_rate'] + 1}
                    Order.objects.filter(pk=order.pk).update(funding_terms=changed)
                    with self.assertRaises(ValidationError):
                        process_order(order.pk)
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    transaction.set_rollback(True)
                with transaction.atomic():
                    operation = config(); operation.vip_enabled = False; operation.save(update_fields=['vip_enabled'])
                    self.assertFalse(process_order(order.pk))
                    evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order=order)
                    self.assertFalse(evidence.vip_eligible); self.assertFalse(evidence.points_eligible)
                    self.assertIsNone(evidence.confirmation_id); self.assertEqual(evidence.awarded_points, 0)
                    operation.vip_enabled = True; operation.save(update_fields=['vip_enabled'])
                    self.assertFalse(process_order(order.pk))
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    transaction.set_rollback(True)
                with transaction.atomic():
                    operation = config(); operation.points_enabled = False; operation.save(update_fields=['points_enabled'])
                    self.assertTrue(process_order(order.pk))
                    evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order=order)
                    self.assertTrue(evidence.vip_eligible); self.assertFalse(evidence.points_eligible)
                    self.assertEqual(evidence.awarded_points, 0)
                    self.assertEqual((evidence.net_paid_units, evidence.net_principal_units), (3340, 3340))
                    transaction.set_rollback(True)
                from engagement.models import LicenseConsumptionPointsDecision
                for unavailable in (False, True):
                    from contextlib import nullcontext
                    from django.db import DatabaseError
                    failure = (patch('stores.license_status.status_for_store', side_effect=DatabaseError('offline'))
                               if unavailable else nullcontext())
                    with transaction.atomic(), override_settings(LICENSE_BENEFIT_ENFORCEMENT_ENABLED=True), failure:
                        self.assertTrue(process_order(order.pk))
                        evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order=order)
                        self.assertTrue(evidence.vip_eligible)
                        self.assertFalse(evidence.points_eligible)
                        self.assertEqual(evidence.awarded_points, 0)
                        decision = LicenseConsumptionPointsDecision.objects.get(order=order)
                        self.assertEqual(decision.outcome, 'UNKNOWN' if unavailable else 'DENY')
                        self.assertEqual((evidence.net_paid_units, evidence.net_principal_units), (3340, 3340))
                        self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                        if unavailable:
                            from uuid import uuid4
                            from engagement.license_points_review import resolve_locked
                            from engagement.refunded_points_confirmation import verify_evidence_locked
                            review = resolve_locked(decision.pk, request_id=uuid4(), actor_id=uuid4(),
                                outcome='ALLOW', reason='已核对实销时有效授权')
                            self.assertEqual(review.awarded_points, 3340)
                            self.assertEqual(verify_evidence_locked(evidence).pk, evidence.pk)
                            self.assertFalse(process_order(order.pk))
                        transaction.set_rollback(True)
                with patch.object(RefundedPointsConsumptionEvidence, 'save', side_effect=RuntimeError('模拟会员凭证失败')):
                    with self.assertRaisesMessage(RuntimeError, '模拟会员凭证失败'):
                        process_order(order.pk)
                member.refresh_from_db(); order.refresh_from_db()
                self.assertEqual(member.points_balance, points_before)
                self.assertIsNone(order.funding_benefits_resolved_at)
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                operation = config(); operation.base_points_rate = 250
                operation.save(update_fields=['base_points_rate'])
                self.assertTrue(process_order(order.pk))
                member.refresh_from_db(); order.refresh_from_db()
                evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order=order)
                self.assertEqual((evidence.paid_units, evidence.refunded_coin_units, evidence.net_paid_units,
                                  evidence.net_principal_units, evidence.awarded_points), (4000, 660, 3340, 3340, 3340))
                self.assertEqual(member.points_balance, points_before + 3340)
                self.assertEqual((evidence.confirmation.amount, evidence.confirmation.refunded_amount), (4000, 660))
                self.assertFalse(process_order(order.pk))
                with transaction.atomic():
                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
                       SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True)
    def test_refund_recomputes_and_credits_unpaid_boss_referral_once(self):
        import tempfile
        from datetime import timedelta
        from django.utils import timezone
        from coupons.models import Coupon, UserCoupon
        from engagement.models import PointFundingLot
        from orders.models import OrderPointsRefundSettlement
        from orders.services import settle_order
        from orders.tests.factories import make_boss_type
        from wallet.models import ProviderReport
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            Transaction.objects.filter(order_id=self.order.pk, tx_type='PAY').update(created_at=timezone.now() - timedelta(days=2))
            inviter = make_user(); inviter_account = get_or_create_account_for_legacy_user(inviter)
            buyer = make_user(); buyer_account = get_or_create_account_for_legacy_user(buyer)
            buyer_account.boss_type = make_boss_type(discount_rate=90)
            buyer_account.inviter = inviter_account; buyer_account.inviter_commission_rate = 10
            buyer_account.save(update_fields=['boss_type', 'inviter', 'inviter_commission_rate'])
            buyer_wallet = get_wallet(user=buyer, account=buyer_account)
            buyer_wallet.balance = 8000; buyer_wallet.save(update_fields=['balance'])
            MemberProfile.objects.create(account=buyer_account, points_balance=15000)
            service = make_service(price=10000)
            coupon = Coupon.objects.create(name='推荐退款测试券', discount_type='DIRECT', amount=3000)
            UserCoupon.objects.create(account=buyer_account, coupon=coupon)
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(buyer_account)[0])
            options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            payload = {'service_id': service.pk, 'preview_points_hundredths': 15000,
                'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                'client_request_id': 'points-referral-order'}
            quote = boss.post('/api/orders/quote/', payload, format='json')
            self.assertEqual(quote.data['code'], 0, quote.data)
            payload.pop('preview_points_hundredths')
            payload['points_checkout_test_token'] = quote.data['data']['points_preview']['checkout_test_token']
            created = boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            self.assertEqual((order.amount, order.inviter_account_id, order.inviter_commission_rate_snapshot),
                             (4000, inviter_account.pk, 10))
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
            provider = make_provider(deposit_mode='EARNINGS')
            provider_account = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
            grabbed = player.post(f'/api/orders/orders/{order.pk}/grab/', {}, format='json')
            self.assertEqual(grabbed.data['code'], 0, grabbed.data)
            application = OrderApplication.objects.get(order=order, applicant=provider_account)
            chosen = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
            self.assertEqual(chosen.data['code'], 0, chosen.data)
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'roi:view'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            opened = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '推荐订单协商退款', 'affected_provider_account_ids': [provider_account.pk],
                'client_request_id': 'points-referral-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'points-referral-proposal', 'expected_version': 0, 'refund_units': 3000,
                'provider_terms': [{'account_id': provider_account.pk, 'retained_units': 1800}],
                'explanation': '双方确认部分退款'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            for actor in (boss, player):
                confirmed = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                    {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(confirmed.data['code'], 0, confirmed.data)
            response = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                {'proposal_id': proposal_id, 'client_request_id': 'points-referral-refund'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertFalse(Transaction.objects.filter(order=order, remark=f'推荐分佣：{order.order_no}').exists())
            from console.roi_period import points_negotiation_cost
            from console.test_operating_workflow import seed_payroll_fixture
            from orders.models import OrderPointsRefund
            seed_payroll_fixture()
            points_refund = OrderPointsRefund.objects.get(redemption__order=order)
            paid_at, refunded_at = points_refund.redemption.payment.created_at, points_refund.refund.created_at
            prior_cost = points_negotiation_cost(order, paid_at, refunded_at)
            refund_cost = points_negotiation_cost(order, refunded_at, timezone.now() + timedelta(seconds=1))
            self.assertEqual(prior_cost, {'provider': 4800, 'referral': 400, 'gift_compensation': 0})
            self.assertEqual(refund_cost, {'provider': -3000, 'referral': -200, 'gift_compensation': 0})
            day = timezone.localdate().isoformat()
            params = {'start_date': day, 'end_date': day}
            roi = client.get('/api/admin/operating-roi/', params)
            self.assertEqual(roi.status_code, 200, roi.data)
            operating = roi.data['data']['operating']
            self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                              operating['referral_cost_fen'], operating['refunded_points_value_fen']),
                             (2000, 1800, 200, 1000), roi.data)
            report = ProviderReport.objects.get(order=order, provider_account=provider_account)
            for kind in ('COMPLETION', 'RESULT'):
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': kind, 'image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            reviewer_user = make_console_user(['report:view', 'report:audit'])
            reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
            reviewer_user.admin_membership.account = reviewer_account; reviewer_user.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
            audited = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(audited.data['code'], 0, audited.data)
            report.refresh_from_db(); ready = report.submitted_at + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=ready), transaction.atomic():
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [provider.pk])
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                settlement = OrderPointsRefundSettlement.objects.get(posting__proposal__case_id=case_id)
                self.assertEqual((settlement.paid_units, settlement.provider_units, settlement.inviter_units,
                                  settlement.store_units), (2000, 1800, 200, 0))
                self.assertEqual(settlement.inviter_transaction.wallet.account_id, inviter_account.pk)
                self.assertEqual(settlement.inviter_transaction.amount, 200)
                self.assertEqual(PointFundingLot.objects.get(account=inviter_account,
                    source_key=f'points-refund-settlement-referral:{settlement.pk}').remaining, 200)
                self.assertEqual(Transaction.objects.filter(order=order, remark=f'推荐分佣：{order.order_no}').count(), 1)
            roi = client.get('/api/admin/operating-roi/', params)
            self.assertEqual((roi.data['data']['operating']['provider_cost_fen'],
                              roi.data['data']['operating']['referral_cost_fen']), (1800, 200), roi.data)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
                       SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING=True)
    def test_double_order_requires_only_boss_and_affected_provider(self):
        import tempfile
        from console.points_redemption_config import save_points_terms
        from console.room_confirmation_config import save_room_confirmation
        from coupons.models import Coupon, UserCoupon
        from orders.points_redemption_config import current_rules
        from orders.room_confirmation_config import current_rules as room_rules
        from orders.tests.factories import make_boss_type
        from orders.tests.test_points_checkout_integration import upload_recording
        from users.models import EscortLevel, EscortProfile
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            buyer = make_user(); account = get_or_create_account_for_legacy_user(buyer)
            account.boss_type = make_boss_type(discount_rate=90); account.save(update_fields=['boss_type'])
            admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle',
                                       'escort:view', 'escort:deposit_topup', 'wallet:recharge'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            recharge = client.post('/api/admin/wallets/recharge/', {
                'account_id': account.pk, 'amount': 25000, 'gift_amount': 0,
                'trade_no': 'points-double-recharge', 'proof_image': screenshot(),
                'client_request_id': 'points-double-recharge',
            }, format='multipart')
            self.assertEqual(recharge.data['code'], 0, recharge.data)
            MemberProfile.objects.create(account=account, points_balance=15000)
            service = make_service(price=10000, billing_provider_count=2, fulfillment_provider_count=2)
            service.commission_policy = 'REGULAR'
            if getattr(self, '_exercise_room_continuation', False):
                service.room_enabled = True
                save_room_confirmation(30, room_rules()['version'], lambda *_: None)
            service.save(update_fields=['commission_policy', 'room_enabled'])
            coupon = Coupon.objects.create(name='双陪积分退款测试', discount_type='DIRECT', amount=3000)
            UserCoupon.objects.create(account=account, coupon=coupon)
            save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                              max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
            boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            options = boss.post('/api/orders/discount-options-preview/', {'service_id': service.pk}, format='json')
            self.assertEqual(options.data['code'], 0, options.data)
            payload = {'service_id': service.pk, 'preview_points_hundredths': 10000, 'dispatch_mode': 'BOSS_SELECT',
                       'discount_selection_token': options.data['data']['options'][0]['selection_token'],
                       'client_request_id': 'points-double-agreement'}
            quote = boss.post('/api/orders/quote/', payload, format='json')
            self.assertEqual(quote.data['code'], 0, quote.data)
            payload.pop('preview_points_hundredths')
            payload['points_checkout_test_token'] = quote.data['data']['points_preview']['checkout_test_token']
            created = boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            from django.utils import timezone
            from datetime import timedelta
            Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(hours=1))
            accounts, players = [], []
            for rate in (20, 30):
                user = make_provider(deposit_mode='EARNINGS')
                provider_account = get_or_create_account_for_legacy_user(user)
                level, _ = EscortLevel.objects.get_or_create(name=f'积分协商-{rate}', defaults={'commission_rate': rate})
                EscortProfile.objects.filter(user=user).update(account=provider_account, level=level)
                accounts.append(provider_account)
                players.append(APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0]))
            recordings = [upload_recording(player) for player in players]
            result = players[0].post(f'/api/orders/orders/{order.pk}/grab/', {
                'team_size': 2, 'partner_id': accounts[1].pk, 'recording_id': recordings[0],
            }, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            application = OrderApplication.objects.get(order=order, applicant=accounts[0])
            result = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                                     {'action': 'CONFIRM_TEAM', 'recording_id': recordings[1]}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            result = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            for player in players:
                result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            preapproved = None
            if getattr(self, '_exercise_preapproved_unpaid', False):
                from orders.tests.points_preapproved_unpaid_flow import complete_and_preapprove
                preapproved = complete_and_preapprove(self, order=order, accounts=accounts, players=players)
            if getattr(self, '_exercise_double_addon_burden', False):
                from orders.tests.points_double_addon_refund_flow import exercise
                exercise(self, order=order, service=service, buyer=buyer, account=account,
                         boss=boss, accounts=accounts, players=players, admin=client,
                         small=getattr(self, '_exercise_double_addon_small', False),
                         preapproved=getattr(self, '_exercise_double_addon_preapproved', False),
                         main_first=getattr(self, '_exercise_double_main_first', False),
                         preallocate=getattr(self, '_exercise_double_addon_preallocate', False),
                         partially_settled=getattr(self, '_exercise_double_addon_partial_paid', False),
                         gift=getattr(self, '_exercise_double_addon_gift', False),
                         split=getattr(self, '_exercise_double_addon_split', False),
                         two_addons=getattr(self, '_exercise_double_two_addons', False),
                         late_purchase=getattr(self, '_exercise_double_late_addon', False),
                         keep_addons=getattr(self, '_exercise_double_keep_addons', False),
                         late_gift=getattr(self, '_exercise_double_late_gift', False),
                         only_late=getattr(self, '_exercise_double_only_late', False))
                return
            if preapproved is not None:
                result = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                    'reason': '仅对第一位陪玩协商', 'affected_provider_account_ids': [accounts[0].pk],
                    'client_request_id': 'points-double-case'}, format='json')
            else:
                result = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '仅对第一位陪玩协商', 'affected_provider_account_ids': [accounts[0].pk],
                    'client_request_id': 'points-double-case'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            case_id = result.data['data']['id']
            proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'points-double-proposal', 'expected_version': 0, 'refund_units': 500,
                'provider_terms': [{'account_id': accounts[0].pk, 'retained_units': 5500}],
                'explanation': '第一位陪玩终止并退回对应资金'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            agreed_coins = proposal.data['data']['refund_coin_units']
            agreed_points = proposal.data['data']['refund_points_hundredths']
            url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            self.assertEqual(players[1].get(url).status_code, 404)
            self.assertEqual(players[1].post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json').status_code, 404)
            self.assertEqual(boss.post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json').data['data']['status'], 'PENDING')
            final = players[0].post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(final.data['data']['status'], 'ACCEPTED')
            self.assertEqual([term['account_id'] for term in final.data['data']['provider_terms']], [accounts[0].pk])
            self.assertNotIn('refund_coin_sources', final.data['data'])
            self.assertFalse(order.refund_records.exists())
            execution_url = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
            request = {'proposal_id': proposal_id, 'client_request_id': 'points-double-execute'}
            buyer_wallet = get_wallet(account_id=account.pk, user_id=buyer.pk)
            member = MemberProfile.objects.get(account=account)
            before = (buyer_wallet.balance, member.points_balance, Transaction.objects.count(), PointLedger.objects.count())
            with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('模拟售后审计写入失败')):
                with self.assertRaisesMessage(RuntimeError, '模拟售后审计写入失败'):
                    client.post(execution_url, request, format='json')
            buyer_wallet.refresh_from_db(); member.refresh_from_db()
            self.assertEqual((buyer_wallet.balance, member.points_balance, Transaction.objects.count(), PointLedger.objects.count()), before)
            self.assertFalse(order.refund_records.exists())
            self.assertFalse(order.providers.filter(released_at__isnull=False).exists())
            self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'OPEN')
            executed = client.post(execution_url, request, format='json')
            self.assertEqual(executed.data['code'], 0, executed.data)
            self.assertEqual((executed.data['data']['refund_equivalent_units'],
                              executed.data['data']['refund_coin_units'],
                              executed.data['data']['refund_points_hundredths']), (500, agreed_coins, agreed_points))
            self.assertEqual(client.post(execution_url, request, format='json').data['data']['execution_id'],
                             executed.data['data']['execution_id'])
            self.assertEqual(client.get(execution_url, request).data['data']['execution_id'],
                             executed.data['data']['execution_id'])
            with transaction.atomic():
                from orders.models import OrderSegmentFinancialSnapshot
                snapshot = OrderSegmentFinancialSnapshot.objects.get(
                    segment__attribution__order=order, segment__provider_account=accounts[0])
                OrderSegmentFinancialSnapshot.objects.filter(pk=snapshot.pk).update(income_at_entry=1)
                self.assertNotEqual(client.get(execution_url, request).data.get('code', 400), 0)
                transaction.set_rollback(True)
            with override_settings(SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=False):
                self.assertEqual(client.get(execution_url, request).data['data']['execution_id'],
                                 executed.data['data']['execution_id'])
                self.assertNotEqual(client.post(execution_url, request, format='json').data.get('code', 400), 0)
            if preapproved is not None:
                from orders.tests.points_preapproved_unpaid_flow import finish_preapproved_unpaid
                finish_preapproved_unpaid(self, order=order, case_id=case_id, buyer=buyer, account=account,
                    accounts=accounts, players=players, boss=boss, client=client, execution_url=execution_url,
                    reports=preapproved)
                return
            order.refresh_from_db()
            self.assertEqual((order.status, order.payment_status, order.provider_account_id),
                             ('IN_SERVICE', 'PARTIALLY_REFUNDED', accounts[1].pk))
            assignments = {row.provider_account_id: row for row in order.providers.all()}
            self.assertIsNotNone(assignments[accounts[0].pk].released_at)
            self.assertIsNone(assignments[accounts[1].pk].released_at)
            self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'PARTIALLY_REFUNDED')
            visible = players[1].get(f'/api/orders/orders/{order.pk}/')
            self.assertEqual(visible.data['code'], 0, visible.data)
            self.assertIsNone(visible.data['data']['refund_summary'])
            self.assertEqual(visible.data['data']['aftersale_status'], '')
            continued = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
                                        {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(continued.data['code'], 0, continued.data)
            if getattr(self, '_exercise_room_continuation', False):
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
                room_url = f'/api/orders/orders/{order.pk}/room/completion-requests/'
                submitted = players[1].post(room_url,
                    {'description':'继续服务完成', 'client_request_id':'points-continuation'}, format='json')
                self.assertEqual(submitted.status_code, 200, submitted.data)
                self.assertEqual(submitted.data['data']['participants'], [accounts[1].pk])
                request_id = submitted.data['data']['id']
                reviewed = client.post(f'/api/admin/room-completion-requests/{request_id}/review/',
                    {'decision':'VERIFIED', 'note':'客服已核实', 'client_request_id':'points-room-review'}, format='json')
                self.assertEqual(reviewed.status_code, 200, reviewed.data)
                confirmed = boss.post(f'{room_url}{request_id}/confirm/',
                    {'client_request_id':'points-room-confirm'}, format='json')
                self.assertEqual(confirmed.status_code, 200, confirmed.data)
            order.refresh_from_db(); self.assertEqual(order.status, 'COMPLETED')
            from orders.points_service_payment import MARKER
            refund = order.refund_records.get()
            with transaction.atomic():
                terms = dict(order.funding_terms)
                del terms[MARKER]
                Order.objects.filter(pk=order.pk).update(funding_terms=terms)
                for player, affected in zip(players, (True, False)):
                    for path, filters in (('/api/orders/orders/', {'status': 'completed'}),
                                          ('/api/wallet/reports/', {'order_id': order.pk})):
                        result = player.get(path, filters)
                        self.assertEqual(result.data['code'], 0, result.data)
                        rows = result.data['data']
                        self.assertEqual(len(rows), 1, (path, affected, rows))
                        summary = rows[0]['refund_summary']
                        if affected:
                            self.assertEqual((summary['amount'], summary['reason'], summary['refund_count']),
                                             (refund.amount, refund.reason, 1))
                        else:
                            self.assertIsNone(summary, path)
                transaction.set_rollback(True)
            rejected = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                'reason': '已终止的范围不能重开', 'affected_provider_account_ids': [accounts[0].pk],
                'client_request_id': 'closed-points-scope'}, format='json')
            self.assertNotEqual(rejected.data.get('code', 400), 0, rejected.data)
            low = boss.post('/api/orders/evaluate/', {'order_id': order.pk, 'provider_account_id': accounts[1].pk,
                'score': 1, 'skill_score': 5, 'attitude_score': 5, 'communication_score': 5}, format='json')
            self.assertEqual(low.data['code'], 0, low.data)
            case = AfterSale.objects.get(pk=case_id)
            self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [accounts[1].pk]))
            self.assertEqual(client.get(execution_url, request).data['data']['execution_id'],
                             executed.data['data']['execution_id'])
            if (getattr(self, '_exercise_room_continuation', False)
                    and not getattr(self, '_exercise_unpaid_zero_deposit', False)):
                return
            if getattr(self, '_exercise_unpaid_zero_deposit', False):
                from orders.tests.points_unpaid_zero_flow import exercise_unpaid_zero_deposit
                exercise_unpaid_zero_deposit(self, order=order, case_id=case_id, buyer=buyer,
                    account=account, accounts=accounts, players=players, boss=boss,
                    client=client, admin=admin, staff=staff, execution_url=execution_url)
                return
            # B may seek a later aftersale before A's first report has become
            # payable and before the store has filled the funding shortfall.
            # Until this new case has full execution support, it is preview
            # only and cannot be accepted as a payable agreement.
            with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True):
                from orders.points_followup_preview import preview_followup
                unpaid = preview_followup(case_id, refund_equivalent_units=1000)
                self.assertFalse(unpaid['prior_paid'])
                self.assertEqual((unpaid['settled_other_provider_units'], unpaid['current_required_units'],
                                  unpaid['conditional_funding_revision'].allocated_units,
                                  unpaid['conditional_funding_revision'].required_units), (0, 110, 0, 0))
                unpaid_preview = client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
                self.assertTrue(unpaid_preview.data['data']['read_only'])
                self.assertFalse(unpaid_preview.data['data']['agreement_available'])
                self.assertTrue(client.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/').data['data']['followup_preview_only'])
                refused = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'unpaid-followup', 'expected_version': 1, 'refund_units': 1000,
                    'provider_terms': [{'account_id': accounts[1].pk, 'retained_units': 4250}],
                    'explanation': '未结算先核对'}, format='json')
                self.assertNotEqual(refused.data.get('code', 400), 0, refused.data)
                self.assertEqual(order.refund_records.count(), 1)
                from django.db import transaction as django_transaction
                from uuid import uuid4
                with django_transaction.atomic(using='default'), transaction.atomic(), override_settings(
                        SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True, SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
                        SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
                        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True):
                    self.assertEqual(self.verify().status_code, 200)
                    partially_funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 50})
                    self.assertEqual(partially_funded.status_code, 200, partially_funded.data)
                    unpaid_preview = client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 10})
                    self.assertTrue(unpaid_preview.data['data']['agreement_available'])
                    self.assertTrue(unpaid_preview.data['data']['execution_available'])
                    funds_url = f'/api/admin/aftersales/{case_id}/negotiation-funding/'
                    checked = client.get(funds_url, {'refund_units': 10})
                    self.assertEqual(checked.data['code'], 0, checked.data)
                    self.assertTrue(checked.data['data']['execution_check_required'])
                    self.assertTrue(checked.data['data']['executable'])
                    self.assertEqual(checked.data['data']['providers'], [{
                        'account_id': accounts[1].pk, 'pending_income_units': 10,
                        'wallet_units': 0, 'deposit_units': 0, 'shortfall_units': 0,
                    }])
                    proposal_url = f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
                    proposal_body = {
                        'request_id': 'unpaid-internal-proposal', 'expected_version': 1, 'refund_units': 10,
                        'provider_terms': [{'account_id': accounts[1].pk, 'retained_units': 5240}],
                        'explanation': '未结算的后续售后隔离测试',
                    }
                    proposal_response = client.post(proposal_url, proposal_body, format='json')
                    self.assertEqual(proposal_response.data['code'], 0, proposal_response.data)
                    self.assertTrue(proposal_response.data['data']['points_execution_available'])
                    unpaid_proposal = proposal_response.data['data']['id']
                    replay = client.post(proposal_url, proposal_body, format='json')
                    self.assertEqual(replay.data['data']['id'], unpaid_proposal)
                    different = {**proposal_body, 'explanation': '同号换成另一份协商说明'}
                    self.assertNotEqual(client.post(proposal_url, different, format='json').data.get('code'), 0)
                    decision_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                    decision = {'proposal_id': unpaid_proposal, 'accepted': True}
                    self.assertEqual(players[0].post(decision_url, decision, format='json').status_code, 404)
                    for actor in (boss, players[1]):
                        confirmation = actor.post(decision_url, decision, format='json')
                        self.assertEqual(confirmation.data['code'], 0, confirmation.data)
                    from orders.points_followup_execution import post_followup_locked
                    from orders.points_followup_termination import execute_followup_and_close
                    before_unpaid = (order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count())
                    unpaid_request = {'proposal_id': unpaid_proposal, 'client_request_id': 'unpaid-internal-post'}
                    self.assertEqual(players[1].post(execution_url, unpaid_request, format='json').status_code, 403)
                    with store_context(self.first):
                        other_user = make_console_user(['order:view', 'support:edit', 'order:refund'])
                        other_account = get_or_create_account_for_legacy_user(other_user)
                        other_user.admin_membership.account = other_account
                        other_user.admin_membership.save(update_fields=['account'])
                        other_client = APIClient(HTTP_HOST='admin.esports.club',
                            HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
                        self.assertNotEqual(other_client.post(execution_url, unpaid_request, format='json').status_code, 200)
                    with override_settings(SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=False):
                        self.assertFalse(client.get(execution_url).data['data']['execution_enabled'])
                        denied = client.post(execution_url, unpaid_request, format='json')
                        self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
                    self.assertEqual(order.refund_records.count(), 1)
                    with patch('orders.models.PointsNegotiationPostingShare.objects.create',
                               side_effect=RuntimeError('未结算承担凭证失败')):
                        with self.assertRaisesMessage(RuntimeError, '未结算承担凭证失败'):
                            client.post(execution_url, unpaid_request, format='json')
                    self.assertEqual((order.refund_records.count(), Transaction.objects.count(),
                                      PointLedger.objects.count()), before_unpaid)
                    self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'OPEN')
                    posted = client.post(execution_url, unpaid_request, format='json')
                    self.assertEqual(posted.data['code'], 0, posted.data)
                    from orders.models import PointsNegotiationPosting
                    posting_unpaid = PointsNegotiationPosting.objects.get(proposal_id=unpaid_proposal)
                    self.assertEqual(posted.data['data']['execution_id'], posting_unpaid.pk)
                    self.assertEqual(client.post(execution_url, unpaid_request, format='json').data['data']['execution_id'], posting_unpaid.pk)
                    self.assertEqual(client.get(execution_url, unpaid_request).data['data']['execution_id'], posting_unpaid.pk)
                    competing = {**unpaid_request, 'client_request_id': 'competing-unpaid-refund'}
                    for rejected in (client.post(execution_url, competing, format='json'),
                                     client.get(execution_url, competing)):
                        self.assertEqual(rejected.status_code, 409, rejected.data)
                        self.assertEqual(rejected.data['errorCode'], 'REFUND_REQUEST_CONFLICT')
                        self.assertNotIn('data', rejected.data)
                    with transaction.atomic():
                        AfterSale.objects.filter(pk=case_id).update(history=[])
                        unknown = client.get(execution_url, competing)
                        self.assertEqual(unknown.status_code, 400, unknown.data)
                        self.assertNotIn('errorCode', unknown.data)
                        transaction.set_rollback(True)
                    revision_unpaid = posting_unpaid.followup_funding_revision
                    self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'PARTIALLY_REFUNDED')
                    self.assertIsNone(revision_unpaid.prior_settlement_id)
                    self.assertIsNone(revision_unpaid.paid_person_id)
                    self.assertEqual((revision_unpaid.paid_provider_units, revision_unpaid.required_units,
                                      revision_unpaid.allocated_units, revision_unpaid.shortfall_units), (0, 100, 50, 50))
                    self.assertEqual((posting_unpaid.points_refund.coin_units,
                                      posting_unpaid.shares.get().pending_deduction), (0, 10))
                    replay_unpaid, _ = post_followup_locked(case_id, unpaid_proposal, operator=staff,
                        legacy_operator=admin, request_id='unpaid-internal-post')
                    self.assertEqual(replay_unpaid.pk, posting_unpaid.pk)
                    from orders.points_followup_funding import verify_followup_funding
                    unpaid_funding = verify_followup_funding(order.pk)
                    self.assertEqual((unpaid_funding.required, unpaid_funding.allocated,
                                      unpaid_funding.remaining, unpaid_funding.fully_funded), (100, 50, 50, False))
                    from orders.services import settle_order
                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                    first_extra = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 20}
                    self.assertEqual(self.fund(first_extra).status_code, 200)
                    self.assertEqual(self.fund(first_extra).status_code, 200)
                    self.assertEqual((verify_followup_funding(order.pk).allocated,
                                      verify_followup_funding(order.pk).remaining), (70, 30))
                    from django.db import DatabaseError
                    from stores.models import PlatformAuditEvent, PlatformPointsFundingOperation
                    original_audit_create = PlatformAuditEvent.objects.create
                    def fail_completion(**kwargs):
                        if kwargs.get('action') == 'points_funding.allocate_completed':
                            raise DatabaseError('模拟平台完成记录失败')
                        return original_audit_create(**kwargs)
                    pending_request = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 30}
                    with patch('stores.platform_points_funding.PlatformAuditEvent.objects.create', side_effect=fail_completion):
                        uncertain = self.fund(pending_request)
                    self.assertEqual((uncertain.status_code, uncertain.data['errorCode']), (503, 'POINTS_FUNDING_UNCERTAIN'))
                    from orders.points_funding_plan import PointsFundingPlanError
                    with self.assertRaises(PointsFundingPlanError):
                        verify_followup_funding(order.pk)
                    operation = PlatformPointsFundingOperation.objects.using('default').get(request_id=pending_request['request_id'])
                    from stores import test_platform_pass_repayment as platform_tests
                    _, recovery_token = platform_tests.PlatformPassRepaymentTests.other_finance(self)
                    recovery = self.client.post(f'/api/admin/platform/points-funding/xinan/{operation.pk}/recover', {},
                        format='json', HTTP_AUTHORIZATION='Bearer ' + recovery_token)
                    self.assertEqual(recovery.status_code, 200, recovery.data)
                    complete = verify_followup_funding(order.pk)
                    self.assertEqual((complete.required, complete.allocated, complete.remaining, complete.fully_funded),
                                     (100, 100, 0, True))
                    self.assertEqual(self.fund(pending_request).status_code, 200)
                    self.assertNotEqual(self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1}).status_code, 200)
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME',
                        remark=f'订单收入：{order.order_no}').exists())
                    from wallet.models import ProviderReport
                    reports_unpaid = [ProviderReport.objects.get(order=order, provider_account=target) for target in accounts]
                    for player, report, kinds in ((players[0], reports_unpaid[0], ('COMPLETION', 'RESULT')),
                                                  (players[1], reports_unpaid[1], ('RESULT',))):
                        for kind in kinds:
                            response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                                   {'kind': kind, 'image': screenshot()}, format='multipart')
                            self.assertEqual(response.data['code'], 0, response.data)
                    reviewer_user = make_console_user(['report:view', 'report:audit'])
                    reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
                    reviewer_user.admin_membership.account = reviewer_account
                    reviewer_user.admin_membership.save(update_fields=['account'])
                    reviewer = APIClient(HTTP_HOST='admin.esports.club',
                        HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
                    # This scenario may run inside the shared-receipt test's
                    # frozen clock. Keep the later audit distinguishable from
                    # the already committed second termination.
                    submitted_at = timezone.now() + timedelta(seconds=1)
                    with patch('django.utils.timezone.now', return_value=submitted_at):
                        submitted = players[0].post(f'/api/wallet/reports/{reports_unpaid[0].pk}/submit/', {}, format='json')
                        self.assertEqual(submitted.data['code'], 0, submitted.data)
                        approved = reviewer.post(f'/api/admin/reports/{reports_unpaid[0].pk}/approve/', {}, format='json')
                        self.assertEqual(approved.data['code'], 0, approved.data)
                    reports_unpaid[0].refresh_from_db()
                    with patch('django.utils.timezone.now', return_value=reports_unpaid[0].submitted_at + timedelta(hours=24)):
                        submitted = players[1].post(f'/api/wallet/reports/{reports_unpaid[1].pk}/submit/', {}, format='json')
                        self.assertEqual(submitted.data['code'], 0, submitted.data)
                        approved = reviewer.post(f'/api/admin/reports/{reports_unpaid[1].pk}/approve/', {}, format='json')
                        self.assertEqual(approved.data['code'], 0, approved.data)
                    reports_unpaid[1].refresh_from_db()
                    with patch('django.utils.timezone.now', return_value=reports_unpaid[0].submitted_at + timedelta(hours=73)):
                        from orders.models import OrderPointsRefundSettlementPerson
                        before_income = Transaction.objects.count()
                        with patch.object(OrderPointsRefundSettlementPerson.objects, 'create',
                                          side_effect=RuntimeError('逐人收益凭证失败')):
                            with self.assertRaisesMessage(RuntimeError, '逐人收益凭证失败'):
                                settle_order(Order.objects.select_for_update().get(pk=order.pk))
                        self.assertEqual(Transaction.objects.count(), before_income)
                        self.assertFalse(order.providers.filter(settled_at__isnull=False).exists())
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                         [reports_unpaid[0].provider_id])
                        self.assertIsNone(Order.objects.get(pk=order.pk).settled_at)
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                        from engagement.models import ConsumptionConfirmation
                        self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                        from orders.points_settlement_test import verified_person_settlement_display
                        self.assertEqual([verified_person_settlement_display(row)[0] for row in reports_unpaid],
                                         ['SETTLED', 'WAITING'])
                        with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                               SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                            from engagement.confirmation import process_order
                            self.assertFalse(process_order(order.pk))
                            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    with patch('django.utils.timezone.now', return_value=reports_unpaid[1].submitted_at + timedelta(hours=73)):
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                         [reports_unpaid[1].provider_id])
                        order.refresh_from_db()
                        self.assertIsNotNone(order.settled_at)
                        from orders.points_followup_settlement import verify_followup_settlement_locked
                        complete_settlement = verify_followup_settlement_locked(order)
                        self.assertEqual(sorted(complete_settlement.persons.values_list('amount', flat=True)), [5240, 5500])
                        self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                        from orders.points_settlement_test import verified_order_settlement_display, verified_person_settlement_display
                        self.assertEqual([verified_person_settlement_display(row)[0] for row in reports_unpaid],
                                         ['SETTLED', 'SETTLED'])
                        self.assertEqual(verified_order_settlement_display(order), 'SETTLED')
                        with override_settings(SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=False):
                            self.assertEqual([verified_person_settlement_display(row)[0] for row in reports_unpaid],
                                             ['SETTLED', 'SETTLED'])
                            self.assertEqual(client.get(execution_url, unpaid_request).data['data']['execution_id'],
                                             posting_unpaid.pk)
                            self.assertNotEqual(client.post(execution_url, unpaid_request, format='json').data.get('code', 400), 0)
                        with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                               SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                            from engagement.confirmation import process_order
                            self.assertTrue(process_order(order.pk))
                            self.assertFalse(process_order(order.pk))
                        with transaction.atomic():
                            Transaction.objects.filter(pk=complete_settlement.persons.get(
                                report=reports_unpaid[0]).income_transaction_id).update(amount=1)
                            from orders.points_funding_plan import PointsFundingPlanError
                            with self.assertRaises(PointsFundingPlanError):
                                verify_followup_funding(order.pk)
                            transaction.set_rollback(True)
                    transaction.set_rollback(True)
                    django_transaction.set_rollback(True, using='default')
            from orders.negotiation_aftersale_scope import closed_provider_accounts
            with transaction.atomic():
                self.assertEqual(closed_provider_accounts(order), {accounts[0].pk})
            # A and B have independent report clocks. B's new open case must
            # not hold A's already agreed earnings after the whole order ends.
            from datetime import timedelta
            from uuid import uuid4
            from orders.points_funding_evidence import verify_order_funding
            from orders.points_settlement_test import verified_person_settlement_display
            from orders.services import settle_order
            from wallet.models import ProviderReport
            reports = [ProviderReport.objects.get(order=order, provider_account=target) for target in accounts]
            for player, report, kinds in ((players[0], reports[0], ('COMPLETION', 'RESULT')),
                                          (players[1], reports[1], ('RESULT',))):
                for kind in kinds:
                    response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                           {'kind': kind, 'image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
            reviewer_user = make_console_user(['report:view', 'report:audit'])
            reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
            reviewer_user.admin_membership.account = reviewer_account
            reviewer_user.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
            response = players[0].post(f'/api/wallet/reports/{reports[0].pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            response = reviewer.post(f'/api/admin/reports/{reports[0].pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            reports[0].refresh_from_db()
            later = reports[0].submitted_at + timedelta(hours=24)
            with patch('django.utils.timezone.now', return_value=later):
                response = players[1].post(f'/api/wallet/reports/{reports[1].pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = reviewer.post(f'/api/admin/reports/{reports[1].pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
            reports[1].refresh_from_db()
            with transaction.atomic():
                funding = verify_order_funding(order.pk)
                self.assertGreater(funding.remaining, 0)
            self.assertEqual(self.verify().status_code, 200)
            funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': funding.remaining})
            self.assertEqual(funded.status_code, 200, funded.data)
            first_ready = reports[0].submitted_at + timedelta(hours=73)
            followup_time = first_ready
            with patch('django.utils.timezone.now', side_effect=lambda: followup_time):
                with transaction.atomic():
                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [reports[0].provider_id])
                self.assertEqual(verified_person_settlement_display(reports[0])[0], 'SETTLED')
                self.assertEqual(verified_person_settlement_display(reports[1])[0], 'WAITING')
                from orders.points_followup_preview import preview_followup
                before = (order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count())
                with transaction.atomic():
                    preview = preview_followup(case_id, refund_equivalent_units=1000)
                self.assertEqual((preview['account_id'], preview['settled_other_provider_units'],
                                  preview['retained_units'], preview['additional_provider_units']),
                                 (accounts[1].pk, 5500, 4250, 0))
                self.assertEqual((preview['refund_coin_units'], preview['refund_points_hundredths']), (730, 666))
                self.assertFalse(preview['execution_available'])
                self.assertEqual(preview['conditional_funding'].provider_income_units, 9750)
                self.assertEqual((preview['current_required_units'], preview['conditional_funding'].required_units,
                                  preview['conditional_funding'].release_units), (110, 0, 110))
                revision = preview['conditional_funding_revision']
                self.assertEqual((revision.paid_provider_units, revision.remaining_provider_units,
                                  revision.release_units, revision.shortfall_units), (5500, 4250, 110, 0))
                self.assertEqual(sum(row[2] for row in revision.releases), 110)
                endpoint = f'/api/admin/aftersales/{case_id}/negotiation-preview/'
                response = client.get(endpoint, {'refund_units': 1000})
                self.assertEqual(response.data['code'], 0, response.data)
                public = response.data['data']
                self.assertEqual(public['provider_terms'][0]['account_id'], accounts[1].pk)
                self.assertEqual(public['provider_terms'][0]['retained_units'], 4250)
                self.assertTrue(public['read_only'])
                self.assertFalse(public['agreement_available'])
                self.assertFalse(public['execution_available'])
                self.assertNotIn('settled_other_provider_units', public)
                self.assertEqual((public['conditional_funding']['required_units'],
                                  public['conditional_funding']['release_units']), (0, 110))
                self.assertNotIn('conditional_funding_revision', public)
                self.assertNotEqual(players[1].get(endpoint, {'refund_units': 1000}).status_code, 200)
                funds_url = f'/api/admin/aftersales/{case_id}/negotiation-funding/'
                checked = client.get(funds_url, {'refund_units': 1000})
                self.assertEqual(checked.data['code'], 0, checked.data)
                self.assertTrue(checked.data['data']['book_amounts_sufficient'])
                self.assertTrue(checked.data['data']['execution_check_required'])
                self.assertFalse(checked.data['data']['executable'])
                self.assertEqual(checked.data['data']['providers'], [{
                    'account_id': accounts[1].pk, 'pending_income_units': 1000,
                    'wallet_units': 0, 'deposit_units': 0, 'shortfall_units': 0,
                }])
                self.assertEqual(players[1].get(funds_url, {'refund_units': 1000}).status_code, 403)
                with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True,
                                       NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True):
                    availability = client.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/').data['data']
                    self.assertTrue(availability['followup_preview_only'])
                    self.assertFalse(availability['execution_available'])
                self.assertEqual((order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count()), before)
                with override_settings(SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING=False), transaction.atomic():
                    with self.assertRaises(ValidationError):
                        preview_followup(case_id, refund_equivalent_units=1000)
                with transaction.atomic():
                    AfterSale.objects.filter(pk=case_id).update(affected_provider_account_ids=[accounts[0].pk])
                    with self.assertRaises(ValidationError):
                        preview_followup(case_id, refund_equivalent_units=1000)
                    transaction.set_rollback(True)
                with transaction.atomic():
                    paid_a = Transaction.objects.get(order=order, tx_type='INCOME', wallet__account=accounts[0],
                                                     remark=f'订单收入：{order.order_no}')
                    Transaction.objects.filter(pk=paid_a.pk).update(balance_after=1)
                    with self.assertRaises(ValidationError):
                        preview_followup(case_id, refund_equivalent_units=1000)
                    transaction.set_rollback(True)
                with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True):
                    final_body = {'request_id': 'points-double-followup-final', 'expected_version': 1,
                                  'refund_units': 14500, 'provider_terms': [
                                      {'account_id': accounts[1].pk, 'retained_units': 0}],
                                  'explanation': '测试剩余支付构成全部退回'}
                    final_proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/',
                                                 final_body, format='json')
                    self.assertEqual(final_proposal.data['code'], 0, final_proposal.data)
                    final_data = final_proposal.data['data']
                    self.assertEqual((final_data['refund_coin_units'], final_data['refund_points_hundredths'],
                                      final_data['refund_is_final']), (10640, 9667, True))
                    self.assertFalse(final_data['points_execution_available'])
                    final_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                    for actor in (boss, players[1]):
                        confirmed = actor.post(final_url, {'proposal_id': final_data['id'], 'accepted': True}, format='json')
                        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                    followup_time += timedelta(seconds=1)
                    from orders.points_followup_execution import prepare_followup_locked
                    target_assignment = OrderProvider.objects.get(order=order, provider_account=accounts[1])
                    target_wallet = get_wallet(account_id=accounts[1].pk, user_id=target_assignment.provider_id)
                    target_wallet.balance = 1200; target_wallet.save(update_fields=['balance'])
                    profile = EscortProfile.objects.get(account=accounts[1])
                    profile.deposit_mode = 'CASH'; profile.deposit_paid = 0; profile.deposit_required = 10000
                    profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
                    topup_url = f'/api/admin/escorts/{profile.pk}/deposit-topups/'
                    first_topup = client.post(topup_url, {'amount': 8000, 'reason': '线下押金隔离测试',
                        'reference': 'FOLLOWUP-DEPOSIT-1', 'client_request_id': 'followup-cash-topup-1'}, format='multipart')
                    self.assertEqual(first_topup.data['code'], 0, first_topup.data)
                    with override_settings(SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True,
                                           SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
                                           SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
                                           SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True,
                                           REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                                           REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                                           SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True):
                        with self.assertRaisesMessage(ValidationError, '不足'):
                            prepare_followup_locked(case_id, final_data['id'], operator=staff)
                        second_topup = client.post(topup_url, {'amount': 50, 'reason': '线下押金隔离补差',
                            'reference': 'FOLLOWUP-DEPOSIT-2', 'client_request_id': 'followup-cash-topup-2'}, format='multipart')
                        self.assertEqual(second_topup.data['code'], 0, second_topup.data)
                        conditional = prepare_followup_locked(case_id, final_data['id'], operator=staff)
                        self.assertEqual((conditional['pending_units'], conditional['wallet_units'],
                                          conditional['deposit_units']), (5250, 1200, 8050))
                        with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True,
                                                                     SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True):
                            from orders.points_followup_termination import execute_followup_and_close
                            closed = execute_followup_and_close(case_id, final_data['id'], operator=staff,
                                legacy_operator=admin, request_id='points-followup-full-close')
                            order.refresh_from_db()
                            self.assertEqual((order.payment_status, AfterSale.objects.get(pk=case_id).status),
                                             ('REFUNDED', 'REFUNDED'))
                            self.assertEqual(execute_followup_and_close(case_id, final_data['id'], operator=staff,
                                legacy_operator=admin, request_id='points-followup-full-close').pk, closed.pk)
                            self.assertEqual(Transaction.objects.get(pk=paid_a.pk).amount, 5500)
                            from orders.points_followup_funding import verify_followup_funding
                            funding = verify_followup_funding(order.pk)
                            self.assertEqual((funding.required, funding.allocated, funding.remaining,
                                              funding.fully_funded), (0, 0, 0, True))
                            with override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True), \
                                    patch('django.utils.timezone.now', return_value=reports[1].submitted_at + timedelta(hours=73)):
                                with transaction.atomic(), override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                        SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                                    from engagement.tasks import confirm_due_consumption
                                    from engagement.models import RefundedPointsConsumptionEvidence
                                    confirm_due_consumption()
                                    self.assertIsNotNone(Order.objects.get(pk=order.pk).settled_at)
                                    scheduled = RefundedPointsConsumptionEvidence.objects.get(
                                        settlement__posting__proposal_id=final_data['id'])
                                    self.assertEqual((scheduled.net_paid_units, scheduled.awarded_points), (0, 0))
                                    transaction.set_rollback(True)
                                with transaction.atomic():
                                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                                     [reports[1].provider_id])
                                    order.refresh_from_db(); self.assertIsNotNone(order.settled_at)
                                    from orders.points_followup_settlement import verify_followup_settlement_locked
                                    final_settlement = verify_followup_settlement_locked(order)
                                    self.assertEqual(final_settlement.persons.get().amount, 0)
                                    self.assertIsNone(final_settlement.persons.get().income_transaction_id)
                                    with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                            SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                                        from engagement.confirmation import process_order
                                        from engagement.models import RefundedPointsConsumptionEvidence
                                        self.assertFalse(process_order(order.pk))
                                        benefits = RefundedPointsConsumptionEvidence.objects.get(settlement=final_settlement)
                                        self.assertEqual((benefits.net_paid_units, benefits.net_principal_units,
                                                          benefits.awarded_points), (0, 0, 0))
                                        self.assertFalse(process_order(order.pk))
                                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                            transaction.set_rollback(True)
                        order.refresh_from_db()
                        target_wallet.refresh_from_db()
                        self.assertEqual(target_wallet.balance, 1200)
                        from orders.points_followup_execution import post_followup_locked
                        from orders.models import PointsNegotiationPostingShare
                        buyer_wallet.refresh_from_db(); member.refresh_from_db(); profile.refresh_from_db()
                        initial = (buyer_wallet.balance, member.points_balance, target_wallet.balance, profile.deposit_paid,
                                   Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count())
                        with patch.object(PointsNegotiationPostingShare.objects, 'create',
                                          side_effect=RuntimeError('模拟后续押金承担凭证失败')):
                            with self.assertRaisesMessage(RuntimeError, '模拟后续押金承担凭证失败'):
                                post_followup_locked(case_id, final_data['id'], operator=staff, legacy_operator=admin,
                                                     request_id='points-followup-full-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db(); target_wallet.refresh_from_db(); profile.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance, member.points_balance, target_wallet.balance, profile.deposit_paid,
                                          Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count()), initial)
                        from orders.models import OrderPointsFollowupFundingReleaseEvidence
                        with patch.object(OrderPointsFollowupFundingReleaseEvidence.objects, 'create',
                                          side_effect=RuntimeError('模拟后续补资释放失败')):
                            with self.assertRaisesMessage(RuntimeError, '模拟后续补资释放失败'):
                                post_followup_locked(case_id, final_data['id'], operator=staff, legacy_operator=admin,
                                                     request_id='points-followup-full-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db(); target_wallet.refresh_from_db(); profile.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance, member.points_balance, target_wallet.balance, profile.deposit_paid,
                                          Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count()), initial)
                        from orders.models import OrderPointsFollowupFundingRevision
                        with patch.object(OrderPointsFollowupFundingRevision.objects, 'create',
                                          side_effect=RuntimeError('模拟后续资金版本凭证失败')):
                            with self.assertRaisesMessage(RuntimeError, '模拟后续资金版本凭证失败'):
                                post_followup_locked(case_id, final_data['id'], operator=staff, legacy_operator=admin,
                                                     request_id='points-followup-full-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db(); target_wallet.refresh_from_db(); profile.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance, member.points_balance, target_wallet.balance, profile.deposit_paid,
                                          Transaction.objects.count(), PointLedger.objects.count(), order.refund_records.count()), initial)
                        posting, revision = post_followup_locked(case_id, final_data['id'], operator=staff, legacy_operator=admin,
                                                                 request_id='points-followup-full-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db(); target_wallet.refresh_from_db(); profile.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance - initial[0], member.points_balance - initial[1],
                                          target_wallet.balance, profile.deposit_paid), (10640, 9667, 0, 0))
                        share = posting.shares.get()
                        self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction,
                                          share.deposit_deficit.remaining), (5250, 1200, 8050, 8050))
                        self.assertEqual((revision.paid_provider_units, revision.release_units), (5500, 110))
                        from orders.points_negotiation_termination import verify_termination_locked
                        from orders.points_followup_revision import verify_followup_revision_locked
                        with transaction.atomic():
                            Order.objects.filter(pk=order.pk).update(payment_status='REFUNDED')
                            prior_posting = revision.prior_settlement.posting
                            with self.assertRaises(ValidationError):
                                verify_termination_locked(prior_posting)
                            self.assertEqual(verify_termination_locked(
                                prior_posting, allow_verified_later_final=True).pk, prior_posting.pk)
                            self.assertEqual(verify_followup_revision_locked(revision).paid_provider_units, 5500)
                            transaction.set_rollback(True)
                        replay, replay_revision = post_followup_locked(case_id, final_data['id'], operator=staff,
                            legacy_operator=admin, request_id='points-followup-full-post')
                        self.assertEqual((replay.pk, replay_revision.pk), (posting.pk, revision.pk))
                        self.assertEqual(Transaction.objects.get(pk=paid_a.pk).amount, 5500)
                    transaction.set_rollback(True)
                with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True):
                    enabled_preview = client.get(endpoint, {'refund_units': 1000}).data['data']
                    self.assertTrue(enabled_preview['agreement_available'])
                    self.assertFalse(enabled_preview['execution_available'])
                    proposal_url = f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
                    body = {'request_id': 'points-double-followup-proposal', 'expected_version': 1,
                            'refund_units': 1000, 'provider_terms': [
                                {'account_id': accounts[1].pk, 'retained_units': 4250}],
                            'explanation': '第二位陪玩与老板协商退款'}
                    from orders.models import NegotiationProposal, OrderPointsRefund
                    previous_count = NegotiationProposal.objects.count()
                    with patch('orders.models.PointsNegotiationTerms.objects.create',
                               side_effect=RuntimeError('模拟后续方案凭证失败')):
                        with self.assertRaisesMessage(RuntimeError, '模拟后续方案凭证失败'):
                            client.post(proposal_url, body, format='json')
                    self.assertEqual(NegotiationProposal.objects.count(), previous_count)
                    proposed = client.post(proposal_url, body, format='json')
                    self.assertEqual(proposed.data['code'], 0, proposed.data)
                    new_id = proposed.data['data']['id']
                    self.assertEqual((proposed.data['data']['refund_coin_units'],
                                      proposed.data['data']['refund_points_hundredths']), (730, 666))
                    self.assertEqual(client.post(proposal_url, body, format='json').data['data']['id'], new_id)
                    agreement_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                    self.assertEqual(players[0].get(agreement_url).status_code, 404)
                    for actor in (boss, players[1]):
                        response = actor.post(agreement_url, {'proposal_id': new_id, 'accepted': True}, format='json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    self.assertEqual(response.data['data']['status'], 'ACCEPTED')
                    followup_time += timedelta(seconds=1)
                    from orders.points_followup_execution import prepare_followup_locked
                    with self.assertRaises(ValidationError):
                        prepare_followup_locked(case_id, new_id, operator=staff)
                    with override_settings(SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True):
                        plan = prepare_followup_locked(case_id, new_id, operator=staff)
                        self.assertEqual((plan['pending_units'], plan['wallet_units'], plan['deposit_units']), (1000, 0, 0))
                        with self.assertRaises(ValidationError):
                            prepare_followup_locked(case_id, new_id, operator=accounts[0])
                    from orders.points_negotiation_preview import verified_asset_terms
                    with transaction.atomic():
                        prior = OrderPointsRefund.objects.get(redemption__order=order)
                        OrderPointsRefund.objects.filter(pk=prior.pk).update(
                            coin_units=prior.coin_units + 10, prepaid_units=prior.prepaid_units + 10)
                        with self.assertRaises(ValidationError):
                            verified_asset_terms(NegotiationProposal.objects.get(pk=new_id))
                        transaction.set_rollback(True)
                    # The next closure primitive keeps the second refund,
                    # release and audit in one savepoint, but is not exposed
                    # through the production aftersale endpoint.
                    with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True,
                            SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
                            SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True, SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True,
                            SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True):
                        from orders.points_followup_termination import execute_followup_and_close
                        before_closure = (order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count())
                        with transaction.atomic():
                            from engagement.models import ConsumptionConfirmation
                            ConsumptionConfirmation.objects.create(order=order, account=account,
                                business_cycle=order.business_cycle, amount=order.amount)
                            with self.assertRaises(ValidationError):
                                execute_followup_and_close(case_id, new_id, operator=staff, legacy_operator=admin,
                                                           request_id='followup-orphan-consumption')
                            self.assertEqual(order.refund_records.count(), before_closure[0])
                            transaction.set_rollback(True)
                        with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
                                SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True,
                                REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True):
                            self.assertTrue(client.get(endpoint, {'refund_units': 1000}).data['data']['execution_available'])
                            self.assertTrue(client.get(proposal_url).data['data']['execution_available'])
                            second_request = {'proposal_id': new_id, 'client_request_id': 'followup-endpoint'}
                            denied = players[1].post(execution_url, second_request, format='json')
                            self.assertNotEqual(denied.status_code, 200)
                            with store_context(self.first):
                                other_user = make_console_user(['order:view', 'support:edit', 'order:refund'])
                                other_account = get_or_create_account_for_legacy_user(other_user)
                                other_user.admin_membership.account = other_account
                                other_user.admin_membership.save(update_fields=['account'])
                                other_client = APIClient(HTTP_HOST='admin.esports.club',
                                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_account)[0])
                                crossed = other_client.post(execution_url, second_request, format='json')
                                self.assertNotEqual(crossed.status_code, 200)
                            with override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=False):
                                unavailable = client.post(execution_url, second_request, format='json')
                                self.assertNotEqual(unavailable.data.get('code', 400), 0, unavailable.data)
                                self.assertEqual(order.refund_records.count(), before_closure[0])
                            executed_followup = client.post(execution_url, second_request, format='json')
                            self.assertEqual(executed_followup.data['code'], 0, executed_followup.data)
                            receipt = executed_followup.data['data']
                            self.assertEqual(receipt['refund_coin_units'], 730)
                            self.assertEqual(client.get(execution_url, second_request).data['data'], receipt)
                            self.assertEqual(client.post(execution_url, second_request, format='json').data['data'], receipt)

                            peer_user = make_console_user(['order:view', 'support:edit', 'order:refund'])
                            peer_account = get_or_create_account_for_legacy_user(peer_user)
                            peer_user.admin_membership.account = peer_account
                            peer_user.admin_membership.save(update_fields=['account'])
                            self.assertNotEqual(peer_account.pk, staff.pk)
                            peer = APIClient(HTTP_HOST='admin.esports.club',
                                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(peer_account)[0])
                            self.assertEqual(peer.get(execution_url).data['data']['proposal']['id'], new_id)
                            competing = {**second_request, 'client_request_id': 'followup-peer-refund'}
                            before_replays = (order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count())
                            for rejected in (peer.get(execution_url, competing),
                                             peer.post(execution_url, competing, format='json')):
                                self.assertEqual(rejected.status_code, 409, rejected.data)
                                self.assertEqual(rejected.data['errorCode'], 'REFUND_REQUEST_CONFLICT')
                                self.assertNotIn('data', rejected.data)
                            for rejected in (peer.get(execution_url, second_request),
                                             peer.post(execution_url, second_request, format='json')):
                                self.assertEqual(rejected.status_code, 400, rejected.data)
                                self.assertNotIn('errorCode', rejected.data)
                                self.assertNotIn('data', rejected.data)
                            self.assertEqual((order.refund_records.count(), Transaction.objects.count(),
                                              PointLedger.objects.count()), before_replays)
                            from console.models import AdminAuditLog
                            refund = order.refund_records.get(pk=receipt['refund_id'])
                            audit = AdminAuditLog.objects.get(path=execution_url, request_body__refund_id=refund.pk)
                            self.assertEqual(refund.operator_account_id, staff.pk)
                            self.assertEqual(audit.operator_account_id, staff.pk)
                            with override_settings(SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=False,
                                                   SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=False,
                                                   SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=False):
                                self.assertEqual(client.get(execution_url, second_request).data['data']['execution_id'],
                                                 executed_followup.data['data']['execution_id'])
                                disabled = client.post(execution_url, second_request, format='json')
                                self.assertNotEqual(disabled.data.get('code', 400), 0, disabled.data)
                            self.assertEqual(client.get(execution_url, request).data['data']['execution_id'],
                                             executed.data['data']['execution_id'])
                            with patch('django.utils.timezone.now', return_value=reports[1].submitted_at + timedelta(hours=73)):
                                from engagement.confirmation import process_order
                                self.assertTrue(process_order(order.pk))
                                order.refresh_from_db(); self.assertIsNotNone(order.settled_at)
                                from engagement.models import ConsumptionConfirmation
                                confirmation = ConsumptionConfirmation.objects.get(order=order)
                                self.assertEqual((confirmation.amount, confirmation.refunded_amount), (11000, 1090))
                            transaction.set_rollback(True)
                        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('后续售后审计失败')):
                            with self.assertRaisesMessage(RuntimeError, '后续售后审计失败'):
                                execute_followup_and_close(case_id, new_id, operator=staff, legacy_operator=admin,
                                                           request_id='followup-close')
                        self.assertEqual((order.refund_records.count(), Transaction.objects.count(),
                                          PointLedger.objects.count()), before_closure)
                        self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'OPEN')
                        closed = execute_followup_and_close(case_id, new_id, operator=staff, legacy_operator=admin,
                                                            request_id='followup-close')
                        self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'PARTIALLY_REFUNDED')
                        self.assertEqual(execute_followup_and_close(case_id, new_id, operator=staff,
                            legacy_operator=admin, request_id='followup-close').pk, closed.pk)
                        self.assertEqual(Transaction.objects.get(pk=paid_a.pk).amount, 5500)
                        from orders.points_followup_funding import verify_followup_funding
                        funding = verify_followup_funding(order.pk)
                        self.assertEqual((funding.required, funding.allocated, funding.remaining,
                                          funding.fully_funded), (0, 0, 0, True))
                        from wallet.reporting import report_financial_snapshot
                        self.assertEqual(reports[1].payout_amount, 5250)
                        with override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True):
                            self.assertEqual(report_financial_snapshot(reports[1])['expected_income'], 4250)
                            from wallet.serializers import ReportSerializer
                            self.assertEqual(ReportSerializer(reports[1]).data['payout_amount'], 4250)
                        # Exercise an independent scenario in which B's
                        # submitted report is approved after the follow-up
                        # agreement, so its audited payout is already net.
                        with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True):
                            from wallet.models import ProviderReport
                            ProviderReport.objects.filter(pk=reports[1].pk).update(status='PENDING', audited_at=None,
                                auditor=None, auditor_account=None, payout_amount=0)
                            with patch('django.utils.timezone.now', return_value=first_ready + timedelta(hours=1)):
                                audited_later = reviewer.post(f'/api/admin/reports/{reports[1].pk}/approve/', {}, format='json')
                                self.assertEqual(audited_later.data['code'], 0, audited_later.data)
                            reports[1].refresh_from_db()
                            self.assertEqual(reports[1].payout_amount, 4250)
                            from wallet.reporting import report_financial_snapshot
                            self.assertEqual(report_financial_snapshot(reports[1])['expected_income'], 4250)
                            with patch('django.utils.timezone.now', return_value=reports[1].submitted_at + timedelta(hours=73)):
                                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                                 [reports[1].provider_id])
                            transaction.set_rollback(True)
                        reports[1].refresh_from_db()
                        # A previously backed cash-deposit deficit is repaid
                        # from B's released income before any remainder is
                        # available in the provider wallet.
                        with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True,
                                REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True):
                            from orders.tests.factories import make_completed_order
                            from orders.models import OrderPointsRefundSettlementPerson, OrderRefund
                            from wallet.models import RefundDepositIncomeApplication
                            from wallet.refund_deposit import charge_refund_deposit
                            profile = EscortProfile.objects.get(account=accounts[1])
                            profile.deposit_mode = 'CASH'; profile.deposit_paid = 0; profile.deposit_required = 10000
                            profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
                            topup = client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                                'amount': 600, 'reason': '隔离测试历史押金', 'reference': 'FOLLOWUP-POSITIVE-GAP',
                                'client_request_id': 'followup-positive-gap'}, format='multipart')
                            self.assertEqual(topup.data['code'], 0, topup.data)
                            profile.refresh_from_db()
                            target = OrderProvider.objects.get(order=order, provider_account=accounts[1])
                            old_order = make_completed_order(order.customer, target.provider, amount=10000,
                                                             payment_status='PAID')
                            old_refund = OrderRefund.objects.create(order=old_order, amount=200, reason='隔离测试历史退款',
                                                                    operator=admin, operator_account=staff)
                            provider_wallet = get_wallet(account_id=accounts[1].pk, user_id=target.provider_id)
                            deficit = charge_refund_deposit(refund=old_refund, profile=profile, amount=200,
                                                            operator=admin, operator_account=staff)
                            balance_before = provider_wallet.balance
                            with patch('django.utils.timezone.now', return_value=reports[1].submitted_at + timedelta(hours=73)):
                                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                                 [target.provider_id])
                                provider_wallet.refresh_from_db(); deficit.refresh_from_db()
                                self.assertEqual((provider_wallet.balance - balance_before, deficit.remaining), (4050, 0))
                                person = OrderPointsRefundSettlementPerson.objects.get(report=reports[1])
                                application = RefundDepositIncomeApplication.objects.get(source_transaction=person.income_transaction)
                                self.assertEqual(application.applied_amount, 200)
                                with transaction.atomic():
                                    application.repayments.update(amount=199)
                                    from orders.settlement import SettlementError
                                    with self.assertRaises(SettlementError):
                                        settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                    transaction.set_rollback(True)
                            transaction.set_rollback(True)
                        order.refresh_from_db()
                        with transaction.atomic():
                            from orders.models import OrderPointsFundingRelease
                            from orders.points_funding_plan import PointsFundingPlanError
                            OrderPointsFundingRelease.objects.filter(posting=closed).update(amount=100)
                            with self.assertRaises(PointsFundingPlanError):
                                verify_followup_funding(order.pk)
                            transaction.set_rollback(True)
                        with override_settings(SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True):
                            self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                            self.assertIsNone(Order.objects.get(pk=order.pk).settled_at)
                            with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                                   SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                                from engagement.confirmation import process_order
                                self.assertFalse(process_order(order.pk))
                                from engagement.models import ConsumptionConfirmation
                                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                            due = reports[1].submitted_at + timedelta(hours=73)
                            with patch('django.utils.timezone.now', return_value=due):
                                from orders.models import OrderPointsRefundSettlementPerson
                                prior_count = Transaction.objects.count()
                                with patch.object(OrderPointsRefundSettlementPerson.objects, 'create',
                                                  side_effect=RuntimeError('后续收益凭证失败')):
                                    with self.assertRaisesMessage(RuntimeError, '后续收益凭证失败'):
                                        settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                self.assertEqual(Transaction.objects.count(), prior_count)
                                self.assertIsNone(Order.objects.get(pk=order.pk).settled_at)
                                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)),
                                                 [reports[1].provider_id])
                                order.refresh_from_db(); self.assertEqual(order.settled_at, due)
                                from orders.points_followup_settlement import verify_followup_settlement_locked
                                final_settlement = verify_followup_settlement_locked(order)
                                self.assertEqual(final_settlement.persons.get().amount, 4250)
                                self.assertEqual(final_settlement.persons.get().income_transaction.amount, 4250)
                                self.assertEqual(Transaction.objects.get(pk=paid_a.pk).amount, 5500)
                                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                                with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                                    from engagement.confirmation import process_order
                                    from engagement.models import ConsumptionConfirmation, RefundedPointsConsumptionEvidence
                                    member.refresh_from_db(); points_before_confirmation = member.points_balance
                                    self.assertTrue(process_order(order.pk))
                                    benefit = RefundedPointsConsumptionEvidence.objects.get(settlement=final_settlement)
                                    self.assertEqual((benefit.paid_units, benefit.refunded_coin_units,
                                                      benefit.net_paid_units, benefit.net_principal_units),
                                                     (11000, 1090, 9910, 9910))
                                    self.assertEqual(ConsumptionConfirmation.objects.get(order=order).refunded_amount, 1090)
                                    member.refresh_from_db()
                                    self.assertEqual(member.points_balance - points_before_confirmation,
                                                     benefit.awarded_points)
                                    self.assertFalse(process_order(order.pk))
                                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                                with transaction.atomic():
                                    Transaction.objects.filter(pk=final_settlement.persons.get().income_transaction_id).update(amount=1)
                                    from orders.settlement import SettlementError
                                    with self.assertRaises(SettlementError):
                                        settle_order(Order.objects.select_for_update().get(pk=order.pk))
                                    transaction.set_rollback(True)
                        with transaction.atomic():
                            from console.models import AdminAuditLog
                            AdminAuditLog.objects.filter(request_body__refund_id=closed.points_refund.refund_id).delete()
                            with self.assertRaises(ValidationError):
                                execute_followup_and_close(case_id, new_id, operator=staff, legacy_operator=admin,
                                                           request_id='followup-close')
                            transaction.set_rollback(True)
                        transaction.set_rollback(True)
                    from orders.points_followup_execution import post_followup_locked
                    from orders.models import PointsNegotiationPostingShare
                    with transaction.atomic(), override_settings(SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True,
                                                                  SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
                                                                  SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True,
                                                                  SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True):
                        buyer_wallet.refresh_from_db(); member.refresh_from_db()
                        prior_assets = (buyer_wallet.balance, member.points_balance, Transaction.objects.count(),
                                        PointLedger.objects.count(), order.refund_records.count())
                        with patch.object(PointsNegotiationPostingShare.objects, 'create',
                                          side_effect=RuntimeError('模拟第二笔承担凭证失败')):
                            with self.assertRaisesMessage(RuntimeError, '模拟第二笔承担凭证失败'):
                                post_followup_locked(case_id, new_id, operator=staff, legacy_operator=admin,
                                                     request_id='points-followup-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance, member.points_balance, Transaction.objects.count(),
                                          PointLedger.objects.count(), order.refund_records.count()), prior_assets)
                        posting, revision = post_followup_locked(case_id, new_id, operator=staff, legacy_operator=admin,
                                                                 request_id='points-followup-post')
                        buyer_wallet.refresh_from_db(); member.refresh_from_db()
                        self.assertEqual((buyer_wallet.balance - prior_assets[0], member.points_balance - prior_assets[1]),
                                         (730, 666))
                        self.assertEqual((posting.points_refund.equivalent_units, posting.shares.get().pending_deduction,
                                          posting.shares.get().retained_income), (1000, 1000, 4250))
                        self.assertEqual((revision.paid_provider_units, revision.release_units), (5500, 110))
                        from orders.points_funding_release import receipt_points_used_locked
                        from users.models import PassRefundRepaymentReceipt
                        held_receipt = PassRefundRepaymentReceipt.objects.get(pk=revision.allocations[0][1])
                        self.assertEqual(receipt_points_used_locked(held_receipt), 0)
                        callback = getattr(self, '_shared_followup_callback', None)
                        if callback:
                            callback(posting, revision, held_receipt)
                        # Shared receipts can revisit a revision whose full
                        # verification is already active in an outer frame.
                        from orders.points_followup_context import checking, is_active
                        with checking(posting.pk):
                            self.assertTrue(is_active(posting.pk))
                            self.assertEqual(receipt_points_used_locked(held_receipt), 0)
                        self.assertFalse(is_active(posting.pk))
                        replay, replay_revision = post_followup_locked(case_id, new_id, operator=staff, legacy_operator=admin,
                                                                   request_id='points-followup-post')
                        self.assertEqual((replay.pk, replay_revision.pk), (posting.pk, revision.pk))
                        from orders.models import OrderPointsFundingRelease
                        self.assertEqual(list(OrderPointsFundingRelease.objects.filter(posting=posting)
                                              .values_list('amount', flat=True)), [110])
                        with transaction.atomic():
                            OrderPointsFundingRelease.objects.filter(posting=posting).update(amount=100)
                            with self.assertRaises(ValidationError):
                                receipt_points_used_locked(held_receipt)
                            with checking(posting.pk), self.assertRaises(ValidationError):
                                receipt_points_used_locked(held_receipt)
                            transaction.set_rollback(True)
                        # A temporarily absent marker is allowed only during
                        # the surrounding verification, before any release row
                        # is written; a normal receipt reader must fail closed.
                        from orders.models import OrderPointsFollowupFundingReleaseEvidence
                        with transaction.atomic():
                            OrderPointsFundingRelease.objects.filter(posting=posting).delete()
                            OrderPointsFollowupFundingReleaseEvidence.objects.filter(revision=revision).delete()
                            with self.assertRaises(ValidationError):
                                receipt_points_used_locked(held_receipt)
                            with checking(posting.pk):
                                self.assertEqual(receipt_points_used_locked(held_receipt), 110)
                            transaction.set_rollback(True)
                        from orders.models import OrderPointsFollowupFundingRevision
                        from orders.points_followup_revision import verify_followup_revision_locked
                        with transaction.atomic():
                            OrderPointsFollowupFundingRevision.objects.filter(pk=revision.pk).update(releases=[])
                            with self.assertRaises(ValidationError):
                                verify_followup_revision_locked(revision)
                            transaction.set_rollback(True)
                        with self.assertRaises(ValidationError):
                            post_followup_locked(case_id, new_id, operator=staff, legacy_operator=admin,
                                                 request_id='different-request')
                        self.assertEqual(Transaction.objects.get(pk=paid_a.pk).amount, 5500)
                        transaction.set_rollback(True)
                    refused = client.post(execution_url, {'proposal_id': new_id,
                        'client_request_id': 'points-double-followup-execution'}, format='json')
                    self.assertNotEqual(refused.data.get('code', 400), 0, refused.data)
                    self.assertEqual((order.refund_records.count(), Transaction.objects.count(), PointLedger.objects.count()), before)
                    transaction.set_rollback(True)
                resolved = client.post(f'/api/admin/aftersales/{case_id}/resolve/',
                                       {'resolution': '已核实，双方不再申请退款'}, format='json')
                self.assertEqual(resolved.data['code'], 0, resolved.data)
                with transaction.atomic():
                    self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
            second_ready = reports[1].submitted_at + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=second_ready), transaction.atomic():
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [reports[1].provider_id])
                self.assertEqual(verified_person_settlement_display(reports[1])[0], 'SETTLED')
            amounts = list(Transaction.objects.filter(order=order, tx_type='INCOME',
                remark=f'订单收入：{order.order_no}').order_by('wallet__account_id').values_list('amount', flat=True))
            self.assertEqual(amounts, [5500, 5250])

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_addon_burden_preserves_other_person_main_refund(self):
        self._exercise_double_addon_burden = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_addon_burden_waits_for_funding_before_either_payout(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_addon_small = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_addon_small

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_addon_burden_preserves_reports_approved_before_main_refund(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_addon_preapproved = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_addon_preapproved

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_refund_then_other_players_addon_refund_settles(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_addon_refund_preserves_prior_main_funding_release(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_addon_refund_waits_for_finance(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_addon_refund_preserves_already_paid_player(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_partial_paid = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_partial_paid

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_gift_addon_refund_releases_remaining_funding(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_addon_gift = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_addon_gift

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_split_addon_refund_preserves_paid_player(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_partial_paid = True
        self._exercise_double_addon_split = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_partial_paid
            del self._exercise_double_addon_split

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_split_gift_addon_refunds_release_funding(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_addon_gift = True
        self._exercise_double_addon_split = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_addon_gift
            del self._exercise_double_addon_split

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_then_two_addon_refunds_share_other_pending_earnings(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_addon_partial_paid = True
        self._exercise_double_two_addons = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_addon_partial_paid
            del self._exercise_double_two_addons

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_refund_then_addon_purchase_and_both_addon_refunds(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_two_addons = True
        self._exercise_double_late_addon = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_two_addons
            del self._exercise_double_late_addon

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_refund_then_addon_purchase_settles_both_addons(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_two_addons = True
        self._exercise_double_late_addon = True
        self._exercise_double_keep_addons = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_two_addons
            del self._exercise_double_late_addon
            del self._exercise_double_keep_addons

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_refund_then_gift_addon_waits_for_whole_order_funding(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_two_addons = True
        self._exercise_double_late_addon = True
        self._exercise_double_keep_addons = True
        self._exercise_double_late_gift = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_two_addons
            del self._exercise_double_late_addon
            del self._exercise_double_keep_addons
            del self._exercise_double_late_gift

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_CONSUMPTION_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True)
    def test_double_main_refund_before_first_addon_purchase_settles(self):
        self._exercise_double_addon_burden = True
        self._exercise_double_main_first = True
        self._exercise_double_addon_small = True
        self._exercise_double_addon_preallocate = True
        self._exercise_double_late_addon = True
        self._exercise_double_keep_addons = True
        self._exercise_double_only_late = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_double_addon_burden
            del self._exercise_double_main_first
            del self._exercise_double_addon_small
            del self._exercise_double_addon_preallocate
            del self._exercise_double_late_addon
            del self._exercise_double_keep_addons
            del self._exercise_double_only_late

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True)
    def test_points_double_scoped_refund_other_player_completes_room(self):
        self._exercise_room_continuation = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_room_continuation

    @override_settings(ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True)
    def test_points_room_followup_refund_uses_wallet_and_deposit(self):
        self._exercise_room_continuation = True
        self._exercise_unpaid_zero_deposit = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_room_continuation
            del self._exercise_unpaid_zero_deposit

    def test_unpaid_double_full_refund_uses_wallet_and_deposit_before_zero_settlement(self):
        self._exercise_unpaid_zero_deposit = True
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_unpaid_zero_deposit

    def test_unpaid_double_preserves_reports_approved_before_first_refund(self):
        from django.utils import timezone
        current = [timezone.now()]
        self._exercise_preapproved_unpaid = current
        try:
            with patch('django.utils.timezone.now', side_effect=lambda: current[0]):
                self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._exercise_preapproved_unpaid

    def test_two_followup_refunds_share_one_platform_receipt(self):
        """Both orders use real checkout, approvals, payout and receipt APIs."""
        from orders.models import OrderPointsFundingRelease
        from orders.points_funding_release import receipt_points_used_locked

        seen = []

        def after_post(posting, revision, receipt):
            if not seen:
                seen.append((posting.pk, revision.pk, receipt.pk))
                # The outer invocation already enabled the decorator's test
                # switches; re-enter the underlying method without reusing the
                # same override_settings context manager instance.
                with override_settings(SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=False,
                                       SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=False,
                                       SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=False):
                    with transaction.atomic():
                        self.test_double_order_requires_only_boss_and_affected_provider.__wrapped__(self)
                        transaction.set_rollback(True)
                return
            self.assertEqual(len(seen), 1)
            first_posting, _, first_receipt = seen[0]
            self.assertNotEqual(first_posting, posting.pk)
            self.assertEqual(first_receipt, receipt.pk)
            self.assertEqual(receipt_points_used_locked(receipt), 0)
            # A corrupt release on either order must invalidate the shared
            # receipt even when the other order is being inspected.
            with transaction.atomic():
                OrderPointsFundingRelease.objects.filter(posting_id=first_posting).update(amount=109)
                with self.assertRaises(ValidationError):
                    receipt_points_used_locked(receipt)
                transaction.set_rollback(True)
            seen.append((posting.pk, revision.pk, receipt.pk))

        self._shared_followup_callback = after_post
        try:
            self.test_double_order_requires_only_boss_and_affected_provider()
        finally:
            del self._shared_followup_callback
        self.assertEqual(len(seen), 2)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True)
    def test_points_agreement_records_exact_assets_and_requires_both_parties(self):
        import tempfile
        from orders.tests.factories import make_provider
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            result = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '测试积分订单协商', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'points-agreement-case'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            case_id = result.data['data']['id']
            preview = client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
            self.assertTrue(preview.data['data']['agreement_available'])
            self.assertFalse(preview.data['data']['execution_available'])
            proposal_url = f'/api/admin/aftersales/{case_id}/negotiation-proposal/'
            full_body = {'request_id': 'points-agreement-full', 'expected_version': 0, 'refund_units': 6000,
                         'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 0}],
                         'explanation': '按原付款构成全额退款'}
            with patch('orders.models.PointsNegotiationTerms.objects.create', side_effect=RuntimeError('写入退款构成失败')):
                with self.assertRaisesMessage(RuntimeError, '写入退款构成失败'):
                    client.post(proposal_url, full_body, format='json')
            from orders.models import NegotiationProposal
            self.assertFalse(NegotiationProposal.objects.filter(case_id=case_id).exists())
            self.assertFalse(PointsNegotiationTerms.objects.filter(proposal__case_id=case_id).exists())
            full = client.post(proposal_url, full_body, format='json')
            self.assertEqual(full.data['code'], 0, full.data)
            self.assertEqual((full.data['data']['refund_coin_units'], full.data['data']['refund_points_hundredths'],
                              full.data['data']['refund_is_final']), (4000, 10000, True))
            full_id = full.data['data']['id']
            body = {'request_id': 'points-agreement-1', 'expected_version': 1, 'refund_units': 1000,
                    'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                    'explanation': '积分和店币按原构成退回'}
            counts_before = (Transaction.objects.count(), PointLedger.objects.count())
            result = client.post(proposal_url, body, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            proposal = result.data['data']; proposal_id = proposal['id']
            self.assertEqual((proposal['refund_units'], proposal['refund_coin_units'],
                              proposal['refund_points_hundredths'], proposal['points_execution_available']),
                             (1000, 660, 1666, False))
            terms = PointsNegotiationTerms.objects.get(proposal_id=proposal_id)
            self.assertEqual((terms.coin_units, terms.prepaid_units, terms.points_hundredths), (660, 660, 1666))
            self.assertEqual(client.post(proposal_url, body, format='json').data['data']['id'], proposal_id)
            user_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            stale = boss.post(user_url, {'proposal_id': full_id, 'accepted': True}, format='json')
            self.assertNotEqual(stale.data.get('code', 400), 0, stale.data)
            for actor in (boss, player):
                displayed = actor.get(user_url)
                self.assertEqual((displayed.data['data']['refund_coin_units'],
                                  displayed.data['data']['refund_points_hundredths']), (660, 1666))
            self.assertEqual(boss.get(user_url).data['data']['refund_coin_sources'],
                             {'prepaid_units': 660, 'gift_units': 0, 'compensation_units': 0})
            self.assertNotIn('refund_coin_sources', player.get(user_url).data['data'])
            stranger = make_provider()
            stranger_account = get_or_create_account_for_legacy_user(stranger)
            outsider = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(stranger_account)[0])
            self.assertEqual(outsider.get(user_url).status_code, 404)
            with override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=False):
                self.assertEqual(boss.get(user_url).data['data']['id'], proposal_id)
                blocked = boss.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertNotEqual(blocked.data.get('code', 400), 0, blocked.data)
            with transaction.atomic():
                PointsNegotiationTerms.objects.filter(pk=terms.pk).delete()
                missing = boss.get(user_url)
                self.assertNotEqual(missing.data.get('code', 400), 0, missing.data)
                transaction.set_rollback(True)
            PointsNegotiationTerms.objects.filter(pk=terms.pk).update(points_hundredths=1667)
            corrupted = boss.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertNotEqual(corrupted.data.get('code', 400), 0, corrupted.data)
            PointsNegotiationTerms.objects.filter(pk=terms.pk).update(points_hundredths=1666)
            first = boss.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(first.data['data']['status'], 'PENDING')
            second = player.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(second.data['data']['status'], 'ACCEPTED')
            self.assertEqual(player.post(user_url, {'proposal_id': proposal_id, 'accepted': True}, format='json').data['data']['status'], 'ACCEPTED')
            from orders.points_negotiation_execution import prepare_locked
            with override_settings(SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True), transaction.atomic():
                prepared = prepare_locked(case_id, proposal_id, operator=staff)
                item = prepared['providers'][0]
                self.assertEqual((item['pending_units'], item['wallet_units'], item['deposit_units']), (1000, 0, 0))
                self.assertEqual(prepared['calculation']['coin_units'], 660)
                self.assertEqual(prepared['calculation']['points_hundredths'], 1666)
                with self.assertRaises(ValidationError):
                    prepare_locked(case_id, proposal_id, operator=order.customer_account)
                PointsNegotiationTerms.objects.filter(pk=terms.pk).update(points_hundredths=1667)
                with self.assertRaises(ValidationError):
                    prepare_locked(case_id, proposal_id, operator=staff)
                PointsNegotiationTerms.objects.filter(pk=terms.pk).update(points_hundredths=1666)
            with transaction.atomic(), self.assertRaises(ValidationError):
                prepare_locked(case_id, proposal_id, operator=staff)
            self.assertFalse(client.get(proposal_url).data['data']['execution_available'])
            attempted = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                                    {'proposal_id': proposal_id, 'client_request_id': 'points-execution'}, format='json')
            self.assertNotEqual(attempted.data.get('code', 400), 0, attempted.data)
            denied = client.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '不能跳过确认'}, format='json')
            self.assertNotEqual(denied.data.get('code', 400), 0, denied.data)
            self.assertEqual((Transaction.objects.count(), PointLedger.objects.count()), counts_before)
            self.assertFalse(order.refund_records.exists())
            # An indivisible one-coin refund can return points but no coins.
            # Test it in a rolled-back savepoint to keep this flow independent.
            with override_settings(SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                                   SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True), transaction.atomic():
                revised = client.post(proposal_url, {'request_id': 'points-only-proposal', 'expected_version': 2,
                    'refund_units': 10, 'provider_terms': [{'account_id': assignment.provider_account_id,
                        'retained_units': 4790}], 'explanation': '仅退积分测试'}, format='json')
                self.assertEqual(revised.data['code'], 0, revised.data)
                revised_id = revised.data['data']['id']
                for actor in (boss, player):
                    confirmed = actor.post(user_url, {'proposal_id': revised_id, 'accepted': True}, format='json')
                    self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                from orders.points_negotiation_execution import post_locked
                posted = post_locked(case_id, revised_id, operator=staff, legacy_operator=admin,
                                     request_id='points-only-post')
                self.assertEqual((posted.points_refund.coin_units, posted.points_refund.points_hundredths), (0, 16))
                self.assertIsNone(posted.points_refund.coin_transaction_id)
                self.assertEqual(posted.shares.get().pending_deduction, 10)
                transaction.set_rollback(True)
            self.assertEqual((Transaction.objects.count(), PointLedger.objects.count()), counts_before)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_full_refund_preflight_checks_all_sources_before_any_posting(self):
        import tempfile
        from orders.points_negotiation_execution import prepare_locked
        from users.models import EscortProfile
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'escort:view', 'escort:deposit_topup'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            opened = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '全额协商', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'points-sources-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            response = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'points-sources-proposal', 'expected_version': 0, 'refund_units': 6000,
                'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 0}],
                'explanation': '全额协商退款'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            proposal_id = response.data['data']['id']
            url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            for actor in (boss, player):
                result = actor.post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            profile = EscortProfile.objects.get(account_id=assignment.provider_account_id)
            profile.deposit_paid = 500; profile.save(update_fields=['deposit_paid'])
            wallet = get_wallet(account_id=assignment.provider_account_id, user_id=assignment.provider_id)
            wallet.balance = 600; wallet.save(update_fields=['balance'])
            before = (wallet.balance, profile.deposit_paid, Transaction.objects.count(), PointLedger.objects.count())
            with transaction.atomic(), self.assertRaisesMessage(ValidationError, '不足'):
                prepare_locked(case_id, proposal_id, operator=staff)
            profile.deposit_paid = 600; profile.save(update_fields=['deposit_paid'])
            with transaction.atomic():
                result = prepare_locked(case_id, proposal_id, operator=staff)
                row = result['providers'][0]
                self.assertEqual((row['pending_units'], row['wallet_units'], row['deposit_units'], row['shortfall_units']),
                                 (4800, 600, 600, 0))
            wallet.refresh_from_db(); profile.refresh_from_db()
            self.assertEqual((wallet.balance, profile.deposit_paid, Transaction.objects.count(), PointLedger.objects.count()),
                             (before[0], 600, before[2], before[3]))
            self.assertFalse(order.refund_records.exists())
            # Create actual paired deposit evidence through the staff API;
            # the arbitrary profile balance above is not valid deposit proof.
            profile.deposit_paid = 0; profile.deposit_required = 10000; profile.deposit_mode = 'CASH'
            profile.save(update_fields=['deposit_paid', 'deposit_required', 'deposit_mode'])
            topup = client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 600, 'reason': '协商退款测试押金', 'reference': 'POINTS-POST-DEPOSIT',
                'client_request_id': 'points-post-topup'}, format='multipart')
            self.assertEqual(topup.data['code'], 0, topup.data)
            from orders.points_negotiation_execution import post_locked
            from orders.models import PointsNegotiationPostingShare
            from wallet.models import get_platform_wallet
            customer = get_wallet(account_id=order.customer_account_id, user_id=order.customer_id)
            member = MemberProfile.objects.get(account_id=order.customer_account_id)
            state = (customer.balance, member.points_balance, wallet.balance, get_platform_wallet().balance,
                     Transaction.objects.count(), PointLedger.objects.count())
            with transaction.atomic():
                with patch.object(PointsNegotiationPostingShare.objects, 'create', side_effect=RuntimeError('模拟凭证写入失败')):
                    with self.assertRaisesMessage(RuntimeError, '模拟凭证写入失败'):
                        post_locked(case_id, proposal_id, operator=staff, legacy_operator=admin, request_id='points-post')
                customer.refresh_from_db(); member.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db()
                self.assertEqual((customer.balance, member.points_balance, wallet.balance, get_platform_wallet().balance,
                                  Transaction.objects.count(), PointLedger.objects.count()), state)
                self.assertEqual(profile.deposit_paid, 600)
                self.assertFalse(order.refund_records.exists())
                posting = post_locked(case_id, proposal_id, operator=staff, legacy_operator=admin, request_id='points-post')
                self.assertEqual(post_locked(case_id, proposal_id, operator=staff, legacy_operator=admin,
                                             request_id='points-post').pk, posting.pk)
                share = posting.shares.get()
                self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction),
                                 (4800, 600, 600))
                self.assertEqual((posting.points_refund.coin_units, posting.points_refund.points_hundredths), (4000, 10000))
                with self.assertRaises(ValidationError):
                    post_locked(case_id, proposal_id, operator=staff, legacy_operator=admin, request_id='other-request')
                with transaction.atomic():
                    Transaction.objects.filter(pk=share.wallet_transaction_id).update(balance_before=999)
                    with self.assertRaises(ValidationError):
                        post_locked(case_id, proposal_id, operator=staff, legacy_operator=admin, request_id='points-post')
                    transaction.set_rollback(True)
            customer.refresh_from_db(); member.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db()
            self.assertEqual((customer.balance, member.points_balance, wallet.balance, profile.deposit_paid),
                             (state[0] + 4000, state[1] + 10000, 0, 0))
            self.assertEqual(order.refund_records.count(), 1)

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True)
    def test_timely_completed_case_can_preview_funding_reserved_without_any_payout(self):
        import tempfile
        from datetime import timedelta
        from uuid import uuid4
        from django.utils import timezone
        from engagement.confirmation import aftersale_deadline, process_order
        from stores import test_platform_pass_repayment as platform_tests
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                assignment = OrderProvider.objects.get(order_id=self.order.pk)
                player = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    response = player.post(f'/api/orders/orders/{self.order.pk}/{action}/',
                                           {key: screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                order = Order.objects.get(pk=self.order.pk)
                boss = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                response = boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                    'reason': '订单完成后申请售后', 'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'timely-points-aftersale'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                case_id = response.data['data']['id']
            self.assertEqual(self.verify().status_code, 200)
            self.assertEqual(self.fund().status_code, 200)
            self.assertEqual(self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 200}).status_code, 200)
            extra = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan',
                {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TIMELY-CASE-FUND', 'amount': 300})
            self.assertEqual(extra.status_code, 200, extra.data)
            claim = extra.data['data']['claim_id']
            self.assertEqual(platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify',
                             {'request_id': str(uuid4())}).status_code, 200)
            result = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 300},
                format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(result.status_code, 200, result.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=order.completed_at + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order_id=order.pk)
                self.assertFalse(settlement.persons.exists())
                self.assertFalse(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').exists())
                with transaction.atomic():
                    result = preview_locked(case_id, refund_equivalent_units=1000)
                    self.assertEqual((result['conditional_funding_if_recovered'].allocated_units,
                                      result['conditional_funding_if_recovered'].release_units), (800, 340))
                    self.assertEqual([part.amount for part in result['conditional_release_parts']], [300, 40])
                    first_use = settlement.funding_uses.order_by('pk').first()
                    OrderPointsSettlementFunding.objects.filter(pk=first_use.pk).update(amount=first_use.amount - 1)
                    with self.assertRaises(ValidationError):
                        preview_locked(case_id, refund_equivalent_units=1000)
                    transaction.set_rollback(True)
                with transaction.atomic():
                    case = AfterSale.objects.get(pk=case_id)
                    case.created_at = aftersale_deadline(Order.objects.get(pk=order.pk)); case.save(update_fields=['created_at'])
                    with self.assertRaises(ValidationError):
                        preview_locked(case_id, refund_equivalent_units=1000)
                    transaction.set_rollback(True)
                admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'reserved-funding-refund', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                    'explanation': '已预留资金的部分退款'}, format='json')
                self.assertEqual(proposal.data['code'], 0, proposal.data)
                proposal_id = proposal.data['data']['id']
                url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
                for actor in (boss, player):
                    confirmed = actor.post(url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(confirmed.data['code'], 0, confirmed.data)
                from orders.points_negotiation_termination import execute_and_terminate
                from orders.points_funding_release import receipt_points_used_locked
                from orders.models import OrderPointsFundingRelease, OrderPointsFundingReleaseEvidence
                from users.models import PassRefundRepaymentReceipt
                with transaction.atomic():
                    with patch.object(OrderPointsFundingRelease.objects, 'bulk_create', side_effect=RuntimeError('释放凭证写入失败')):
                        with self.assertRaisesMessage(RuntimeError, '释放凭证写入失败'):
                            execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=admin,
                                                  request_id='reserved-funding-post')
                    self.assertFalse(Order.objects.get(pk=order.pk).refund_records.exists())
                    self.assertFalse(OrderPointsFundingReleaseEvidence.objects.exists())
                    posting = execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=admin,
                                                    request_id='reserved-funding-post')
                    self.assertFalse(posting.termination_in_service)
                    self.assertEqual(execute_and_terminate(case_id, proposal_id, operator=staff, legacy_operator=admin,
                                                           request_id='reserved-funding-post').pk, posting.pk)
                    self.assertEqual(posting.funding_release_evidence.expected_release, 340)
                    self.assertEqual(list(posting.funding_releases.order_by('-allocation_id')
                        .values_list('amount', flat=True)), [300, 40])
                    original_receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=self.receipt_id)
                    latest_receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(
                        pk=extra.data['data']['receipt_id'])
                    self.assertEqual((receipt_points_used_locked(original_receipt),
                                      receipt_points_used_locked(latest_receipt)), (460, 0))
                    from orders.points_funding_evidence import verify_order_funding
                    funding = verify_order_funding(order.pk)
                    self.assertEqual((funding.required, funding.allocated, funding.remaining, funding.fully_funded),
                                     (460, 460, 0, True))
                    with transaction.atomic():
                        posting.funding_releases.order_by('-allocation_id').first().delete()
                        with self.assertRaises(ValidationError):
                            receipt_points_used_locked(latest_receipt)
                        transaction.set_rollback(True)
                # The original reservation remains auditable even though
                # its unused portion is returned to the same receipt.
                self.assertEqual(sum(settlement.funding_uses.values_list('amount', flat=True)), 800)
                from stores.test_platform_pass_refunds import PlatformPassRefundTests
                shortfall = PlatformPassRefundTests.create_shortfall(self, 300)
            with store_context(self.first):
                self.assertFalse(OrderPointsFundingRelease.objects.exists())
                self.assertFalse(OrderPointsFundingReleaseEvidence.objects.exists())
            allocated = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': shortfall.pk, 'amount': 150})
            self.assertEqual(allocated.status_code, 200, allocated.data)
            over = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': shortfall.pk, 'amount': 151})
            self.assertNotEqual(over.status_code, 200)
            report = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
            self.assertEqual(report.status_code, 200, report.data)
            self.assertEqual(report.data['data']['status'], 'OK', report.data)
            receipt_rows = {row['receipt']['id']: row['receipt'] for row in report.data['data']['claims']}
            self.assertEqual((receipt_rows[self.receipt_id]['allocated_amount'],
                              receipt_rows[self.receipt_id]['unallocated_amount']), (460, 40))
            second_receipt = extra.data['data']['receipt_id']
            self.assertEqual((receipt_rows[second_receipt]['allocated_amount'],
                              receipt_rows[second_receipt]['unallocated_amount']), (150, 150))
            # Split the released receipt between the pass refund and a
            # different service order, using the same receipt mutex.
            from orders.tests.test_service_funding_api import ServiceFundingApiTest
            with store_context(self.second):
                ServiceFundingApiTest.setUp(self)
                new_boss = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
                options = new_boss.post('/api/orders/discount-options-preview/',
                                        {'service_id': self.service.pk}, format='json')
                self.assertEqual(options.data['code'], 0, options.data)
                payload = ServiceFundingApiTest.payload(self, gift_balance_amount=400,
                    discount_selection_token=options.data['data']['options'][0]['selection_token'])
                created = new_boss.post('/api/orders/create/', payload, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                other_order = created.data['data']['id']
            reused = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/service-allocation',
                {'request_id': str(uuid4()), 'order_id': other_order, 'amount': 150}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(reused.status_code, 200, reused.data)
            report = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
            self.assertEqual(report.data['data']['status'], 'OK', report.data)
            receipt_rows = {row['receipt']['id']: row['receipt'] for row in report.data['data']['claims']}
            self.assertEqual((receipt_rows[second_receipt]['allocated_amount'],
                              receipt_rows[second_receipt]['unallocated_amount']), (300, 0))
            self.assertEqual((receipt_rows[self.receipt_id]['allocated_amount'],
                              receipt_rows[self.receipt_id]['unallocated_amount']), (460, 40))
            with store_context(self.second):
                from stores.db import connection
                tables = [name for name in connection.introspection.table_names()
                          if name != OrderPointsFundingRelease._meta.db_table]
                with patch.object(connection.introspection, 'table_names', return_value=tables):
                    hidden = platform_tests.PlatformPassRepaymentTests.reconciliation(self)
                self.assertEqual(hidden.data['data']['status'], 'REVIEW', hidden.data)
                self.assertTrue(all(row['receipt']['unallocated_amount'] is None
                                    for row in hidden.data['data']['claims']))

    @override_settings(SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
                       SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
                       SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                       REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_single_full_refund_ends_service_and_retains_report_audit(self):
        import tempfile
        from orders.models import OrderRefund
        from orders.negotiation_settlement import report_income
        from orders.tests.factories import make_completed_order
        from wallet.models import RefundDepositRecoveryPermit
        from wallet.models import ProviderReport
        from wallet.refund_deposit import charge_refund_deposit
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund',
                                       'escort:view', 'escort:deposit_topup'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            profile = assignment.provider.escort_profile
            profile.deposit_mode = 'CASH'; profile.deposit_paid = 0; profile.deposit_required = 10000
            profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
            topup = client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 600, 'reason': '隔离测试线下押金', 'reference': 'TEST-RECOVERY-001',
                'client_request_id': 'test-recovery-topup'}, format='multipart')
            self.assertEqual(topup.data['code'], 0, topup.data)
            get_wallet(account_id=assignment.provider_account_id, user_id=assignment.provider_id)
            previous = make_completed_order(order.customer, assignment.provider, amount=10000, payment_status='PAID')
            previous_refund = OrderRefund.objects.create(order=previous, amount=200, reason='历史退款测试凭证',
                                                         operator=admin, operator_account=staff)
            with transaction.atomic():
                deficit = charge_refund_deposit(refund=previous_refund, profile=profile, amount=200,
                                                operator=admin, operator_account=staff)
                permit = RefundDepositRecoveryPermit.objects.create(profile=profile,
                    account=assignment.provider_account, order=order)
            opened = client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                'reason': '单陪协商全退', 'affected_provider_account_ids': [assignment.provider_account_id],
                'client_request_id': 'single-full-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            result = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'single-full-proposal', 'expected_version': 0, 'refund_units': 6000,
                'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 0}],
                'explanation': '双方同意全额退款并终止'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            proposal_id = result.data['data']['id']
            for actor in (boss, player):
                response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                      {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
            wallet = get_wallet(account_id=assignment.provider_account_id, user_id=assignment.provider_id)
            wallet.balance = 1200; wallet.save(update_fields=['balance'])
            result = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'single-full-post'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual((result.data['data']['refund_equivalent_units'], result.data['data']['refund_coin_units'],
                              result.data['data']['refund_points_hundredths']), (6000, 4000, 10000))
            order.refresh_from_db(); assignment.refresh_from_db(); wallet.refresh_from_db()
            self.assertEqual((order.status, order.payment_status, wallet.balance), ('COMPLETED', 'REFUNDED', 0))
            self.assertEqual(order.completed_at, assignment.released_at)
            self.assertEqual(AfterSale.objects.get(pk=case_id).status, 'REFUNDED')
            report = ProviderReport.objects.get(order=order, provider_account=assignment.provider_account)
            self.assertEqual(report_income(report), (True, 0))
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            for kind in ('COMPLETION', 'RESULT'):
                uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': kind, 'image': screenshot()}, format='multipart')
                self.assertEqual(uploaded.data['code'], 0, uploaded.data)
            submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            reviewer_user = make_console_user(['report:view', 'report:audit'])
            reviewer_account = get_or_create_account_for_legacy_user(reviewer_user)
            reviewer_user.admin_membership.account = reviewer_account
            reviewer_user.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
            audited = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(audited.data['code'], 0, audited.data)
            report.refresh_from_db()
            self.assertEqual((report.status, report.payout_amount), ('APPROVED', 0))
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            from datetime import timedelta
            from orders.models import OrderPointsRefundSettlementPerson
            from orders.points_settlement_test import verified_person_settlement_display
            from orders.points_refunded_settlement import SettlementError
            from orders.services import settle_order
            from engagement.tasks import confirm_due_consumption
            ready = report.submitted_at + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=ready), transaction.atomic():
                with override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=False), self.assertRaises(SettlementError):
                    settle_order(Order.objects.select_for_update().get(pk=order.pk))
                permit.refresh_from_db(); self.assertIsNone(permit.closed_at)
                from engagement.models import RefundedPointsConsumptionEvidence
                with transaction.atomic(), override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                                              SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                    confirm_due_consumption()
                    self.assertEqual(RefundedPointsConsumptionEvidence.objects.get(
                        settlement__posting__proposal__case__order=order).net_paid_units, 0)
                    transaction.set_rollback(True)
                permit.refresh_from_db(); self.assertIsNone(permit.closed_at)
                # The scheduled scan includes fully refunded points orders;
                # zero earnings still settle the recovery permit once mature.
                confirm_due_consumption()
                self.assertEqual(settle_order(Order.objects.select_for_update().get(pk=order.pk)), [])
                person = OrderPointsRefundSettlementPerson.objects.get(report=report)
                self.assertEqual(person.amount, 0)
                self.assertIsNone(person.income_transaction_id)
                self.assertEqual(verified_person_settlement_display(report), ('SETTLED', ready))
                permit.refresh_from_db(); deficit.refresh_from_db()
                self.assertEqual((permit.close_reason, permit.zero_points_refund_person_id, deficit.remaining),
                                 ('ZERO_POINTS_REF_SETTLED', person.pk, 200))
                from engagement.confirmation import process_order
                from engagement.models import ConsumptionConfirmation, RefundedPointsConsumptionEvidence
                with override_settings(SERVICE_POINTS_CONSUMPTION_TESTING=True,
                                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True):
                    self.assertFalse(process_order(order.pk))
                    evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order=order)
                    self.assertEqual((evidence.net_paid_units, evidence.net_principal_units, evidence.awarded_points), (0, 0, 0))
                    self.assertIsNone(evidence.confirmation_id); self.assertIsNone(evidence.points_ledger_id)
                    self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                    self.assertFalse(process_order(order.pk))
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertEqual(Transaction.objects.get(order=order, tx_type='SHOP_INCOME').amount, 1200)

    def test_real_order_and_case_use_paid_sources_and_verified_funding_without_writes(self):
        import tempfile
        from uuid import uuid4
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                assignment = OrderProvider.objects.get(order_id=self.order.pk)
                player = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
                response = player.post(f'/api/orders/orders/{self.order.pk}/start/',
                                       {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                response = client.post('/api/admin/aftersales/open-negotiation/', {
                    'order_id': self.order.pk, 'reason': '测试协商退款',
                    'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'points-negotiation-case'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                case_id = response.data['data']['id']
            self.assertEqual(self.verify().status_code, 200)
            self.assertEqual(self.fund().status_code, 200)
            result = self.fund({'request_id': str(uuid4()), 'order_id': self.order.pk, 'amount': 200})
            self.assertEqual(result.status_code, 200, result.data)
            with store_context(self.second), transaction.atomic():
                before = (Transaction.objects.count(), PointLedger.objects.count(),
                          Order.objects.get(pk=self.order.pk).payment_status)
                result = preview_locked(case_id, refund_equivalent_units=1000)
                funding = result['conditional_funding_if_recovered']
                self.assertEqual((result['coin_units'], result['points_hundredths'], result['is_final']),
                                 (660, 1666, False))
                self.assertEqual(result['provider_terms'], [{'account_id': assignment.provider_account_id,
                    'original_income_units': 4800, 'burden_units': 1000, 'retained_units': 3800, 'additional_units': 0}])
                self.assertEqual((result['original_required_units'], funding.allocated_units,
                                  funding.required_units, funding.release_units, funding.remaining_units),
                                 (800, 500, 460, 40, 0))
                latest = OrderPointsFundingAllocation.objects.filter(redemption__order_id=self.order.pk).order_by('-pk').first()
                self.assertEqual([(row.allocation_id, row.amount) for row in result['conditional_release_parts']],
                                 [(latest.pk, 40)])
                full = preview_locked(case_id, refund_equivalent_units=6000)
                self.assertEqual((full['coin_units'], full['points_hundredths'], full['is_final']), (4000, 10000, True))
                self.assertEqual((full['provider_terms'][0]['retained_units'],
                                  full['provider_terms'][0]['additional_units']), (0, 1200))
                self.assertEqual(full['conditional_funding_if_recovered'].release_units, 500)
                for invalid in (0, 1001, 6010, True):
                    with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                        preview_locked(case_id, refund_equivalent_units=invalid)
                api = client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
                self.assertEqual(api.data['code'], 0, api.data)
                self.assertEqual((api.data['data']['refund_coin_units'], api.data['data']['refund_points_hundredths'],
                                  api.data['data']['read_only']), (660, 1666, True))
                self.assertNotIn('conditional_funding_if_recovered', api.data['data'])
                breakdown = client.get(f'/api/admin/aftersales/{case_id}/negotiation-funding/', {'refund_units': 1000})
                self.assertEqual(breakdown.data['code'], 0, breakdown.data)
                self.assertEqual((breakdown.data['data']['providers'][0]['pending_income_units'],
                                  breakdown.data['data']['providers'][0]['shortfall_units'],
                                  breakdown.data['data']['executable'], breakdown.data['data']['read_only_points']),
                                 (1000, 0, False, True))
                viewer = make_console_user(['order:view', 'support:edit'])
                viewer_account = get_or_create_account_for_legacy_user(viewer)
                viewer.admin_membership.account = viewer_account; viewer.admin_membership.save(update_fields=['account'])
                viewer_client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(viewer_account)[0])
                denied_funding = viewer_client.get(f'/api/admin/aftersales/{case_id}/negotiation-funding/',
                                                   {'refund_units': 1000})
                self.assertEqual(denied_funding.status_code, 403)
                with override_settings(SERVICE_POINTS_NEGOTIATION_TESTING=False):
                    closed = client.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
                    self.assertNotEqual(closed.data.get('code', 400), 0, closed.data)
                denied = player.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
                self.assertEqual(denied.status_code, 403)
                state = client.get(f'/api/admin/aftersales/{case_id}/negotiation-proposal/').data['data']
                self.assertFalse(state['execution_available'])
                state = client.get(f'/api/admin/aftersales/{case_id}/negotiation-execution/').data['data']
                self.assertFalse(state['execution_enabled'])
                proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'points-plan-protection', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                    'explanation': '测试协商退款'}, format='json')
                self.assertNotEqual(proposal.data.get('code', 400), 0, proposal.data)
                from orders.models import NegotiationProposal
                self.assertFalse(NegotiationProposal.objects.filter(case_id=case_id).exists())
                self.assertEqual(before, (Transaction.objects.count(), PointLedger.objects.count(),
                                          Order.objects.get(pk=self.order.pk).payment_status))
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=self.order.pk).exists())
                action = OrderPointsFundingAction.objects.filter(allocation__redemption__order_id=self.order.pk).first()
                action.result = {}; action.save(update_fields=['result'])
                with self.assertRaises(ValidationError):
                    preview_locked(case_id, refund_equivalent_units=1000)
                transaction.set_rollback(True)
            with store_context(self.first):
                self.assertFalse(OrderPointsSettlement.objects.exists())
                other_admin = make_console_user(['order:view', 'support:edit'])
                other_staff = get_or_create_account_for_legacy_user(other_admin)
                other_admin.admin_membership.account = other_staff
                other_admin.admin_membership.save(update_fields=['account'])
                other = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other_staff)[0])
                inaccessible = other.get(f'/api/admin/aftersales/{case_id}/negotiation-preview/', {'refund_units': 1000})
                self.assertEqual(inaccessible.status_code, 404)
