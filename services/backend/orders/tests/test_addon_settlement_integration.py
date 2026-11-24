from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
import os
from unittest import skipUnless
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, PointFundingLot, PointLedger
from engagement.services import completed_consumption, config
from orders.models import (GameCategory, Order, OrderAddonConsumption, OrderAddonSettlement,
                           OrderPointsSettlement, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_service_funding_api as funding_fixture
from orders.tests.factories import make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from stores import test_platform_pass_repayment as platform_fixture
from stores.context import store_context
from wallet.models import ProviderReport, Transaction
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True, ADDON_HANDOVER_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
    SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CELERY_TASK_ALWAYS_EAGER=False)
class AddonSettlementIntegrationTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = platform_fixture.PlatformPassRepaymentTests.setUp
    post_receipt = platform_fixture.PlatformPassRepaymentTests.post
    action = platform_fixture.PlatformPassRepaymentTests.action

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_addon_full_refund_uses_other_addon_earnings_then_settles(self):
        self._addon_refund_settlement(use_other_addon=True)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_addon_full_refund_deducts_unsettled_main_and_preserves_referral(self):
        self._addon_refund_settlement(use_other_addon=False)

    def _addon_refund_settlement(self, *, use_other_addon):
        from orders.models import OrderAddonRefund
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        with store_context(self.second):
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            inviter = None
            if not use_other_addon:
                from orders.tests.factories import make_user
                inviter = get_or_create_account_for_legacy_user(make_user())
                fixture.account.inviter, fixture.account.inviter_commission_rate = inviter, 10
                fixture.account.save(update_fields=['inviter', 'inviter_commission_rate'])
            game = GameCategory.objects.create(name='跨加购退款结算')
            fixture.service.game_category = game
            fixture.service.save(update_fields=['game_category'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='加购服务', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon,
                enabled=True, conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 1000
            lot.save(update_fields=['remaining'])
            gift_lot = PointFundingLot.objects.get(account=fixture.account, kind='GIFT')
            gift_lot.remaining += 400
            gift_lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 1400
            fixture.wallet.save(update_fields=['balance'])
            created = fixture.boss.post('/api/orders/create/', fixture.payload(gift_balance_amount=400), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(fixture.provider)[0])
            self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': screenshot()}, format='multipart').data['code'], 0)
            url = f'/api/orders/orders/{order.pk}/addons/intents/'
            payments = []
            for index in ((1, 2) if use_other_addon else (1,)):
                intent = fixture.boss.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': fixture.provider.pk,
                    'request_id': f'cross-addon-{index}', 'expected_amount': 500,
                    'gift_balance_amount': 200, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(intent.status_code, 200, intent.data)
                paid = fixture.boss.post(url + f"{intent.data['data']['id']}/pay/", {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
                payments.append(paid.data['data']['payment_id'])
            with store_context(self.first):
                registered = self.post_receipt('xinan')
                self.assertEqual(registered.status_code, 200, registered.data)
                claim = registered.data['data']['claim_id']
                self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}).status_code, 200)
                allocation = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                    {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 400}, format='json',
                    HTTP_AUTHORIZATION='Bearer ' + self.token)
                self.assertEqual(allocation.status_code, 200, allocation.data)
            if not use_other_addon:
                # Project a main-service refund while the actual paid add-on
                # remains in force. Its income must stay funded before any
                # allocation is returned to the store's receipt balance.
                from orders.addon_funding import include_paid_addons
                from orders.addon_funding_history import combined_at
                from orders.points_negotiation_preview import funding_after_refund
                from orders.refunded_addon_funding import combine_refunded_funding, combine_refunded_history
                from django.utils import timezone
                from stores.db import transaction
                main = funding_after_refund(principal_units=600, gift_units=400, compensation_units=0,
                    returned_prepaid_units=200, returned_gift_units=0, returned_compensation_units=0,
                    provider_income_units=800, pending_deduction_units=200, provider_external_units=0,
                    inviter_rate=10, allocated_units=400)
                with transaction.atomic():
                    combined = combine_refunded_funding(main, include_paid_addons(order, main))
                    cutoff = timezone.now()
                    historical = combine_refunded_history(main, combined_at(order, main, cutoff))
                    self.assertEqual(historical, combined)
                self.assertEqual((main.required_units, main.release_units), (280, 120))
                self.assertEqual((combined.net_coin_units, combined.net_principal_units, combined.net_gift_units,
                                  combined.provider_income_units, combined.inviter_income_units),
                                 (1300, 700, 600, 1000, 80))
                self.assertEqual((combined.required_units, combined.release_units, combined.remaining_units),
                                 (380, 20, 0))
            self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/complete/',
                {'completion_image': screenshot()}, format='multipart').data['code'], 0)
            case = fixture.boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason': '第一项加购未履行',
                'affected_provider_account_ids': [fixture.provider.pk], 'client_request_id': 'cross-addon-case'}, format='json')
            self.assertEqual(case.status_code, 200, case.data)
            case_id = case.data['data']['id']
            staff_user = make_console_user(['order:view', 'order:refund', 'order:settle', 'report:view', 'report:audit', 'roi:view'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payments[0]}/refund-proposals/'
            proposal = admin.post(refund_url, {'request_id': 'cross-addon-refund', 'amount_raw': 500,
                'case_id': case_id, 'explanation': '第一项全部退款'}, format='json')
            self.assertEqual(proposal.status_code, 200, proposal.data)
            self.assertEqual({key: proposal.data['data']['participants'][0][key] for key in
                ('addon_pending_deduction_raw', 'main_pending_deduction_raw', 'wallet_deduction_raw',
                 'deposit_deduction_raw')},
                dict.fromkeys(('addon_pending_deduction_raw', 'main_pending_deduction_raw', 'wallet_deduction_raw',
                               'deposit_deduction_raw')))
            self.assertIsNone(proposal.data['data']['participants'][0]['addon_deductions'])
            proposal_id = proposal.data['data']['id']
            decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payments[0]}/refund-proposals/{proposal_id}/'
            for client in (fixture.boss, player):
                self.assertEqual(client.post(decision_url, {'accepted': True}, format='json').status_code, 200)
            refund = admin.post(refund_url + f'{proposal_id}/execute/', {}, format='json')
            self.assertEqual(refund.status_code, 200, refund.data)
            participant = refund.data['data']['participants'][0]
            self.assertEqual((participant['addon_pending_deduction_raw'], participant['main_pending_deduction_raw'],
                              participant['wallet_deduction_raw'], participant['deposit_deduction_raw']),
                             (500, 0, 0, 0) if use_other_addon else (400, 100, 0, 0))
            self.assertEqual({entry['payment_id']: entry['amount_raw'] for entry in participant['addon_deductions']},
                             {payments[0]: 400, payments[1]: 100} if use_other_addon else {payments[0]: 400})
            self.assertTrue(all(entry['addon_name'] == '加购服务' for entry in participant['addon_deductions']))
            shown = player.get(decision_url)
            self.assertEqual(shown.status_code, 200, shown.data)
            self.assertEqual(shown.data['data']['participants'][0]['main_pending_deduction_raw'],
                             0 if use_other_addon else 100)
            receipt = OrderAddonRefund.objects.get(proposal_id=proposal_id)
            from console.roi_period import service_payment_cost
            from django.utils import timezone
            payment_time = order.transactions.get(tx_type='PAY', amount__lt=0).created_at
            prior_cost = service_payment_cost(order, payment_time, receipt.created_at)
            refund_cost = service_payment_cost(order, receipt.created_at, timezone.now() + timedelta(seconds=1))
            self.assertIsNotNone(prior_cost)
            self.assertIsNotNone(refund_cost)
            self.assertEqual(prior_cost, {'provider': 1600 if use_other_addon else 1200,
                                          'referral': 0 if use_other_addon else 100,
                                          'gift_compensation': 800 if use_other_addon else 600})
            self.assertEqual(refund_cost, {'provider': -500, 'referral': 0, 'gift_compensation': -200})
            from console.test_operating_workflow import seed_payroll_fixture
            seed_payroll_fixture()
            day = timezone.localdate().isoformat()
            roi = admin.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day})
            self.assertEqual(roi.status_code, 200, roi.data)
            operating = roi.data['data']['operating']
            self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                              operating['referral_cost_fen'], operating['gift_compensation_cost_fen']),
                             (1500, 1100, 0, 600) if use_other_addon else (1000, 700, 100, 400), roi.data)
            if not use_other_addon:
                # Later add-on refunds must not rewrite the earlier funding
                # basis used by a main-order refund at this cutoff.
                with transaction.atomic():
                    self.assertEqual(combine_refunded_history(main, combined_at(order, main, cutoff)), combined)
            expected_deductions = ({payments[0]: 400, payments[1]: 100} if use_other_addon else {payments[0]: 400})
            self.assertEqual(dict(receipt.earning_deductions.values_list('payment_id', 'amount')), expected_deductions)
            self.assertEqual(receipt.main_pending_deduction, 0 if use_other_addon else 100)
            from orders.models import OrderAddonFundingReleaseRun
            release = OrderAddonFundingReleaseRun.objects.get(refund=receipt)
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount),
                             (400, 200, 200))
            resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '加购退款完成'}, format='json')
            self.assertEqual(resolved.status_code, 200, resolved.data)
            report = ProviderReport.objects.get(order=order)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind': 'RESULT', 'image': screenshot()}, format='multipart').data['code'], 0)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
            self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
            report.refresh_from_db()
            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(order.pk))
                self.assertFalse(process_order(order.pk))
            order.refresh_from_db()
            self.assertEqual(service_payment_cost(order, payment_time, receipt.created_at), prior_cost)
            self.assertEqual(service_payment_cost(order, receipt.created_at, timezone.now() + timedelta(seconds=1)), refund_cost)
            if not use_other_addon:
                from django.db import transaction
                from orders.addon_refund_execution import _main_pending_income
                # The settlement receipt is authoritative even when the original
                # provider assignment does not carry its own settlement time.
                order.refresh_from_db()
                self.assertIsNone(order.providers.get(provider_account=fixture.provider).settled_at)
                with transaction.atomic():
                    self.assertEqual(_main_pending_income(order, fixture.provider.pk), 0)
            self.assertFalse(OrderAddonSettlement.objects.filter(payment_id=payments[0]).exists())
            if use_other_addon:
                self.assertEqual(OrderAddonSettlement.objects.get(payment_id=payments[1]).amount, 300)
            self.assertEqual(get_wallet(account=fixture.provider).balance, 1100 if use_other_addon else 700)
            self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payments[0]).refunded_units, 500)
            if use_other_addon:
                self.assertEqual(OrderAddonConsumption.objects.get(payment_id=payments[1]).paid_units, 500)
            else:
                self.assertEqual(get_wallet(account=inviter).balance, 100)
                settlement = OrderPointsSettlement.objects.get(order=order)
                self.assertEqual((settlement.provider_units, settlement.inviter_units, settlement.funding_units),
                                 (700, 100, 200))
                displayed = player.get('/api/wallet/reports/').data['data'][0]
                self.assertEqual(displayed['settlement_status'], 'SETTLED')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_handover_waits_for_whole_order_funding_and_pays_each_player_once(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        with store_context(self.second):
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            game = GameCategory.objects.create(name='加购换人整单结算')
            fixture.service.game_category = game
            fixture.service.billing_provider_count = 2
            fixture.service.fulfillment_provider_count = 2
            fixture.service.save(update_fields=['game_category', 'billing_provider_count', 'fulfillment_provider_count'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            other_user = make_provider(deposit_mode='CASH', deposit_paid=40000)
            other = get_or_create_account_for_legacy_user(other_user)
            other_user.escort_profile.account = other
            other_user.escort_profile.save(update_fields=['account'])
            other_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='换人加购', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon, enabled=True,
                conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 3500
            lot.save(update_fields=['remaining'])
            gift_lot = PointFundingLot.objects.get(account=fixture.account, kind='GIFT')
            gift_lot.remaining += 800
            gift_lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 4300
            fixture.wallet.save(update_fields=['balance'])
            payload = fixture.payload()
            payload.pop('provider_id')
            payload.update(provider_ids=[fixture.provider.pk, other.pk], gift_balance_amount=400)
            created = fixture.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            players = [APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                       for account in (fixture.provider, other)]
            for player in players:
                result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            intent_url = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = fixture.boss.post(intent_url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': fixture.provider.pk, 'request_id': 'handover-settlement',
                'expected_amount': 500, 'gift_balance_amount': 400, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = fixture.boss.post(intent_url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']
            # Exercise different already-snapshotted rates: switching the add-on
            # from A (20%) to B (30%) reduces its funding requirement.
            from orders.models import OrderProvider, OrderSegmentFinancialSnapshot
            assignment_b = OrderProvider.objects.get(order=order, provider_account=other)
            old_income = assignment_b.provider_income
            assignment_b.commission_rate = 30
            assignment_b.provider_income = assignment_b.settlement_base * 70 // 100
            assignment_b.save(update_fields=['commission_rate', 'provider_income'])
            order.provider_income -= old_income - assignment_b.provider_income
            order.shop_income += old_income - assignment_b.provider_income
            order.save(update_fields=['provider_income', 'shop_income'])
            OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order,
                segment__provider_account=other).update(commission_rate=30, income_at_entry=assignment_b.provider_income)
            with store_context(self.first):
                recorded = self.post_receipt('xinan', {**self.data, 'amount': 200})
                self.assertEqual(recorded.status_code, 200, recorded.data)
                claim = recorded.data['data']['claim_id']
                self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}).status_code, 200)
                allocation_url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
                allocation = self.client.post(allocation_url,
                    {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 200},
                    format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
                self.assertEqual(allocation.status_code, 200, allocation.data)
            staff_user = make_console_user(['report:view', 'report:audit', 'order:view', 'order:dispatch', 'order:refund', 'order:settle', 'transaction:view'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/handovers/'
            handover = admin.post(staff_url, {'request_id': 'split-addon', 'target_provider_account_id': other.pk,
                'retained_basis_raw': 200, 'completed_work': '甲完成两份', 'remaining_work': '乙继续三份'}, format='json')
            self.assertEqual(handover.status_code, 200, handover.data)
            from orders.addon_funding import include_paid_addons
            from orders.points_funding_plan import PointsFundingPlanError
            from orders.service_funding_plan import plan_verified_ordinary_order
            from stores.db import transaction
            with transaction.atomic():
                with self.assertRaises(PointsFundingPlanError):
                    include_paid_addons(order, plan_verified_ordinary_order(order.pk))
            refund_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            blocked = admin.post(refund_url, {'request_id': 'while-handover', 'amount_raw': 100,
                                              'explanation': '换人待确认'}, format='json')
            self.assertNotEqual(blocked.status_code, 200, blocked.data)
            decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/handovers/{handover.data["data"]["id"]}/decision/'
            for client in (fixture.boss, players[0]):
                decision = client.post(decision_url, {'accepted': True}, format='json')
                self.assertEqual(decision.status_code, 200, decision.data)
            with patch('orders.addon_funding_release.record_handover_funding_release', side_effect=RuntimeError('release failed')):
                with self.assertRaisesMessage(RuntimeError, 'release failed'):
                    players[1].post(decision_url, {'accepted': True}, format='json')
            from orders.models import OrderAddonHandover
            proposal = OrderAddonHandover.objects.get(pk=handover.data['data']['id'])
            self.assertEqual(proposal.status, 'PENDING')
            self.assertIsNone(proposal.decisions.get(account=other).accepted)
            decision = players[1].post(decision_url, {'accepted': True}, format='json')
            self.assertEqual(decision.status_code, 200, decision.data)
            self.assertEqual(decision.data['data']['status'], 'ACCEPTED')
            from orders.models import OrderAddonFundingReleaseRun
            release = OrderAddonFundingReleaseRun.objects.get(handover_id=handover.data['data']['id'])
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (200, 170, 30))
            second_handover = admin.post(staff_url, {'request_id': 'split-again',
                'target_provider_account_id': fixture.provider.pk, 'retained_basis_raw': 100,
                'completed_work': '乙完成一份', 'remaining_work': '甲接替两份'}, format='json')
            self.assertEqual(second_handover.status_code, 200, second_handover.data)
            second_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/handovers/{second_handover.data["data"]["id"]}/decision/'
            for client in (fixture.boss, *players):
                result = client.post(second_url, {'accepted': True}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
            release = OrderAddonFundingReleaseRun.objects.get(handover_id=second_handover.data['data']['id'])
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (170, 190, 0))
            from orders.points_funding_evidence import verify_order_funding
            evidence = verify_order_funding(order.pk)
            self.assertEqual((evidence.allocated, evidence.remaining, evidence.fully_funded), (170, 20, False))
            from orders.models import AfterSale
            refund_case = AfterSale.objects.create(order=order, account=fixture.account, reason='双人加购协商退款')
            refund_data = {'request_id': 'multi-refund-after-handover', 'amount_raw': 10, 'explanation': '共同退款',
                           'affected_provider_account_ids': [fixture.provider.pk, other.pk]}
            refund_proposal = admin.post(refund_url, refund_data, format='json')
            self.assertEqual(refund_proposal.status_code, 200, refund_proposal.data)
            refund_id = refund_proposal.data['data']['id']
            refund_decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{refund_id}/'
            for client in (fixture.boss, *players):
                result = client.post(refund_decision_url, {'accepted': True}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
            posted = admin.post(refund_url + f'{refund_id}/execute/', {}, format='json')
            self.assertEqual(posted.status_code, 200, posted.data)
            from orders.models import OrderAddonRefund
            refund = OrderAddonRefund.objects.get(proposal_id=refund_id)
            self.assertEqual((refund.amount, refund.addon_pending_deduction, refund.rounding_surplus), (10, 20, 10))
            evidence = verify_order_funding(order.pk)
            self.assertEqual((evidence.allocated, evidence.remaining, evidence.fully_funded), (170, 10, False))
            resolved = admin.post(f'/api/admin/aftersales/{refund_case.pk}/resolve/', {'resolution': '加购退款完成'}, format='json')
            self.assertEqual(resolved.status_code, 200, resolved.data)
            with store_context(self.first):
                allocation = self.client.post(allocation_url,
                    {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 10},
                    format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
                self.assertEqual(allocation.status_code, 200, allocation.data)
            blocked = admin.post(refund_url, {'request_id': 'after-handover', 'amount_raw': 100,
                                              'explanation': '多人责任退款'}, format='json')
            self.assertNotEqual(blocked.status_code, 200, blocked.data)
            for player in players:
                result = player.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            reports = []
            for player, account in zip(players, (fixture.provider, other)):
                report = ProviderReport.objects.get(order=order, provider_account=account)
                result = player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
                self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                self.assertEqual(admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
                report.refresh_from_db()
                reports.append(report)
            case = fixture.boss.post(f'/api/orders/orders/{order.pk}/aftersale/',
                {'reason': '乙的服务待处理', 'affected_provider_account_ids': [other.pk], 'client_request_id': 'handover-case'}, format='json')
            self.assertEqual(case.status_code, 200, case.data)
            case_id = case.data['data']['id']
            matured = max(report.submitted_at for report in reports) + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=matured):
                self.assertFalse(process_order(order.pk))
            self.assertEqual(OrderAddonSettlement.objects.filter(payment_id=payment_id).count(), 1)

        with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
            self.assertFalse(process_order(order.pk))
            first = OrderAddonSettlement.objects.get(payment_id=payment_id)
            self.assertEqual((first.segment.provider_account_id, first.amount), (fixture.provider.pk, 310))
            first_balance = get_wallet(account=fixture.provider).balance
            self.assertEqual(get_wallet(account=other).balance, 0)
            self.assertFalse(process_order(order.pk))
            self.assertEqual(get_wallet(account=fixture.provider).balance, first_balance)
            resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '已完成'}, format='json')
            self.assertEqual(resolved.status_code, 200, resolved.data)
            self.assertTrue(process_order(order.pk))
            rows = list(OrderAddonSettlement.objects.filter(payment_id=payment_id).order_by('segment_id'))
            self.assertEqual([(row.segment.provider_account_id, row.amount) for row in rows],
                             [(fixture.provider.pk, 310), (other.pk, 60)])
            self.assertEqual(get_wallet(account=fixture.provider).balance, first_balance)
            self.assertEqual(OrderAddonConsumption.objects.filter(payment_id=payment_id).count(), 1)
            for player, row in zip(players, rows):
                history = player.get('/api/wallet/transactions/')
                self.assertEqual(history.status_code, 200, history.data)
                entry = next(item for item in history.data['data']['transactions'] if item['id'] == row.income_transaction_id)
                self.assertEqual((entry['order_id'], entry['addon']['payment_id']), (order.pk, payment_id))
            listing = admin.get('/api/admin/transactions/', {'order_no_exact': order.order_no})
            self.assertEqual(listing.status_code, 200, listing.data)
            self.assertTrue({row.income_transaction_id for row in rows} <=
                            {item['id'] for item in listing.data['data']['list']})

    def test_whole_order_funding_before_wallet_income_and_vip_points(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        with store_context(self.second):
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            game = GameCategory.objects.create(name='加购结算测试')
            fixture.service.game_category = game
            fixture.service.save(update_fields=['game_category'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='加购项目', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon, enabled=True,
                conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 500
            lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 500
            fixture.wallet.save(update_fields=['balance'])
            created = fixture.boss.post('/api/orders/create/', fixture.payload(), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(fixture.provider)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            url = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = fixture.boss.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': fixture.provider.pk, 'request_id': 'settlement-addon',
                'expected_amount': 500, 'gift_balance_amount': 400, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = fixture.boss.post(url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']
            completed = player.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(completed.data['code'], 0, completed.data)
            report = ProviderReport.objects.get(order=order)
            result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
            admin = make_console_user(['report:view', 'report:audit'])
            reviewer_account = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = reviewer_account
            admin.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(reviewer_account)[0])
            approved = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(approved.data['code'], 0, approved.data)
            report.refresh_from_db()
            matured = report.submitted_at + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=matured):
                self.assertFalse(process_order(order.pk))
            self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

        recorded = self.post_receipt('xinan')
        self.assertEqual(recorded.status_code, 200, recorded.data)
        claim = recorded.data['data']['claim_id']
        verified = self.action(claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        allocation = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 100},
                                      format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(allocation.status_code, 200, allocation.data)
        with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
            with patch('orders.addon_settlement.OrderAddonSettlement.objects.create', side_effect=RuntimeError('写入失败')):
                with self.assertRaisesMessage(RuntimeError, '写入失败'):
                    process_order(order.pk)
            self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertFalse(OrderAddonConsumption.objects.filter(payment_id=payment_id).exists())
            self.assertTrue(process_order(order.pk))
            settlement = OrderPointsSettlement.objects.get(order=order)
            self.assertEqual((settlement.paid_units, settlement.principal_units, settlement.gift_units,
                              settlement.funding_units, settlement.provider_units, settlement.store_units),
                             (1500, 1100, 400, 100, 1200, 0))
            addon_settlement = OrderAddonSettlement.objects.get(payment_id=payment_id)
            self.assertEqual((addon_settlement.amount, addon_settlement.income_transaction.amount), (400, 400))
            self.assertEqual(get_wallet(account=fixture.provider).balance, 1200)
            self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 1000)
            addon_consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
            self.assertEqual((addon_consumption.paid_units, addon_consumption.principal_units,
                              addon_consumption.awarded_points), (500, 100, 150))
            self.assertEqual(completed_consumption(fixture.account), 1500)
            self.assertEqual(sum(PointLedger.objects.filter(account=fixture.account, kind='CONSUMPTION')
                                 .values_list('amount', flat=True)), 1650)
            self.assertFalse(process_order(order.pk))
            self.assertEqual(OrderAddonSettlement.objects.filter(payment_id=payment_id).count(), 1)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '加购退款结算 provider/gift_compensation/押金缺口分摊属正式资金口径深水区（18-盘点 加购退款结算待联调，口径②家族待用户裁定）；最终联调启用')
    def test_double_order_other_players_open_aftersale_does_not_retract_paid_addon(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        with store_context(self.second):
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            game = GameCategory.objects.create(name='双陪加购结算')
            fixture.service.game_category = game
            fixture.service.billing_provider_count = 2
            fixture.service.fulfillment_provider_count = 2
            fixture.service.save(update_fields=['game_category', 'billing_provider_count', 'fulfillment_provider_count'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            other_user = make_provider(deposit_mode='CASH', deposit_paid=40000)
            other = get_or_create_account_for_legacy_user(other_user)
            other_user.escort_profile.account = other
            other_user.escort_profile.save(update_fields=['account'])
            other_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='双陪加购', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon, enabled=True,
                conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 3500
            lot.save(update_fields=['remaining'])
            gift_lot = PointFundingLot.objects.get(account=fixture.account, kind='GIFT')
            gift_lot.remaining += 800
            gift_lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 4300
            fixture.wallet.save(update_fields=['balance'])
            payload = fixture.payload()
            payload.pop('provider_id')
            payload['provider_ids'] = [fixture.provider.pk, other.pk]
            payload['gift_balance_amount'] = 400
            created = fixture.boss.post('/api/orders/create/', payload, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            players = [APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                       for account in (fixture.provider, other)]
            for player in players:
                result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            addon_url = f'/api/orders/orders/{order.pk}/addons/intents/'
            payments = []
            for index, account in enumerate((fixture.provider, other)):
                intent = fixture.boss.post(addon_url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': account.pk, 'request_id': f'double-addon-{index}',
                    'expected_amount': 500, 'gift_balance_amount': 400, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(intent.status_code, 200, intent.data)
                paid = fixture.boss.post(addon_url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
                payments.append(paid.data['data']['payment_id'])
            for player in players:
                result = player.post(f'/api/orders/orders/{order.pk}/complete/',
                                     {'completion_image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
            staff_user = make_console_user(['report:view', 'report:audit', 'order:view', 'order:settle', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            reports = []
            for player, account in zip(players, (fixture.provider, other)):
                report = ProviderReport.objects.get(order=order, provider_account=account)
                result = player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(result.data['code'], 0, result.data)
                result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                result = admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                report.refresh_from_db()
                reports.append(report)
            case_result = fixture.boss.post(f'/api/orders/orders/{order.pk}/aftersale/',
                {'reason': '乙的加购问题', 'affected_provider_account_ids': [other.pk], 'client_request_id': 'case-addon-b'}, format='json')
            self.assertEqual(case_result.status_code, 200, case_result.data)
            case_id = case_result.data['data']['id']
            matured = max(report.submitted_at for report in reports) + timedelta(hours=73)
            with patch('django.utils.timezone.now', return_value=matured):
                self.assertFalse(process_order(order.pk))
            self.assertFalse(OrderAddonSettlement.objects.filter(payment_id__in=payments).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

        recorded = self.post_receipt('xinan', {**self.data, 'amount': 600})
        self.assertEqual(recorded.status_code, 200, recorded.data)
        claim = recorded.data['data']['claim_id']
        self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        allocation_url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        for index, amount in enumerate((300, 300)):
            allocation = self.client.post(allocation_url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': amount},
                                          format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(allocation.status_code, 200, allocation.data)
            if index == 0:
                with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                    from orders.points_funding_evidence import verify_order_funding
                    self.assertFalse(verify_order_funding(order.pk).fully_funded)
                    self.assertFalse(process_order(order.pk))
                    self.assertFalse(OrderAddonSettlement.objects.filter(payment_id__in=payments).exists())
                    self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        with store_context(self.second):
            self.enterContext(patch('django.utils.timezone.now', return_value=matured))
            self.assertFalse(process_order(order.pk))
            self.assertTrue(OrderAddonSettlement.objects.filter(payment_id=payments[0]).exists())
            self.assertFalse(OrderAddonSettlement.objects.filter(payment_id=payments[1]).exists())
            first_balance = get_wallet(account=fixture.provider).balance
            self.assertGreater(first_balance, 0)
            self.assertEqual(get_wallet(account=other).balance, 0)
            staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payments[1]}/refund-proposals/'
            user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payments[1]}/refund-proposals/'
            proposal = admin.post(staff_url, {'request_id': 'addon-b-refund', 'amount_raw': 150,
                'explanation': '乙的加购部分退款'}, format='json')
            self.assertEqual(proposal.status_code, 200, proposal.data)
            proposal_id = proposal.data['data']['id']
            self.assertEqual(fixture.boss.post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
            self.assertEqual(players[1].post(user_url + f'{proposal_id}/', {'accepted': True}, format='json').status_code, 200)
            refunded = admin.post(staff_url + f'{proposal_id}/execute/', {}, format='json')
            self.assertEqual(refunded.status_code, 200, refunded.data)
            self.assertEqual(get_wallet(account=fixture.provider).balance, first_balance)
            from orders.points_funding_evidence import verify_order_funding
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.required, proof.allocated, proof.remaining), (550, 550, 0))
            resolved = admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '乙的加购已退'}, format='json')
            self.assertEqual(resolved.status_code, 200, resolved.data)
            self.assertTrue(process_order(order.pk))
            self.assertEqual(get_wallet(account=fixture.provider).balance, first_balance)
            self.assertEqual(OrderAddonSettlement.objects.get(payment_id=payments[1]).amount, 250)
            self.assertEqual(OrderAddonSettlement.objects.get(payment_id=payments[0]).amount, 400)
            consumption_b = OrderAddonConsumption.objects.get(payment_id=payments[1])
            self.assertEqual((consumption_b.refunded_units, consumption_b.refunded_principal_units,
                              consumption_b.awarded_points), (150, 100, 0))
            self.assertEqual(completed_consumption(fixture.account), 2850)
            self.assertEqual(sum(PointLedger.objects.filter(account=fixture.account, kind='CONSUMPTION')
                                 .values_list('amount', flat=True)), 2550)

    def test_vip_stopped_before_confirmation_never_back_awards_addon_points(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        with store_context(self.second):
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            game = GameCategory.objects.create(name='加购会员停用')
            fixture.service.game_category = game
            fixture.service.save(update_fields=['game_category'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='会员停用加购', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon,
                enabled=True, conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 500
            lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 500
            fixture.wallet.save(update_fields=['balance'])
            created = fixture.boss.post('/api/orders/create/', fixture.payload(), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(fixture.provider)[0])
            result = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            url = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = fixture.boss.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': fixture.provider.pk, 'request_id': 'vip-stop-addon',
                'expected_amount': 500, 'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = fixture.boss.post(url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']
            result = player.post(f'/api/orders/orders/{order.pk}/complete/',
                                 {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            report = ProviderReport.objects.get(order=order)
            result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
            staff_user = make_console_user(['report:view', 'report:audit'])
            staff = get_or_create_account_for_legacy_user(staff_user)
            staff_user.admin_membership.account = staff
            staff_user.admin_membership.save(update_fields=['account'])
            reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            self.assertEqual(reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
            report.refresh_from_db()
            operation = config()
            operation.vip_enabled = False
            operation.save(update_fields=['vip_enabled'])
            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                order.refresh_from_db()
                self.assertIsNotNone(order.settled_at)
                consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
                self.assertFalse(consumption.vip_eligible)
                self.assertEqual(consumption.awarded_points, 0)
                operation.vip_enabled = True
                operation.save(update_fields=['vip_enabled'])
                self.assertFalse(process_order(order.pk))
                self.assertEqual(completed_consumption(fixture.account), 0)
                self.assertFalse(PointLedger.objects.filter(account=fixture.account, kind='CONSUMPTION').exists())
