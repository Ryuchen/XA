"""Real specified-team checkout, finance allocation and settlement across two stores."""
import tempfile
from datetime import timedelta
from itertools import count
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation
from orders.models import OrderPointsFundingAllocation, OrderSegmentFinancialSnapshot
from orders.tests.factories import make_console_user
from orders.tests.test_grab_team_points_flow import GrabTeamPointsFlowTest
from orders.tests.test_points_redemption_preview import PointsServiceQuoteTest
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
from wallet.models import ProviderReport, Transaction


@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_TEAM_WINDOW_ENABLED=True,
    GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
    REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class SpecifiedPointsFundingTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = PointsServiceQuoteTest.payload
    prepare = GrabTeamPointsFlowTest.prepare

    def setUp(self):
        PlatformPassRepaymentTests.setUp(self)
        self.platform_client = self.client
        with store_context(self.second):
            PointsServiceQuoteTest.setUp(self)
            self.client = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])

    @override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_points_order_addon_refund_uses_deal_basis_in_operating_cost(self):
        from django.utils import timezone
        from console.test_operating_workflow import seed_payroll_fixture
        from orders.models import GameCategory, ServiceAddonConfiguration, ServiceAddonPriceOption
        from orders.tests.factories import make_service

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            game = GameCategory.objects.create(name='积分订单加购成本')
            self.service.game_category = game
            self.service.save(update_fields=['game_category'])
            addon = make_service(name='积分订单加购', game_category=game, is_addon=True)
            configuration = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
                enabled=True, conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=configuration, unit='HOUR', price=500)
            self.prepare(team_size=2, selected=True, high_discount=True, host='xa.esports.club')
            order_id = self.order.pk
            for player in self.clients:
                started = player.post(f'/api/orders/orders/{order_id}/start/',
                                      {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
            url = f'/api/orders/orders/{order_id}/addons/intents/'
            intent = self.client.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': self.accounts[0].pk,
                'request_id': 'points-roi-addon', 'expected_amount': 500,
                'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = self.client.post(url + f"{intent.data['data']['id']}/pay/", {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']
            seed_payroll_fixture()
            staff_user = make_console_user(['roi:view', 'order:view', 'order:refund', 'order:settle'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            day = timezone.localdate().isoformat()
            params = {'start_date': day, 'end_date': day}
            roi = admin.get('/api/admin/operating-roi/', params)
            self.assertEqual(roi.status_code, 200, roi.data)
            self.assertEqual((roi.data['data']['operating']['net_consumption_fen'],
                              roi.data['data']['operating']['provider_cost_fen']), (8500, 12400), roi.data)
            for player in self.clients:
                completed = player.post(f'/api/orders/orders/{order_id}/complete/',
                                        {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(completed.data['code'], 0, completed.data)
            case = self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
                'reason': '加购未履行', 'affected_provider_account_ids': [self.accounts[0].pk],
                'client_request_id': 'points-roi-addon-case'}, format='json')
            self.assertEqual(case.status_code, 200, case.data)
            refund_url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
            proposal = admin.post(refund_url, {'request_id': 'points-roi-addon-refund', 'amount_raw': 500,
                'case_id': case.data['data']['id'], 'explanation': '退回加购'}, format='json')
            self.assertEqual(proposal.status_code, 200, proposal.data)
            proposal_id = proposal.data['data']['id']
            decision_url = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
            for client in (self.client, self.clients[0]):
                decision = client.post(decision_url, {'accepted': True}, format='json')
                self.assertEqual(decision.status_code, 200, decision.data)
            result = admin.post(refund_url + f'{proposal_id}/execute/', {}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            roi = admin.get('/api/admin/operating-roi/', params)
            self.assertEqual(roi.status_code, 200, roi.data)
            self.assertEqual((roi.data['data']['operating']['net_consumption_fen'],
                              roi.data['data']['operating']['provider_cost_fen']), (8000, 11900), roi.data)

    def test_finance_partially_then_fully_funds_specified_double_order(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                self.prepare(team_size=2, selected=True, high_discount=True, host='xa.esports.club')
                self.assertEqual((self.receipt.deal_units, self.receipt.paid_units), (15000, 8000))
                from django.utils import timezone
                from console.test_operating_workflow import seed_payroll_fixture
                seed_payroll_fixture()
                analyst_user = make_console_user(['roi:view'])
                analyst = get_or_create_account_for_legacy_user(analyst_user)
                analyst_user.admin_membership.account = analyst
                analyst_user.admin_membership.save(update_fields=['account'])
                roi_client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(analyst)[0])
                day = timezone.localdate().isoformat()
                params = {'start_date': day, 'end_date': day}
                roi = roi_client.get('/api/admin/operating-roi/', params)
                self.assertEqual(roi.status_code, 200, roi.data)
                expected_income = sum(OrderSegmentFinancialSnapshot.objects.filter(
                    segment__attribution__order=self.order).values_list('income_at_entry', flat=True))
                self.assertGreater(expected_income, self.order.provider_income)
                self.assertEqual((roi.data['data']['operating']['net_consumption_fen'],
                                  roi.data['data']['operating']['provider_cost_fen']), (8000, expected_income), roi.data)
                for player in self.clients:
                    result = player.post(f'/api/orders/orders/{self.order.pk}/start/',
                                         {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                for player in self.clients:
                    result = player.post(f'/api/orders/orders/{self.order.pk}/complete/',
                                         {'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                staff = make_console_user(['report:view', 'report:audit'])
                reviewer = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = reviewer
                staff.admin_membership.save(update_fields=['account'])
                review = APIClient(HTTP_HOST='admin.esports.club',
                                   HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer)[0])
                reports = []
                for player, account in zip(self.clients, self.accounts):
                    report = ProviderReport.objects.get(order=self.order, provider_account=account)
                    result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                         {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                    result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    result = review.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    report.refresh_from_db()
                    reports.append(report)
                ready_at = max(row.submitted_at for row in reports) + timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=ready_at):
                    self.assertFalse(process_order(self.order.pk))
                self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=self.order).exists())
                order_id = self.order.pk

            self.client = self.platform_client
            registered = PlatformPassRepaymentTests.post(self, 'xinan', {
                **self.data, 'request_id': str(uuid4()), 'external_reference': 'SPECIFIED-DOUBLE', 'amount': 4000})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            verified = PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(verified.status_code, 200, verified.data)
            url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
            for index in range(2):
                payload = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 2000}
                result = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual(self.client.post(url, payload, format='json',
                    HTTP_AUTHORIZATION='Bearer ' + self.token).data, result.data)
                with store_context(self.second), patch('django.utils.timezone.now', return_value=ready_at):
                    if index == 0:
                        self.assertFalse(process_order(order_id))
                        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
                        self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order_id).exists())
                    else:
                        self.assertTrue(process_order(order_id))
                        self.assertFalse(process_order(order_id))
            with store_context(self.second):
                self.assertEqual(sum(OrderPointsFundingAllocation.objects.filter(
                    redemption__order_id=order_id).values_list('amount', flat=True)), 4000)
                snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=order_id)
                incomes = dict(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').values_list(
                    'wallet__account_id', 'amount'))
                self.assertEqual(incomes, {row.segment.provider_account_id: row.income_at_entry for row in snapshots})
                self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order_id).amount, 8000)
                roi = roi_client.get('/api/admin/operating-roi/', params)
                self.assertEqual(roi.status_code, 200, roi.data)
                self.assertEqual(roi.data['data']['operating']['provider_cost_fen'], expected_income, roi.data)
            with store_context(self.first):
                self.assertFalse(OrderPointsFundingAllocation.objects.exists())

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True)
    def test_two_seat_reassignment_releases_once_for_whole_order(self):
        from orders.models import OrderAddonFundingReleaseRun, OrderPrestartHandoff
        from orders.points_funding_evidence import verify_order_funding
        from orders.tests.factories import make_provider
        from stores.db import transaction

        with store_context(self.second):
            self.prepare(team_size=2, selected=True, high_discount=True, host='xa.esports.club')
            replacements = []
            for _ in range(2):
                user = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(user)
                user.escort_profile.account = account
                user.escort_profile.save(update_fields=['account'])
                user.escort_profile.service_items.add(self.service)
                replacements.append(account)
            staff = make_console_user(['order:view', 'order:dispatch'])
            actor = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = actor
            staff.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
            order_id = self.order.pk
        self.client = self.platform_client
        registered = PlatformPassRepaymentTests.post(self, 'xinan', {
            **self.data, 'request_id': str(uuid4()), 'external_reference': 'BOTH-PRESTART', 'amount': 4000})
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        verified = PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        response = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 4000},
                                    format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.data)
        with store_context(self.second):
            self.service.commission_override = True
            self.service.commission_rate = 30
            self.service.save(update_fields=['commission_override', 'commission_rate'])
            response = admin.post(f'/api/admin/orders/{order_id}/transfer/', {'reason': '双席同时改派',
                'transfers': [{'old_provider_id': old.pk, 'new_provider_id': new.pk}
                              for old, new in zip(self.accounts, replacements)]}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(OrderPrestartHandoff.objects.filter(order_id=order_id).count(), 2)
            release = OrderAddonFundingReleaseRun.objects.get(prestart_handoff__order_id=order_id)
            self.assertEqual((release.required_amount, release.allocated_before, release.released_amount),
                             (2500, 4000, 1500))
            self.assertEqual(len(release.prestart_snapshot['events']), 2)
            with transaction.atomic():
                self.assertEqual((verify_order_funding(order_id).allocated, verify_order_funding(order_id).required),
                                 (2500, 2500))

    def test_prestart_change_preserves_allocated_funds_and_finance_completes_shortfall(self):
        self._prestart_addon_journey()

    def test_prestart_return_then_addon_refund_uses_current_main_share(self):
        self._prestart_addon_journey(refund_addon=True)

    def test_prestart_return_then_main_negotiation_settles_both_seats(self):
        self._prestart_addon_journey(refund_main=True)

    def test_followup_with_addon_previews_whole_order_without_executing(self):
        self._prestart_addon_journey(refund_main=True, preview_addon_followup=True)

    def test_followup_with_addon_preserves_first_players_paid_income(self):
        self._prestart_addon_journey(refund_main=True, preview_addon_followup=True, paid_first=True)

    def test_full_main_refund_preserves_addons_and_recovers_deposit(self):
        self._prestart_addon_journey(refund_main=True, preview_addon_followup=True,
                                     paid_first=True, full_main_refund=True)

    def test_followup_after_addon_refund_reduces_unpaid_main_income(self):
        self._prestart_addon_journey(refund_main=True, preview_addon_followup=True, paid_first=True,
                                     addon_deduction_after_first=True)

    def test_followup_after_addon_refund_when_neither_player_is_paid(self):
        self._prestart_addon_journey(refund_main=True, preview_addon_followup=True,
                                     addon_deduction_after_first=True)

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True,
        NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
        SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
        SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING=True, SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True, SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True, SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True,
        SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING=True, SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING=True,
        SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_prestart_reassignment_then_two_separate_negotiated_refunds(self):
        from orders.models import OrderAddonFundingReleaseRun, OrderPointsRefund, OrderPointsRefundSettlement
        from orders.tests.factories import make_provider
        from orders.points_followup_funding import verify_followup_funding
        from stores.db import transaction

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                self.prepare(team_size=2, selected=True, high_discount=True, host='xa.esports.club')
                boss, order_id = self.client, self.order.pk
                replacement_user = make_provider(deposit_mode='EARNINGS')
                replacement = get_or_create_account_for_legacy_user(replacement_user)
                replacement_user.escort_profile.account = replacement
                replacement_user.escort_profile.save(update_fields=['account'])
                replacement_user.escort_profile.service_items.add(self.service)
                player_a = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(replacement)[0])
                player_b = self.clients[1]
                staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle',
                                           'support:edit', 'report:view', 'report:audit', 'escort:view', 'escort:deposit_topup'])
                actor = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = actor
                staff.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
            self.client = self.platform_client
            registered = PlatformPassRepaymentTests.post(self, 'xinan', {
                **self.data, 'request_id': str(uuid4()), 'external_reference': 'TWO-REFUNDS-PRESTART', 'amount': 4000})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            self.assertEqual(PlatformPassRepaymentTests.action(self, claim, 'verify',
                {'request_id': str(uuid4())}).status_code, 200)
            url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
            allocated = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 4000},
                                         format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(allocated.status_code, 200, allocated.data)
            with store_context(self.second):
                self.service.commission_override, self.service.commission_rate = True, 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                changed = admin.post(f'/api/admin/orders/{order_id}/transfer/', {'reason': '改派', 'transfers': [
                    {'old_provider_id': self.accounts[0].pk, 'new_provider_id': replacement.pk}]}, format='json')
                self.assertEqual(changed.data['code'], 0, changed.data)
                self.assertEqual(OrderAddonFundingReleaseRun.objects.get(prestart_handoff__order_id=order_id).released_amount, 750)
                for player in (player_a, player_b):
                    response = player.post(f'/api/orders/orders/{order_id}/start/',
                                           {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
                    'reason': '首位陪玩终止', 'affected_provider_account_ids': [replacement.pk],
                    'client_request_id': 'first-prestart-negotiation'}, format='json')
                self.assertEqual(opened.status_code, 200, opened.data)
                case_id = opened.data['data']['id']
                first = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'first-prestart-plan', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': replacement.pk, 'retained_units': 4250}],
                    'explanation': '首位陪玩协商终止'}, format='json')
                self.assertEqual(first.status_code, 200, first.data)
                for client in (boss, player_a):
                    response = client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                        {'proposal_id': first.data['data']['id'], 'accepted': True}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                response = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                    {'proposal_id': first.data['data']['id'], 'client_request_id': 'first-prestart-refund'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                completed = player_b.post(f'/api/orders/orders/{order_id}/complete/',
                                          {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(completed.data['code'], 0, completed.data)
                rejected = boss.post(f'/api/orders/orders/{order_id}/aftersale/', {'reason': '已终止范围',
                    'affected_provider_account_ids': [replacement.pk], 'client_request_id': 'closed-prestart-scope'}, format='json')
                self.assertNotEqual(rejected.data.get('code', 400), 0, rejected.data)
                reports = []
                for player, account in ((player_a, replacement), (player_b, self.accounts[1])):
                    report = ProviderReport.objects.get(order_id=order_id, provider_account=account)
                    for kind in (('COMPLETION', 'RESULT') if account.pk == replacement.pk else ('RESULT',)):
                        response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                               {'kind': kind, 'image': screenshot()}, format='multipart')
                        self.assertEqual(response.status_code, 200, response.data)
                    self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                    self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
                    report.refresh_from_db()
                    reports.append(report)
                opened = boss.post(f'/api/orders/orders/{order_id}/aftersale/', {'reason': '第二位服务售后',
                    'affected_provider_account_ids': [self.accounts[1].pk], 'client_request_id': 'second-prestart-case'}, format='json')
                self.assertEqual(opened.status_code, 200, opened.data)
                follow_case = opened.data['data']['id']
                second = admin.post(f'/api/admin/aftersales/{follow_case}/negotiation-proposal/', {
                    'request_id': 'second-prestart-plan', 'expected_version': 1, 'refund_units': 1000,
                    'provider_terms': [{'account_id': self.accounts[1].pk, 'retained_units': 5000}],
                    'explanation': '第二位服务部分退款'}, format='json')
                self.assertEqual(second.status_code, 200, second.data)
                for client in (boss, player_b):
                    response = client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                        {'proposal_id': second.data['data']['id'], 'accepted': True}, format='json')
                    self.assertEqual(response.status_code, 200, response.data)
                response = admin.post(f'/api/admin/aftersales/{follow_case}/negotiation-execution/',
                    {'proposal_id': second.data['data']['id'], 'client_request_id': 'second-prestart-refund'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                repeated = admin.post(f'/api/admin/aftersales/{follow_case}/negotiation-execution/',
                    {'proposal_id': second.data['data']['id'], 'client_request_id': 'second-prestart-refund'}, format='json')
                self.assertEqual(repeated.status_code, 200, repeated.data)
                self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(), 2)
                with transaction.atomic():
                    funded = verify_followup_funding(order_id)
                self.assertEqual((funded.required, funded.allocated, funded.remaining), (2310, 2310, 0))
                with patch('django.utils.timezone.now', return_value=max(r.submitted_at for r in reports) + timedelta(hours=73)):
                    self.assertTrue(process_order(order_id))
                    self.assertFalse(process_order(order_id))
                settlement = OrderPointsRefundSettlement.objects.get(posting__proposal_id=second.data['data']['id'])
                self.assertEqual(set(settlement.persons.values_list('segment__provider_account_id', 'amount')),
                                 {(replacement.pk, 4250), (self.accounts[1].pk, 5000)})
                confirmation = ConsumptionConfirmation.objects.get(order_id=order_id)
                self.assertEqual((confirmation.amount, confirmation.refunded_amount), (8000, 1060))

    @override_settings(PRESTART_HANDOFF_ENABLED=True, SERVICE_POINTS_PRESTART_TESTING=True,
                       ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
                       ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_RELEASE_TESTING=True, SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True,
                       SERVICE_POINTS_REFUND_ADDON_REVISION_TESTING=True,
                       SERVICE_POINTS_FOLLOWUP_PREVIEW_TESTING=True, SERVICE_POINTS_FOLLOWUP_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_UNPAID_FOLLOWUP_AGREEMENT_TESTING=True)
    def _prestart_addon_journey(self, *, refund_addon=False, refund_main=False, preview_addon_followup=False,
                                paid_first=False, addon_deduction_after_first=False, full_main_refund=False):
        from orders.models import (GameCategory, OrderAddonFundingReleaseRun, OrderAddonPayment, OrderAddonSettlement,
                                   OrderPrestartHandoff, OrderSegmentFinancialCoverage,
                                   ServiceAddonConfiguration, ServiceAddonPriceOption)
        from orders.points_funding_evidence import verify_order_funding
        from orders.points_funding_plan import PointsFundingPlanError
        from orders.points_funding_plan import plan_verified_order
        from orders.tests.factories import make_provider, make_service
        from stores.db import transaction

        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                game = GameCategory.objects.create(name='积分改派加购')
                self.service.game_category = game
                self.service.save(update_fields=['game_category'])
                addon = make_service(name='积分改派加购项目', game_category=game, is_addon=True)
                config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon,
                    enabled=True, conditions='', condition_mode='NONE', version=1)
                ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=500)
                self.prepare(team_size=2, selected=True, high_discount=True, host='xa.esports.club')
                boss = self.client
                replacement_user = make_provider(deposit_mode='EARNINGS')
                replacement = get_or_create_account_for_legacy_user(replacement_user)
                replacement_user.escort_profile.account = replacement
                replacement_user.escort_profile.save(update_fields=['account'])
                replacement_user.escort_profile.service_items.add(self.service)
                staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'order:settle', 'support:edit', 'report:view', 'report:audit', 'escort:view', 'escort:deposit_topup'])
                actor = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = actor
                staff.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(actor)[0])
                order_id = self.order.pk
            self.client = self.platform_client
            registered = PlatformPassRepaymentTests.post(self, 'xinan', {
                **self.data, 'request_id': str(uuid4()), 'external_reference': 'SPECIFIED-PRESTART', 'amount': 4750})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            verified = PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(verified.status_code, 200, verified.data)
            url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
            initial = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 4000},
                                       format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(initial.status_code, 200, initial.data)
            with store_context(self.second):
                self.service.commission_override = True
                self.service.commission_rate = 30
                self.service.save(update_fields=['commission_override', 'commission_rate'])
                body = {'reason': '客服协调换人', 'transfers': [{'old_provider_id': self.accounts[0].pk,
                                                              'new_provider_id': replacement.pk}]}
                from rest_framework.exceptions import ValidationError
                with patch('orders.points_prestart_release.digest', side_effect=ValidationError('测试释放失败')):
                    failed = admin.post(f'/api/admin/orders/{order_id}/transfer/', body, format='json')
                self.assertNotEqual(failed.data['code'], 0, failed.data)
                self.order.refresh_from_db()
                self.assertEqual(self.order.provider_account_id, self.accounts[0].pk)
                self.assertFalse(OrderPrestartHandoff.objects.filter(order_id=order_id).exists())
                self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(prestart_handoff__order_id=order_id).exists())
                transferred = admin.post(f'/api/admin/orders/{order_id}/transfer/', body, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                repeated = admin.post(f'/api/admin/orders/{order_id}/transfer/', body, format='json')
                self.assertNotEqual(repeated.data['code'], 0, repeated.data)
                self.assertEqual(OrderAddonFundingReleaseRun.objects.filter(prestart_handoff__order_id=order_id).count(), 1)
                self.order.refresh_from_db()
                self.assertEqual(self.order.provider_account_id, replacement.pk)
                self.assertEqual(OrderPrestartHandoff.objects.filter(order_id=order_id).count(), 1)
                self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order_id=order_id).status, 'ACTIVE')
                self.assertEqual(sum(OrderPointsFundingAllocation.objects.filter(
                    redemption__order_id=order_id).values_list('amount', flat=True)), 4000)
                release = OrderAddonFundingReleaseRun.objects.get(prestart_handoff__order_id=order_id)
                self.assertEqual((release.required_amount, release.allocated_before, release.released_amount),
                                 (3250, 4000, 750))
                self.assertEqual(sum(release.releases.values_list('amount', flat=True)), 750)
                with transaction.atomic():
                    self.assertEqual((verify_order_funding(order_id).allocated, verify_order_funding(order_id).required),
                                     (3250, 3250))
                original_hash = release.prestart_hash
                OrderAddonFundingReleaseRun.objects.filter(pk=release.pk).update(prestart_hash='0' * 64)
                with transaction.atomic(), self.assertRaises(PointsFundingPlanError):
                    verify_order_funding(order_id)
                OrderAddonFundingReleaseRun.objects.filter(pk=release.pk).update(prestart_hash=original_hash)
                self.service.commission_rate = 10
                self.service.save(update_fields=['commission_rate'])
                body['transfers'] = [{'old_provider_id': replacement.pk, 'new_provider_id': self.accounts[0].pk}]
                transferred = admin.post(f'/api/admin/orders/{order_id}/transfer/', body, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                with transaction.atomic():
                    self.assertEqual(plan_verified_order(order_id).additional_store_funding_units, 4750)
            extra = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 1500},
                                     format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(extra.status_code, 200, extra.data)
            with store_context(self.second):
                players = [self.clients[0], self.clients[1]]
                participants = [self.accounts[0], self.accounts[1]]
                for player in players:
                    response = player.post(f'/api/orders/orders/{order_id}/start/',
                                           {'entry_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                addon_url = f'/api/orders/orders/{order_id}/addons/intents/'
                intent = boss.post(addon_url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': self.accounts[0].pk,
                    'request_id': 'prestart-addon', 'expected_amount': 500,
                    'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(intent.status_code, 200, intent.data)
                pay_url = addon_url + str(intent.data['data']['id']) + '/pay/'
                self.wallet.refresh_from_db()
                balance_before = self.wallet.balance
                with patch('orders.addon_funding_release._record_funding_release',
                           side_effect=ValidationError('测试资金版本写入失败')):
                    failed_payment = boss.post(pay_url, {}, format='json')
                self.assertNotEqual(failed_payment.status_code, 200, failed_payment.data)
                self.wallet.refresh_from_db()
                self.assertEqual(self.wallet.balance, balance_before)
                self.assertFalse(OrderAddonPayment.objects.filter(intent_id=intent.data['data']['id']).exists())
                paid = boss.post(pay_url, {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
                payment_id = paid.data['data']['payment_id']
                self.assertEqual(boss.post(pay_url, {}, format='json').data['data']['payment_id'], payment_id)
                addon_release = OrderAddonFundingReleaseRun.objects.get(payment_id=payment_id)
                self.assertEqual((addon_release.allocated_before, addon_release.required_amount,
                                  addon_release.released_amount), (4750, 4700, 50))
                if paid_first or addon_deduction_after_first:
                    second_intent = boss.post(addon_url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                        'configuration_version': 1, 'provider_account_id': self.accounts[1].pk,
                        'request_id': 'prestart-second-addon', 'expected_amount': 500,
                        'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
                    self.assertEqual(second_intent.status_code, 200, second_intent.data)
                    second_paid = boss.post(addon_url + str(second_intent.data['data']['id']) + '/pay/', {}, format='json')
                    self.assertEqual(second_paid.status_code, 200, second_paid.data)
                    second_payment_id = second_paid.data['data']['payment_id']
                    release_second = OrderAddonFundingReleaseRun.objects.get(payment_id=second_payment_id)
                    self.assertEqual((release_second.allocated_before, release_second.required_amount,
                                      release_second.released_amount), (4700, 4600, 100))
                if refund_main:
                    opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
                        'reason': '单席协商终止', 'affected_provider_account_ids': [self.accounts[0].pk],
                        'client_request_id': 'prestart-main-case'}, format='json')
                    self.assertEqual(opened.status_code, 200, opened.data)
                    case_id = opened.data['data']['id']
                    proposal = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                        'request_id': 'prestart-main-proposal', 'expected_version': 0, 'refund_units': 1000,
                        'provider_terms': [{'account_id': self.accounts[0].pk, 'retained_units': 5750}],
                        'explanation': '双方确认单席终止'}, format='json')
                    self.assertEqual(proposal.status_code, 200, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    for client in (boss, players[0]):
                        result = client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                            {'proposal_id': proposal_id, 'accepted': True}, format='json')
                        self.assertEqual(result.status_code, 200, result.data)
                    result = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                        {'proposal_id': proposal_id, 'client_request_id': 'prestart-main-refund'}, format='json')
                    self.assertEqual(result.status_code, 200, result.data)
                    repeated = admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/',
                        {'proposal_id': proposal_id, 'client_request_id': 'prestart-main-refund'}, format='json')
                    self.assertEqual(repeated.status_code, 200, repeated.data)
                    from orders.models import OrderPointsRefund
                    self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(), 1)
                    if not paid_first and not addon_deduction_after_first:
                        from console.roi_period import period_revenue
                        from django.utils import timezone
                        now = timezone.now()
                        revenue = period_revenue(now - timedelta(days=1), now + timedelta(days=1))
                        self.assertEqual((revenue['paid_units'], revenue['refunded_units'], revenue['net_units']),
                                         (8500, 530, 7970), revenue)
                        self.assertEqual(revenue['refunded_points_value_units'], 470, revenue)
                        self.assertGreater(revenue['refunded_points_hundredths'], 0)
                        from engagement.models import PointLedger
                        points_refund = OrderPointsRefund.objects.get(redemption__order_id=order_id)
                        original_points = points_refund.points_ledger.amount
                        PointLedger.objects.filter(pk=points_refund.points_ledger_id).update(amount=original_points - 1)
                        damaged = period_revenue(now - timedelta(days=1), now + timedelta(days=1))
                        self.assertEqual(damaged['net_units'], 7970)
                        self.assertIsNone(damaged['refunded_points_hundredths'])
                        self.assertEqual(damaged['invalid_points_order_ids'], [order_id])
                        PointLedger.objects.filter(pk=points_refund.points_ledger_id).update(amount=original_points)
                    if paid_first:
                        from django.utils import timezone
                        first_time = timezone.now()
                for player in (players[1:] if refund_main else players):
                    from contextlib import nullcontext
                    clock = patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=48)) if paid_first else nullcontext()
                    with clock:
                        response = player.post(f'/api/orders/orders/{order_id}/complete/',
                                               {'completion_image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                if refund_addon:
                    case = boss.post(f'/api/orders/orders/{order_id}/aftersale/', {
                        'reason': '加购未履行', 'affected_provider_account_ids': [self.accounts[0].pk],
                        'client_request_id': 'prestart-addon-case'}, format='json')
                    self.assertEqual(case.status_code, 200, case.data)
                    case_id = case.data['data']['id']
                    proposal_url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
                    proposal = admin.post(proposal_url, {'request_id': 'prestart-addon-refund', 'amount_raw': 500,
                        'case_id': case_id, 'explanation': '加购未履行全额退款'}, format='json')
                    self.assertEqual(proposal.status_code, 200, proposal.data)
                    proposal_id = proposal.data['data']['id']
                    decision_url = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
                    for client in (boss, players[0]):
                        decision = client.post(decision_url, {'accepted': True}, format='json')
                        self.assertEqual(decision.status_code, 200, decision.data)
                    result = admin.post(proposal_url + f'{proposal_id}/execute/', {}, format='json')
                    self.assertEqual(result.status_code, 200, result.data)
                    participant = result.data['data']['participants'][0]
                    self.assertEqual((participant['addon_pending_deduction_raw'],
                                      participant['main_pending_deduction_raw']), (450, 50))
                    from console.roi_period import period_revenue
                    from django.utils import timezone
                    now = timezone.now()
                    revenue = period_revenue(now - timedelta(days=1), now + timedelta(days=1))
                    self.assertEqual((revenue['paid_units'], revenue['refunded_units'], revenue['net_units']),
                                     (8500, 500, 8000), revenue)
                    result = admin.post(f'/api/admin/aftersales/{case_id}/resolve/',
                                        {'resolution': '加购退款完成'}, format='json')
                    self.assertEqual(result.status_code, 200, result.data)
                reports = []
                for player, account in zip(players, participants):
                    report = ProviderReport.objects.get(order_id=order_id, provider_account=account)
                    if refund_main and account.pk == self.accounts[0].pk:
                        response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                               {'kind': 'COMPLETION', 'image': screenshot()}, format='multipart')
                        self.assertEqual(response.data['code'], 0, response.data)
                    clock = patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=48)) if paid_first and account.pk == self.accounts[1].pk else nullcontext()
                    with clock:
                        response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                               {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                        self.assertEqual(response.data['code'], 0, response.data)
                        response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                        self.assertEqual(response.data['code'], 0, response.data)
                        response = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                        self.assertEqual(response.data['code'], 0, response.data)
                    report.refresh_from_db()
                    reports.append(report)
                if refund_main:
                    from stores.db import transaction
                    from orders.points_funding_evidence import verify_order_funding
                    with transaction.atomic():
                        funding = verify_order_funding(order_id)
                    expected_funding = 4130 if paid_first or addon_deduction_after_first else 4230
                    self.assertEqual((funding.required, funding.allocated, funding.remaining),
                                     (expected_funding, expected_funding, 0))
                if preview_addon_followup:
                    clock = patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=48, minutes=1)) if paid_first else nullcontext()
                    with clock:
                        opened = boss.post(f'/api/orders/orders/{order_id}/aftersale/', {'reason': '第二位陪玩售后',
                            'affected_provider_account_ids': [self.accounts[1].pk],
                            'client_request_id': 'addon-followup-preview'}, format='json')
                    self.assertEqual(opened.status_code, 200, opened.data)
                    next_case = opened.data['data']['id']
                    if paid_first:
                        now = first_time + timedelta(hours=73)
                        ticks = count()
                        self.enterContext(patch('django.utils.timezone.now',
                            side_effect=lambda: now + timedelta(microseconds=next(ticks))))
                        self.assertFalse(process_order(order_id))
                        from orders.models import OrderPointsRefundSettlement
                        prior_settlement = OrderPointsRefundSettlement.objects.get(posting__proposal_id=proposal_id)
                        self.assertEqual(prior_settlement.persons.get().amount, 5750)
                        self.assertEqual(prior_settlement.addon_settlements.get(payment_id=payment_id).amount, 450)
                    if full_main_refund:
                        ranking = boss.get('/api/orders/rankings/?period=month')
                        self.assertEqual(ranking.status_code, 200, ranking.data)
                        self.order.refresh_from_db()
                        row = next(item for item in ranking.data['data']['consume_rank']
                                   if item['customer_account_id'] == self.account.pk)
                        # Refund 1,000 equivalent (not 530 coins); add-ons total 1,000.
                        self.assertEqual(row['total_amount'], self.order.original_amount)
                        from django.utils import timezone
                        from orders.grab_original_facts import original_amount_facts
                        from orders.models import OrderParticipationSegment
                        facts = original_amount_facts([account.pk for account in self.accounts], as_of=timezone.now())
                        segments = {row.provider_account_id: row for row in OrderParticipationSegment.objects.filter(
                            attribution__order_id=order_id, started_at__isnull=False)}
                        self.assertEqual(facts[self.accounts[0].pk].amount,
                                         segments[self.accounts[0].pk].original_share - 1000 + 500)
                        self.assertEqual(facts[self.accounts[1].pk].amount,
                                         segments[self.accounts[1].pk].original_share + 500)
                        from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide
                        candidates = [SubmittedCandidate(1, (SubmittedMember(self.accounts[0].pk, 'GOLD'),), timezone.now()),
                                      SubmittedCandidate(2, (SubmittedMember(self.accounts[1].pk, 'GOLD'),), timezone.now())]
                        self.assertEqual(decide(build_rankable_candidates(candidates, as_of=timezone.now())).winner_id, 2)
                        from users.models import EscortProfile
                        from wallet.models import SystemConfig
                        profile = EscortProfile.objects.get(account=self.accounts[1])
                        profile.deposit_mode = 'CASH'
                        profile.deposit_paid = 0
                        profile.deposit_required = 8000
                        profile.save(update_fields=['deposit_mode', 'deposit_paid', 'deposit_required'])
                        SystemConfig.objects.update_or_create(key='provider_deposit_targets', defaults={
                            'value': '{"cash_target":8000,"earnings_target":16000,"version":1}'})
                        topup = admin.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                            'amount': 8000, 'reason': '全额退款押金测试', 'reference': 'FULL-MAIN-ADDON',
                            'client_request_id': 'full-main-addon-deposit'}, format='multipart')
                        self.assertEqual(topup.data['code'], 0, topup.data)
                    if addon_deduction_after_first:
                        addon_refund_url = f'/api/admin/orders/{order_id}/addons/payments/{second_payment_id}/refund-proposals/'
                        addon_proposal = admin.post(addon_refund_url, {'request_id': 'later-addon-refund',
                            'amount_raw': 500, 'case_id': next_case, 'explanation': '加购未履行退款'}, format='json')
                        self.assertEqual(addon_proposal.status_code, 200, addon_proposal.data)
                        addon_proposal_id = addon_proposal.data['data']['id']
                        decision_url = f'/api/orders/orders/{order_id}/addons/payments/{second_payment_id}/refund-proposals/{addon_proposal_id}/'
                        for client in (boss, players[1]):
                            decision = client.post(decision_url, {'accepted': True}, format='json')
                            self.assertEqual(decision.status_code, 200, decision.data)
                        result = admin.post(addon_refund_url + f'{addon_proposal_id}/execute/', {}, format='json')
                        self.assertEqual(result.status_code, 200, result.data)
                        person = result.data['data']['participants'][0]
                        self.assertEqual((person['addon_pending_deduction_raw'], person['main_pending_deduction_raw']), (400, 100))
                        self.assertFalse(process_order(order_id))
                        if paid_first:
                            ranking = boss.get('/api/orders/rankings/?period=month')
                            self.assertEqual(ranking.status_code, 200, ranking.data)
                            self.order.refresh_from_db()
                            row = next(item for item in ranking.data['data']['consume_rank']
                                       if item['customer_account_id'] == self.account.pk)
                            # Main refund 1,000 equivalent; only A's 500 add-on remains.
                            self.assertEqual(row['total_amount'], self.order.original_amount - 500)
                    refund_units = 14000 if full_main_refund else 1000
                    retained_units = 0 if full_main_refund else 4900 if addon_deduction_after_first else 5000
                    preview = admin.get(f'/api/admin/aftersales/{next_case}/negotiation-preview/', {'refund_units': refund_units})
                    self.assertEqual(preview.status_code, 200, preview.data)
                    details = preview.data['data']
                    self.assertEqual((details['has_addons'], details['read_only'], details['agreement_available'],
                                      details['execution_available']), (True, True, False, False))
                    estimate = details['conditional_funding']
                    self.assertEqual((estimate['net_coin_units'], estimate['provider_income_units'],
                                      estimate['required_units'], estimate['allocated_units'], estimate['release_units']),
                                     (1000, 6600, 0, 4130, 4130) if full_main_refund else
                                     (7440, 11100, 3660, 4130, 470) if addon_deduction_after_first else
                                     (7940, 11600, 3660, 4130, 470) if paid_first else
                                     (7440, 11200, 3760, 4230, 470))
                    refused = admin.post(f'/api/admin/aftersales/{next_case}/negotiation-proposal/', {
                        'request_id': 'addon-followup-not-open', 'expected_version': 1, 'refund_units': refund_units,
                        'provider_terms': [{'account_id': self.accounts[1].pk,
                                            'retained_units': retained_units}],
                        'explanation': '第二次售后核对'}, format='json')
                    self.assertNotEqual(refused.data.get('code', 400), 0, refused.data)
                    self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(), 1)
                    # Exercise the real posting and versioning in a rolled-back
                    # isolated transaction; the public execution remains closed.
                    with override_settings(SERVICE_POINTS_FOLLOWUP_ADDON_REVISION_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_EXECUTION_TESTING=True, SERVICE_POINTS_FOLLOWUP_POSTING_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_REVISION_TESTING=True, SERVICE_POINTS_FOLLOWUP_RELEASE_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_TERMINATION_TESTING=True, SERVICE_POINTS_FOLLOWUP_FUNDING_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_SETTLEMENT_TESTING=True, SERVICE_POINTS_FOLLOWUP_ENDPOINT_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_FUNDING_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_POSTING_TESTING=True, SERVICE_POINTS_UNPAID_FOLLOWUP_TERMINATION_TESTING=True,
                        SERVICE_POINTS_UNPAID_FOLLOWUP_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_ADDON_UNPAID_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_ADDON_PAID_SETTLEMENT_TESTING=True,
                        SERVICE_POINTS_FOLLOWUP_ADDON_CHANGED_DEDUCTION_TESTING=True,
                        SERVICE_POINTS_REFUND_DEPOSIT_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                        REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True), transaction.atomic():
                        ready = admin.get(f'/api/admin/aftersales/{next_case}/negotiation-preview/', {'refund_units': refund_units})
                        self.assertEqual(ready.status_code, 200, ready.data)
                        self.assertTrue(ready.data['data']['execution_available'])
                        internal = admin.post(f'/api/admin/aftersales/{next_case}/negotiation-proposal/', {
                            'request_id': 'addon-followup-internal', 'expected_version': 1, 'refund_units': refund_units,
                            'provider_terms': [{'account_id': self.accounts[1].pk,
                                                'retained_units': retained_units}],
                            'explanation': '隔离核对后续退款'}, format='json')
                        self.assertEqual(internal.status_code, 200, internal.data)
                        for client in (boss, players[1]):
                            decision = client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                                {'proposal_id': internal.data['data']['id'], 'accepted': True}, format='json')
                            self.assertEqual(decision.status_code, 200, decision.data)
                        from orders.points_followup_revision import verify_followup_revision_locked
                        execution_url = f'/api/admin/aftersales/{next_case}/negotiation-execution/'
                        body = {'proposal_id': internal.data['data']['id'],
                                'client_request_id': 'addon-followup-internal-refund'}
                        executed = admin.post(execution_url, body, format='json')
                        self.assertEqual(executed.status_code, 200, executed.data)
                        repeated = admin.post(execution_url, body, format='json')
                        self.assertEqual(repeated.data['data']['execution_id'], executed.data['data']['execution_id'])
                        from orders.models import PointsNegotiationPosting
                        posting = PointsNegotiationPosting.objects.get(proposal_id=internal.data['data']['id'])
                        revision = posting.followup_funding_revision
                        self.assertEqual((revision.net_paid_units, revision.provider_units, revision.required_units,
                                          revision.allocated_units, revision.release_units),
                                         (1000, 6600, 0, 4130, 4130) if full_main_refund else
                                         (7440, 11100, 3660, 4130, 470) if addon_deduction_after_first else
                                         (7940, 11600, 3660, 4130, 470) if paid_first else
                                         (7440, 11200, 3760, 4230, 470))
                        self.assertEqual(revision.addon_snapshot['addon_history'][0]['payment_id'], payment_id)
                        self.assertEqual(verify_followup_revision_locked(revision).release_units, 4130 if full_main_refund else 470)
                        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(), 2)
                        with patch('django.utils.timezone.now', return_value=max(r.submitted_at for r in reports) + timedelta(hours=73)):
                            failure_target = ('orders.services._credit_wallet' if addon_deduction_after_first
                                              else 'orders.addon_settlement.OrderAddonSettlement.objects.create')
                            with patch(failure_target, side_effect=RuntimeError('测试加购收益入账失败')):
                                with self.assertRaisesMessage(RuntimeError, '测试加购收益入账失败'):
                                    process_order(order_id)
                            from orders.models import OrderPointsRefundSettlement
                            self.assertFalse(OrderPointsRefundSettlement.objects.filter(posting=posting).exists())
                            if not paid_first:
                                self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
                            self.assertTrue(process_order(order_id))
                            self.assertFalse(process_order(order_id))
                        settlement = OrderPointsRefundSettlement.objects.get(posting=posting)
                        self.assertEqual(set(settlement.persons.values_list('segment__provider_account_id', 'amount')),
                                         {(self.accounts[1].pk, retained_units)} if paid_first else
                                         {(self.accounts[0].pk, 5750), (self.accounts[1].pk, 4900 if addon_deduction_after_first else 5000)})
                        if paid_first:
                            second_addon = settlement.addon_settlements.get(payment_id=second_payment_id)
                            if addon_deduction_after_first:
                                self.assertEqual(second_addon.amount, 0)
                                self.assertIsNone(second_addon.income_transaction_id)
                            else:
                                self.assertEqual(second_addon.amount, 400)
                            self.assertEqual(prior_settlement.addon_settlements.get(payment_id=payment_id).amount, 450)
                        else:
                            self.assertEqual(settlement.addon_settlements.get(payment_id=payment_id).amount, 450)
                        self.assertEqual((settlement.paid_units, settlement.addon_paid_units, settlement.funding_units),
                                         (0, 1000, 0) if full_main_refund else
                                         (6940, 500, 3660) if addon_deduction_after_first else
                                         (6940, 1000, 3660) if paid_first else (6940, 500, 3760))
                        if full_main_refund:
                            from wallet.models import RefundDepositDeficit
                            deficit = RefundDepositDeficit.objects.get(refund=posting.points_refund.refund, profile=profile)
                            self.assertEqual((deficit.amount, deficit.remaining), (8000, 7600))
                            profile.refresh_from_db()
                            self.assertEqual(profile.deposit_paid, 400)
                            self.assertTrue(posting.points_refund.is_final)
                            self.assertEqual((posting.points_refund.coin_units, posting.points_refund.points_hundredths),
                                             (7470, 9334))
                            ranking = boss.get('/api/orders/rankings/?period=month')
                            self.assertEqual(ranking.status_code, 200, ranking.data)
                            row = next(item for item in ranking.data['data']['consume_rank']
                                       if item['customer_account_id'] == self.account.pk)
                            self.assertEqual((row['total_amount'], row['amount_complete']), (1000, True))
                            final_facts = original_amount_facts([account.pk for account in self.accounts],
                                                               as_of=timezone.now())
                            self.assertEqual([final_facts[account.pk].amount for account in self.accounts], [500, 500])
                        else:
                            self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order_id).refunded_amount, 1060)
                        transaction.set_rollback(True)
                    self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(), 1)
                    resolved = admin.post(f'/api/admin/aftersales/{next_case}/resolve/',
                                          {'resolution': '暂不退款'}, format='json')
                    self.assertEqual(resolved.status_code, 200, resolved.data)
                with patch('django.utils.timezone.now', return_value=max(row.submitted_at for row in reports) + timedelta(hours=73)):
                    self.assertTrue(process_order(order_id))
                    self.assertFalse(process_order(order_id))
                incomes = dict(Transaction.objects.filter(order_id=order_id, tx_type='INCOME',
                    remark=f'订单收入：{self.order.order_no}').values_list('wallet__account_id', 'amount'))
                self.assertEqual(incomes, {self.accounts[0].pk: 5750 if refund_main else 6700 if refund_addon else 6750,
                                           self.accounts[1].pk: 5900 if addon_deduction_after_first else 6000})
                if refund_addon:
                    self.assertFalse(OrderAddonSettlement.objects.filter(payment_id=payment_id).exists())
                else:
                    self.assertEqual(OrderAddonSettlement.objects.get(payment_id=payment_id).amount, 450)
                confirmation = ConsumptionConfirmation.objects.get(order_id=order_id)
                self.assertEqual((confirmation.amount, confirmation.refunded_amount), (8000, 530 if refund_main else 0))
            with store_context(self.first):
                self.assertFalse(OrderAddonFundingReleaseRun.objects.exists())
