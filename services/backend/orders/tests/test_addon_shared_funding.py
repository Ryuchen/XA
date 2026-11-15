from django.test import TestCase, override_settings
from django.conf import settings
from django.utils import timezone
from unittest import skipUnless
from uuid import uuid4
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from orders.models import AfterSale, GameCategory, Order, ServiceAddonConfiguration, ServiceAddonPriceOption
from orders.tests import test_service_funding_api as funding_fixture
from orders.tests.test_addon_configuration import staff_client
from orders.tests.factories import make_service
from stores import test_platform_pass_repayment as platform_fixture
from stores.context import current_store, resolve_store, store_context
from stores.db import transaction


@override_settings(ALLOWED_HOSTS=['*'], BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=False,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AddonSharedFundingTests(TestCase):
    payload = funding_fixture.ServiceFundingApiTest.payload

    def setUp(self):
        from tempfile import TemporaryDirectory
        funding_fixture.ServiceFundingApiTest.setUp(self)
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_addon_refund_then_main_negotiation_reports_actual_costs(self):
        self._addon_then_main_negotiation(addon_refund=100)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_addon_refund_using_main_income_reduces_later_negotiated_share(self):
        self._addon_then_main_negotiation(addon_refund=500)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_refunded_main_with_addon_identifies_store_funding_shortfall(self):
        self._addon_then_main_negotiation(addon_refund=100, gift=400)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True)
    def test_matured_refund_and_addon_income_is_posted_once(self):
        self._addon_then_main_negotiation(addon_refund=100, post=True)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True)
    def test_matured_main_income_reflects_prior_addon_burden(self):
        self._addon_then_main_negotiation(addon_refund=500, post=True)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True)
    def test_addon_income_failure_rolls_back_main_income_before_retry(self):
        self._addon_then_main_negotiation(addon_refund=100, post=True, fail_post=True)

    def _addon_then_main_negotiation(self, *, addon_refund, gift=0, before_refund=None, post=False, fail_post=False, finalize=False, fail_final=False, zero_recovery=False):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from console.models import AdminMembership
        from console.test_operating_workflow import seed_payroll_fixture
        from orders.tests.factories import make_user
        from orders.tests.test_release_flow import screenshot

        inviter = get_or_create_account_for_legacy_user(make_user())
        self.account.inviter, self.account.inviter_commission_rate = inviter, 10
        self.account.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = AdminMembership.objects.get(account=self.staff).roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:settle', 'roi:view', 'report:view', 'report:audit']
        role.save(update_fields=['permissions'])
        game = GameCategory.objects.create(name='主单和加购退款测试')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        self.provider_user.escort_profile.service_items.add(self.service)
        addon = make_service(name='独立加购', game_category=game, is_addon=True)
        relation = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
            conditions='', condition_mode='NONE', version=1)
        ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
        lot = PointFundingLot.objects.get(account=self.account, kind='PREPAID')
        lot.remaining += 500
        lot.save(update_fields=['remaining'])
        self.wallet.balance += 500
        self.wallet.save(update_fields=['balance'])
        created = self.boss.post('/api/orders/create/', self.payload(gift_balance_amount=gift), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        player = APIClient(HTTP_HOST=self.boss.defaults.get('HTTP_HOST', 'sl.esports.club'),
                           HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
        started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        intent_url = f'/api/orders/orders/{order.pk}/addons/intents/'
        intent = self.boss.post(intent_url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': self.provider.pk, 'request_id': 'mixed-refund-addon',
            'expected_amount': 500, 'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = self.boss.post(intent_url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        payment_id = paid.data['data']['payment_id']
        if before_refund:
            before_refund(order.pk)

        case = self.admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'affected_provider_account_ids': [self.provider.pk], 'reason': '先处理加购退款',
            'client_request_id': 'mixed-addon-case'}, format='json')
        self.assertEqual(case.data['code'], 0, case.data)
        case_id = case.data['data']['id']
        addon_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
        proposal = self.admin.post(addon_url, {'request_id': 'mixed-addon-refund', 'amount_raw': addon_refund,
            'case_id': case_id, 'explanation': '加购部分退款'}, format='json')
        self.assertEqual(proposal.status_code, 200, proposal.data)
        proposal_id = proposal.data['data']['id']
        decision_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
        for actor in (self.boss, player):
            response = actor.post(decision_url, {'accepted': True}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
        response = self.admin.post(addon_url + f'{proposal_id}/execute/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        closed = self.admin.post(f'/api/admin/aftersales/{case_id}/resolve/', {'resolution': '加购已退款'}, format='json')
        self.assertEqual(closed.data['code'], 0, closed.data)

        case = self.admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
            'affected_provider_account_ids': [self.provider.pk], 'reason': '主单协商终止',
            'client_request_id': 'mixed-main-case'}, format='json')
        self.assertEqual(case.data['code'], 0, case.data)
        case_id = case.data['data']['id']
        main_refund = (700 if addon_refund == 500 else 800) if zero_recovery else 200
        main_income = 800 - max(addon_refund - 400, 0) - main_refund
        total_income = main_income + max(400 - addon_refund, 0)
        referral = (1000 - main_refund) // 10
        net = 1500 - addon_refund - main_refund
        proposal = self.admin.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
            'request_id': 'mixed-main-proposal', 'expected_version': 0, 'refund_units': main_refund,
            'provider_terms': [{'account_id': self.provider.pk, 'retained_units': main_income}],
            'explanation': '主单部分退款并保留已完成服务收益'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for actor in (self.boss, player):
            response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                  {'proposal_id': proposal_id, 'accepted': True}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        response = self.admin.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
            'proposal_id': proposal_id, 'client_request_id': 'mixed-main-refund'}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)

        from datetime import timedelta
        from console.roi_period import ordinary_negotiation_cost
        from orders.models import OrderAddonRefund
        from wallet.models import Transaction
        payment_time = Transaction.objects.get(order=order, tx_type='PAY', amount__lt=0).created_at
        addon_time = OrderAddonRefund.objects.get(payment_id=payment_id).created_at
        main_time = order.refund_records.get().created_at
        self.assertEqual(ordinary_negotiation_cost(order, payment_time, addon_time),
                         {'provider': 1200, 'referral': 100, 'gift_compensation': gift})
        self.assertEqual(ordinary_negotiation_cost(order, addon_time, main_time),
                         {'provider': -addon_refund, 'referral': 0, 'gift_compensation': 0})
        self.assertEqual(ordinary_negotiation_cost(order, main_time, timezone.now() + timedelta(seconds=1)),
                         {'provider': -main_refund, 'referral': -(main_refund // 10), 'gift_compensation': 0})

        from orders.ordinary_refunded_addon_funding import plan_locked
        with store_context(current_store() or resolve_store()), transaction.atomic():
            funding = plan_locked(order.pk)
            from orders.points_funding_evidence import verify_order_funding
            verified = verify_order_funding(order.pk)
        required = 180 if gift else 0
        assigned = required if before_refund else 0
        self.assertEqual((funding.required, funding.allocated, funding.remaining, funding.releasable),
                         (required, assigned, required - assigned, 0))
        self.assertEqual((verified.required, verified.allocated, verified.remaining, verified.fully_funded),
                         (required, assigned, required - assigned, required == assigned))
        self.assertEqual((funding.combined.net_coin_units, funding.combined.net_principal_units,
                          funding.combined.provider_income_units, funding.combined.inviter_income_units,
                          funding.combined.store_income_units),
                         (1200, 800, 900, 80, 0) if gift else
                         (net, net, total_income, referral, net - total_income - referral))
        from orders.points_funding_plan import PointsFundingPlanError
        with store_context(current_store() or resolve_store()), transaction.atomic():
            original_payment = Transaction.objects.get(order=order, tx_type='PAY', amount__lt=0)
            original_payment.amount += 10
            original_payment.save(update_fields=['amount'])
            with self.assertRaises(PointsFundingPlanError):
                plan_locked(order.pk)
            transaction.set_rollback(True)

        seed_payroll_fixture()
        day = timezone.localdate().isoformat()
        roi = self.admin.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day})
        self.assertEqual(roi.status_code, 200, roi.data)
        operating = roi.data['data']['operating']
        self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                          operating['referral_cost_fen'], operating['gift_compensation_cost_fen']),
                         (1200, 900, 80, 400) if gift else
                         (net, total_income, referral, 0), roi.data)

        from unittest.mock import patch
        from engagement.confirmation import process_order
        from wallet.models import ProviderReport
        from wallet.services import get_wallet
        report = ProviderReport.objects.get(order=order, provider_account=self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                   {'kind': kind, 'image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
        response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        response = self.admin.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        detail = self.admin.get(f'/api/admin/reports/{report.pk}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['data']['platform_funding_insufficient'], required > assigned, detail.data)
        report.refresh_from_db()
        if zero_recovery:
            from types import SimpleNamespace
            from orders.tests.factories import make_completed_order
            from wallet.models import get_platform_wallet
            from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
            from wallet.models import RefundDepositRecoveryPermit
            profile = self.provider_user.escort_profile
            provider_wallet = get_wallet(account=self.provider, user=self.provider_user)
            platform = get_platform_wallet()
            platform.balance += 200
            platform.save(update_fields=['balance'])
            previous_order = make_completed_order(self.user, self.provider_user, amount=1000,
                provider_account=self.provider, payment_status='PAID')
            source = SimpleNamespace(profile=profile, wallet=provider_wallet, platform=platform,
                                     order=previous_order, assertEqual=self.assertEqual)
            deficit = RefundDepositDeficitTest.deficit(source, 200)
            permit = RefundDepositRecoveryPermit.objects.create(profile=self.provider_user.escort_profile,
                                                                  account=self.provider, order=order)
        # Combined ordinary-refund/add-on settlement is not yet connected.
        # A mature report must not post either party's money prematurely.
        with store_context(current_store() or resolve_store()), override_settings(
                ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=post), patch(
                'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            if fail_post:
                from orders.services import _credit_wallet
                calls = []
                def fail_addon(**kwargs):
                    calls.append(kwargs)
                    if len(calls) == 2:
                        raise RuntimeError('addon income write failed')
                    return _credit_wallet(**kwargs)
                with patch('orders.services._credit_wallet', side_effect=fail_addon):
                    with self.assertRaisesMessage(RuntimeError, 'addon income write failed'):
                        process_order(order.pk)
                self.assertEqual(get_wallet(account=self.provider).balance, 0)
                self.assertFalse(order.transactions.filter(tx_type='INCOME').exists())
            if fail_final:
                from orders.services import _credit_wallet
                def fail_store(**kwargs):
                    if kwargs['tx_type'] == Transaction.TxType.SHOP_INCOME:
                        raise RuntimeError('store income write failed')
                    return _credit_wallet(**kwargs)
                with patch('orders.services._credit_wallet', side_effect=fail_store):
                    with self.assertRaisesMessage(RuntimeError, 'store income write failed'):
                        process_order(order.pk)
                self.assertEqual(get_wallet(account=self.provider).balance, 0)
                self.assertEqual(get_wallet(account=inviter).balance, 0)
                self.assertFalse(order.transactions.filter(tx_type__in=['INCOME', 'SHOP_INCOME']).exists())
            self.assertEqual(process_order(order.pk), bool(finalize and post and required == assigned))
            self.assertFalse(process_order(order.pk))
            if post and required == assigned:
                rows = player.get('/api/wallet/reports/')
                self.assertEqual(rows.status_code, 200, rows.data)
                shown = next(row for row in rows.data['data'] if row['id'] == report.pk)
                self.assertEqual(shown['settlement_status'], 'SETTLED', shown)
                self.assertIsNotNone(shown['settled_at'])
                self.assertEqual(shown['expected_income'], main_income)
                detail = self.admin.get(f'/api/admin/reports/{report.pk}/')
                self.assertEqual(detail.data['data']['settlement_status'], 'SETTLED', detail.data)
                self.assertIsNotNone(detail.data['data']['settled_at'])
        self.assertEqual(get_wallet(account=self.provider).balance,
                         max(total_income - (200 if zero_recovery else 0), 0) if post and required == assigned else 0)
        self.assertEqual(get_wallet(account=inviter).balance, referral if finalize and post and required == assigned else 0)
        if zero_recovery:
            deficit.refresh_from_db()
            self.assertEqual(deficit.remaining, max(200 - total_income, 0))
            permit.refresh_from_db()
            if total_income:
                self.assertEqual(permit.close_reason, 'GAP_PAID')
                self.assertIsNotNone(permit.income_application_id)
                self.assertIsNone(permit.zero_ordinary_addon_person_id)
            else:
                self.assertEqual(permit.close_reason, 'ZERO_ADDON_SETTLED')
                self.assertIsNotNone(permit.zero_ordinary_addon_person_id)
                with store_context(current_store() or resolve_store()), transaction.atomic(), patch(
                        'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    permit.order = previous_order
                    permit.save(update_fields=['order'])
                    rows = player.get('/api/wallet/reports/')
                    shown = next(row for row in rows.data['data'] if row['id'] == report.pk)
                    self.assertEqual(shown['settlement_status'], 'REVIEW', shown)
                    transaction.set_rollback(True)
        if finalize and post and required == assigned:
            from engagement.models import ConsumptionConfirmation
            from orders.models import OrderAddonConsumption
            order.refresh_from_db()
            self.assertIsNotNone(order.settled_at)
            self.assertTrue(ConsumptionConfirmation.objects.filter(order=order).exists())
            self.assertTrue(OrderAddonConsumption.objects.filter(payment_id=payment_id).exists())
        if post and required == assigned:
            credits = list(order.transactions.filter(tx_type='INCOME', wallet__account=self.provider).order_by('pk'))
            self.assertEqual([row.amount for row in credits], ([main_income] if main_income else []) +
                             ([400 - addon_refund] if addon_refund < 400 else []))
            if addon_refund == 100:
                with store_context(current_store() or resolve_store()), transaction.atomic(), patch(
                        'django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    credits[-1].amount -= 1
                    credits[-1].save(update_fields=['amount'])
                    rows = player.get('/api/wallet/reports/')
                    shown = next(row for row in rows.data['data'] if row['id'] == report.pk)
                    self.assertEqual(shown['settlement_status'], 'REVIEW', shown)
                    transaction.set_rollback(True)
        from orders.models import OrderPointsSettlement
        reservation = OrderPointsSettlement.objects.filter(order=order).first()
        if required == assigned:
            self.assertIsNotNone(reservation)
            self.assertEqual((reservation.paid_units, reservation.principal_units, reservation.funding_units,
                              reservation.provider_units, reservation.inviter_units, reservation.store_units),
                             (1200, 800, 180, 900, 80, 0) if gift else
                             (net, net, 0, total_income, referral, net - total_income - referral))
            self.assertEqual(reservation.persons.count(), 1 if post else 0)
            evidence = reservation.ordinary_refund_addon_evidence
            self.assertEqual([(row['account_id'], row['main_income']) for row in evidence.facts['people']],
                             [(self.provider.pk, main_income)])
            self.assertEqual([row['income'] for row in evidence.facts['addons']], [] if addon_refund == 500 else [300])
            from orders.settlement import SettlementError
            with store_context(current_store() or resolve_store()), transaction.atomic():
                evidence.facts_hash = 'invalid'
                evidence.save(update_fields=['facts_hash'])
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    with self.assertRaises(SettlementError):
                        process_order(order.pk)
                transaction.set_rollback(True)
        else:
            self.assertIsNone(reservation)
        return order.pk

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True)
    def test_refunded_main_and_addon_finalize_referral_and_consumption(self):
        self._addon_then_main_negotiation(addon_refund=100, post=True, finalize=True, fail_final=True)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True,
                       SERVICE_ORDINARY_FUNDING_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True)
    def test_zero_income_releases_recovery_permit(self):
        self._addon_then_main_negotiation(addon_refund=500, post=True, finalize=True, zero_recovery=True)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True,
                       SERVICE_ORDINARY_FUNDING_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True)
    def test_zero_main_income_closes_recovery_with_actual_addon_income(self):
        self._addon_then_main_negotiation(addon_refund=100, post=True, finalize=True, zero_recovery=True)

    def test_real_main_and_addon_payment_share_one_funding_requirement(self):
        from django.core.exceptions import ValidationError
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.addon_funding import include_paid_addons
        from orders.points_funding_write import allocate
        from orders.service_funding_plan import plan_verified_ordinary_order
        from orders.tests.factories import make_user
        from users.pass_repayment import register_receipt, verify_receipt

        inviter = get_or_create_account_for_legacy_user(make_user())
        self.account.inviter, self.account.inviter_commission_rate = inviter, 10
        self.account.save(update_fields=['inviter', 'inviter_commission_rate'])
        game = GameCategory.objects.create(name='加购资金测试')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        self.provider_user.escort_profile.service_items.add(self.service)
        addon = make_service(name='加购测试', game_category=game, is_addon=True)
        relation = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
            conditions='', condition_mode='NONE', version=1)
        ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
        lot = PointFundingLot.objects.get(account=self.account, kind='PREPAID')
        lot.remaining += 500; lot.save(update_fields=['remaining'])
        self.wallet.balance += 500; self.wallet.save(update_fields=['balance'])
        created = self.boss.post('/api/orders/create/', self.payload(), format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        player = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.provider)[0])
        started = player.post(f'/api/orders/orders/{order.pk}/start/', {}, format='json')
        self.assertEqual(started.data['code'], 0, started.data)
        url = f'/api/orders/orders/{order.pk}/addons/intents/'
        selection = {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1, 'configuration_version': 1,
            'provider_account_id': self.provider.pk, 'request_id': 'addon-funding-1', 'expected_amount': 500,
            'gift_balance_amount': 400, 'compensation_balance_amount': 0}
        intent = self.boss.post(url, selection, format='json')
        self.assertEqual(intent.status_code, 200, intent.data)
        paid = self.boss.post(url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        from console.roi_period import service_payment_cost
        from datetime import timedelta
        now = timezone.now()
        costs = service_payment_cost(order, now - timedelta(days=1), now + timedelta(days=1))
        self.assertEqual(costs, {'provider': 1200, 'referral': 100, 'gift_compensation': 400})
        from console.models import AdminMembership
        from console.test_operating_workflow import seed_payroll_fixture
        seed_payroll_fixture()
        role = AdminMembership.objects.get(account=self.staff).roles.first()
        role.permissions = [*role.permissions, 'roi:view']
        role.save(update_fields=['permissions'])
        day = timezone.localdate().isoformat()
        response = self.admin.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day})
        self.assertEqual(response.status_code, 200, response.data)
        operating = response.data['data']['operating']
        self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'],
                          operating['referral_cost_fen'], operating['gift_compensation_cost_fen']),
                         (1500, 1200, 100, 400), response.data)
        gift_lot = PointFundingLot.objects.get(account=self.account, kind='GIFT')
        gift_lot.kind = 'PREPAID'
        gift_lot.save(update_fields=['kind'])
        review = self.admin.get('/api/admin/operating-roi/', {'start_date': day, 'end_date': day}).data['data']['operating']
        self.assertEqual(review['net_consumption_fen'], 1500)
        self.assertIsNone(review['provider_cost_fen'])
        self.assertIn(order.pk, review['unknown_cost_order_ids'])
        gift_lot.kind = 'GIFT'
        gift_lot.save(update_fields=['kind'])
        with store_context(resolve_store()), transaction.atomic():
            main = plan_verified_ordinary_order(order.pk)
            combined = include_paid_addons(order, main)
            self.assertEqual((main.inviter_income_units, main.store_income_units,
                              main.additional_store_funding_units), (100, 100, 0))
            self.assertEqual((combined.addon_principal_units, combined.addon_provider_income_units,
                              combined.addon_inviter_income_units, combined.store_income_units,
                              combined.additional_store_funding_units), (100, 400, 0, 0, 200))
            receipt = register_receipt(actor='finance-1', request_key='shared-receipt',
                external_reference='shared-bank-reference', amount=300, received_at=timezone.now(), evidence='线下到账')
            verify_receipt(actor='finance-2', receipt_id=receipt.pk)
            allocation = allocate(actor='finance-2', request_key='addon-order-funding', receipt_id=receipt.pk,
                                  order_id=order.pk, amount=200)
            self.assertEqual(allocation.service_order_id, order.pk)
            self.assertEqual(allocation.amount, 200)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-2', request_key='addon-order-too-much', receipt_id=receipt.pk,
                         order_id=order.pk, amount=1)
            # A lost response can be retried after the order has settled; it
            # must return the original allocation without allocating again.
            order.settled_at = timezone.now()
            order.save(update_fields=['settled_at'])
            retried = allocate(actor='finance-2', request_key='addon-order-funding', receipt_id=receipt.pk,
                               order_id=order.pk, amount=200)
            self.assertEqual(retried.pk, allocation.pk)
            self.assertEqual(receipt.points_funding_allocations.count(), 1)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-2', request_key='addon-order-funding', receipt_id=receipt.pk,
                         order_id=order.pk, amount=100)
            with self.assertRaises(ValidationError):
                allocate(actor='finance-3', request_key='addon-order-funding', receipt_id=receipt.pk,
                         order_id=order.pk, amount=200)
            receipt.verified_at = None
            receipt.verified_by = ''
            receipt.save(update_fields=['verified_at', 'verified_by'])
            with self.assertRaises(ValidationError):
                allocate(actor='finance-2', request_key='addon-order-funding', receipt_id=receipt.pk,
                         order_id=order.pk, amount=200)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
    ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=False,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AddonPlatformFundingTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = platform_fixture.PlatformPassRepaymentTests.setUp
    post_receipt = platform_fixture.PlatformPassRepaymentTests.post
    action = platform_fixture.PlatformPassRepaymentTests.action
    reconciliation = platform_fixture.PlatformPassRepaymentTests.reconciliation

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_funding_release_failure_rolls_back_main_refund(self):
        from unittest.mock import patch
        from wallet.services import get_wallet
        registered = self.post_receipt('xinan')
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        def allocate_before_refund(order_id):
            response = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 200},
                                        format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(response.status_code, 200, response.data)
        with store_context(self.second):
            fixture = AddonSharedFundingTests(methodName='test_refunded_main_with_addon_identifies_store_funding_shortfall')
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            fixture.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            with patch('orders.ordinary_refunded_addon_release._facts',
                       side_effect=RuntimeError('release write failed')):
                with self.assertRaisesMessage(RuntimeError, 'release write failed'):
                    fixture._addon_then_main_negotiation(addon_refund=100, gift=400,
                                                        before_refund=allocate_before_refund)
            order = Order.objects.get(customer_account=fixture.account)
            self.assertEqual(order.payment_status, 'PAID')
            self.assertFalse(order.refund_records.exists())
            self.assertEqual(get_wallet(account=fixture.account).balance, 800)
            from orders.models import OrderAddonFundingReleaseRun
            self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(negotiation__refund__order=order).exists())
            from orders.points_funding_release import receipt_points_used_locked
            from users.models import PassRefundRepaymentReceipt
            with transaction.atomic():
                receipt = PassRefundRepaymentReceipt.objects.select_for_update().get()
                self.assertEqual(receipt_points_used_locked(receipt), 200)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True)
    def test_ordinary_refund_releases_excess_funding_to_same_store_receipt(self):
        registered = self.post_receipt('xinan')
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        self.assertEqual(self.action(claim, 'verify', {'request_id': str(uuid4())}).status_code, 200)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        def allocate_before_refund(order_id):
            response = self.client.post(url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 200},
                                        format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(response.status_code, 200, response.data)
        with store_context(self.second):
            fixture = AddonSharedFundingTests(methodName='test_refunded_main_with_addon_identifies_store_funding_shortfall')
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            fixture.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            order_id = fixture._addon_then_main_negotiation(addon_refund=100, gift=400,
                                                             before_refund=allocate_before_refund)
            from orders.models import OrderAddonFundingReleaseRun
            run = OrderAddonFundingReleaseRun.objects.get(negotiation__refund__order_id=order_id)
            self.assertEqual((run.allocated_before, run.required_amount, run.released_amount), (200, 180, 20))
            self.assertEqual(sum(run.releases.values_list('amount', flat=True)), 20)
            from orders.points_funding_release import receipt_points_used_locked
            from users.models import PassRefundRepaymentReceipt
            with transaction.atomic():
                receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=run.releases.first().allocation.receipt_id)
                self.assertEqual(receipt_points_used_locked(receipt), 180)
            from orders.models import NegotiationExecution
            execution = NegotiationExecution.objects.get(pk=run.negotiation_id)
            replay = fixture.admin.post(f'/api/admin/aftersales/{execution.proposal.case_id}/negotiation-execution/',
                {'proposal_id': execution.proposal_id, 'client_request_id': 'mixed-main-refund'}, format='json')
            self.assertEqual(replay.data['code'], 0, replay.data)
            self.assertEqual(OrderAddonFundingReleaseRun.objects.filter(negotiation=execution).count(), 1)
            from rest_framework.exceptions import ValidationError
            with transaction.atomic():
                run.ordinary_refund_hash = 'invalid'
                run.save(update_fields=['ordinary_refund_hash'])
                with self.assertRaises(ValidationError):
                    receipt_points_used_locked(receipt)
                transaction.set_rollback(True)

            new_order = fixture.boss.post('/api/orders/create/', fixture.payload(
                client_request_id='released-funding-new-order', compensation_balance_amount=300), format='json')
            self.assertEqual(new_order.data['code'], 0, new_order.data)
            allocated = self.client.post(url, {'request_id': str(uuid4()), 'order_id': new_order.data['data']['id'],
                'amount': 20}, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(allocated.status_code, 200, allocated.data)
            with transaction.atomic():
                receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt.pk)
                self.assertEqual(receipt_points_used_locked(receipt), 200)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       REFUND_DEPOSIT_DEFICIT_ENABLED=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                       ADDON_SETTLEMENT_INTERNAL_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                       SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
                       ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True)
    def test_platform_finance_funds_ordinary_main_refund_with_addon(self):
        from orders.points_funding_evidence import verify_order_funding
        with store_context(self.second):
            fixture = AddonSharedFundingTests(methodName='test_refunded_main_with_addon_identifies_store_funding_shortfall')
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            fixture.admin.defaults['HTTP_HOST'] = 'admin.esports.club'
            order_id = fixture._addon_then_main_negotiation(addon_refund=100, gift=400)
        recorded = self.post_receipt('xinan')
        self.assertEqual(recorded.status_code, 200, recorded.data)
        claim = recorded.data['data']['claim_id']
        verified = self.action(claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        payload = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 100}
        response = self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.post(url, payload, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token).data,
                         response.data)
        with store_context(self.second):
            proof = verify_order_funding(order_id)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (180, 100, 80, False))
        response = self.client.post(url, {**payload, 'request_id': str(uuid4()), 'amount': 80},
                                    format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(response.status_code, 200, response.data)
        with store_context(self.second):
            proof = verify_order_funding(order_id)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (180, 180, 0, True))
            from datetime import timedelta
            from unittest.mock import patch
            from engagement.confirmation import process_order
            from orders.models import OrderPointsSettlement
            from wallet.models import ProviderReport
            report = ProviderReport.objects.get(order_id=order_id, provider_account=fixture.provider)
            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertTrue(process_order(order_id))
                self.assertFalse(process_order(order_id))
            reservation = OrderPointsSettlement.objects.get(order_id=order_id)
            self.assertEqual((reservation.funding_units, reservation.provider_units,
                              reservation.inviter_units, reservation.store_units), (180, 900, 80, 0))
            self.assertEqual(sum(reservation.funding_uses.values_list('amount', flat=True)), 180)
            self.assertEqual(reservation.persons.count(), 1)
            from wallet.services import get_wallet
            self.assertEqual(get_wallet(account=fixture.provider).balance, 900)
            from orders.settlement import SettlementError
            with transaction.atomic():
                use = reservation.funding_uses.order_by('pk').first()
                use.amount -= 1
                use.save(update_fields=['amount'])
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    with self.assertRaises(SettlementError):
                        process_order(order_id)
                transaction.set_rollback(True)

    def test_platform_finance_allocates_and_verifies_the_whole_order(self):
        from orders.points_funding_evidence import verify_order_funding

        with store_context(self.second):
            from stores.test_platform_pass_refunds import PlatformPassRefundTests
            pass_refund_id = PlatformPassRefundTests.create_shortfall(self, 200).pk
            fixture = funding_fixture.ServiceFundingApiTest(methodName='test_ordinary_service_funding_uses_principal_not_gift_as_cash')
            fixture.setUp()
            fixture.boss.defaults['HTTP_HOST'] = 'xa.esports.club'
            game = GameCategory.objects.create(name='门店加购资金测试')
            fixture.service.game_category = game
            fixture.service.save(update_fields=['game_category'])
            fixture.provider_user.escort_profile.service_items.add(fixture.service)
            addon = make_service(name='加购测试', game_category=game, is_addon=True)
            relation = ServiceAddonConfiguration.objects.create(service=fixture.service, addon=addon, enabled=True,
                conditions='', condition_mode='NONE', version=1)
            ServiceAddonPriceOption.objects.create(configuration=relation, unit='HOUR', price=500)
            lot = PointFundingLot.objects.get(account=fixture.account, kind='PREPAID')
            lot.remaining += 500; lot.save(update_fields=['remaining'])
            fixture.wallet.balance += 500; fixture.wallet.save(update_fields=['balance'])
            created = fixture.boss.post('/api/orders/create/', fixture.payload(), format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order = Order.objects.get(pk=created.data['data']['id'])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(fixture.provider)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {}, format='json')
            self.assertEqual(started.data['code'], 0, started.data)
            url = f'/api/orders/orders/{order.pk}/addons/intents/'
            intent = fixture.boss.post(url, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': fixture.provider.pk, 'request_id': 'fund-1',
                'expected_amount': 500, 'gift_balance_amount': 400, 'compensation_balance_amount': 0}, format='json')
            self.assertEqual(intent.status_code, 200, intent.data)
            paid = fixture.boss.post(url + str(intent.data['data']['id']) + '/pay/', {}, format='json')
            self.assertEqual(paid.status_code, 200, paid.data)
            payment_id = paid.data['data']['payment_id']

        recorded = self.post_receipt('xinan')
        self.assertEqual(recorded.status_code, 200, recorded.data)
        claim = recorded.data['data']['claim_id']
        verified = self.action(claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        pass_allocation = self.action(claim, 'allocate', {'request_id': str(uuid4()),
            'refund_id': pass_refund_id, 'amount': 200})
        self.assertEqual(pass_allocation.status_code, 200, pass_allocation.data)
        allocation_url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        body = {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 40}
        allocated = self.client.post(allocation_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(allocated.status_code, 200, allocated.data)
        replay = self.client.post(allocation_url, body, format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(replay.data, allocated.data)
        with store_context(self.second):
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (100, 40, 60, False))
        rest = self.client.post(allocation_url, {**body, 'request_id': str(uuid4()), 'amount': 60},
                                format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
        self.assertEqual(rest.status_code, 200, rest.data)
        with store_context(self.second):
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (100, 100, 0, True))
            AfterSale.objects.create(order=order, account=fixture.account, reason='加购部分退款')
            support = staff_client(['order:refund'])
            admin_path = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            user_path = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
            proposed = support.post(admin_path, {'request_id': 'partial-add-on', 'amount_raw': 150,
                                                 'explanation': '加购项目部分未履行'}, format='json')
            self.assertEqual(proposed.status_code, 200, proposed.data)
            proposal_id = proposed.data['data']['id']
            boss_confirmed = fixture.boss.post(user_path + f'{proposal_id}/', {'accepted': True}, format='json')
            self.assertEqual(boss_confirmed.status_code, 200, boss_confirmed.data)
            provider_confirmed = player.post(user_path + f'{proposal_id}/', {'accepted': True}, format='json')
            self.assertEqual(provider_confirmed.data['data']['status'], 'ACCEPTED')
            refunded = support.post(admin_path + f'{proposal_id}/execute/', {}, format='json')
            self.assertEqual(refunded.status_code, 200, refunded.data)
            self.assertTrue(refunded.data['data']['refund_executed'])
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (50, 50, 0, True))
            second = support.post(admin_path, {'request_id': 'partial-add-on-2', 'amount_raw': 50,
                                               'explanation': '追加退回剩余部分'}, format='json')
            self.assertEqual(second.status_code, 200, second.data)
            second_id = second.data['data']['id']
            self.assertEqual(fixture.boss.post(user_path + f'{second_id}/', {'accepted': True}, format='json').status_code, 200)
            self.assertEqual(player.post(user_path + f'{second_id}/', {'accepted': True}, format='json').status_code, 200)
            final_refund = support.post(admin_path + f'{second_id}/execute/', {}, format='json')
            self.assertEqual(final_refund.status_code, 200, final_refund.data)
            proof = verify_order_funding(order.pk)
            self.assertEqual((proof.required, proof.allocated, proof.remaining, proof.fully_funded), (0, 0, 0, True))
        overview = self.reconciliation()
        self.assertEqual(overview.status_code, 200, overview.data)
        self.assertEqual(overview.data['data']['claims'][0]['receipt']['unallocated_amount'], 300)
        with store_context(self.second):
            next_pass_refund = PlatformPassRefundTests.create_shortfall(self, 250)
        reuse = self.action(claim, 'allocate', {'request_id': str(uuid4()),
            'refund_id': next_pass_refund.pk, 'amount': 250})
        self.assertEqual(reuse.status_code, 200, reuse.data)
        self.assertEqual(self.reconciliation().data['data']['claims'][0]['receipt']['unallocated_amount'], 50)
