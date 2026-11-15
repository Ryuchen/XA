"""Room completion uses the real, store-scoped points funding settlement path."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.confirmation import process_order
from engagement.models import ConsumptionConfirmation, PointLedger
from orders.models import Order, OrderPointsSettlement, OrderPointsSettlementPerson, OrderProvider
from orders.tests.factories import make_console_user
from orders.tests.test_points_checkout_integration import PointsCheckoutIntegrationTests, PointsFundingPlatformRecoveryTests
from orders.tests.test_points_redemption_preview import PointsServiceQuoteTest
from orders.tests.test_release_flow import screenshot
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context
from wallet.models import ProviderReport, Transaction


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
                   DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                   SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False,
                   ROOM_COMPLETION_REVIEW_ENABLED=True, ROOM_BOSS_CONFIRMATION_ENABLED=True,
                   ROOM_AUTO_CONFIRMATION_ENABLED=True)
class RoomPointsFundingTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    payload = PointsServiceQuoteTest.payload
    quoted_payload = PointsCheckoutIntegrationTests.quoted_payload
    setUp = PointsFundingPlatformRecoveryTests.setUp
    fund = PointsFundingPlatformRecoveryTests.fund
    verify = PointsFundingPlatformRecoveryTests.verify

    def create_assigned_points_order(self):
        from console.room_confirmation_config import save_room_confirmation
        save_room_confirmation(30, 0, lambda *_: None)
        self.service.room_enabled = True
        self.service.save(update_fields=['room_enabled'])
        return PointsCheckoutIntegrationTests.create_assigned_points_order(self)

    def test_room_completion_waits_for_full_points_funding_before_income_and_vip(self):
        with TemporaryDirectory(prefix='xa-room-points-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                self.assertTrue(order.room_required_snapshot)
                assignment = OrderProvider.objects.get(order=order)
                player = APIClient(HTTP_HOST='xa.esports.club',
                    HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(assignment.provider_account)[0])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.account)[0])
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    response = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                request = player.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                    {'description': '服务完成', 'client_request_id': 'points-room'}, format='json')
                self.assertEqual(request.status_code, 200, request.data)
                pk = request.data['data']['id']
                admin = make_console_user(['order:view', 'support:edit', 'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                response = reviewer.post(f'/api/admin/room-completion-requests/{pk}/review/',
                    {'decision': 'VERIFIED', 'note': '已核实', 'client_request_id': 'points-review'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/room/completion-requests/{pk}/confirm/',
                    {'client_request_id': 'points-confirm'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                report = ProviderReport.objects.get(order=order)
                response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                report.refresh_from_db(); matured = report.submitted_at + timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=matured):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertEqual(self.verify().status_code, 200)
            self.assertEqual(self.fund().status_code, 200)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=order.pk).exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
                self.assertFalse(PointLedger.objects.filter(order_id=order.pk, kind='CONSUMPTION').exists())
            registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan',
                {**self.data, 'request_id': str(uuid4()), 'external_reference': 'TX-ROOM-POINTS-2', 'amount': 600})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            response = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(response.status_code, 200, response.data)
            response = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 500}, format='json',
                HTTP_AUTHORIZATION='Bearer '+self.token)
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                self.assertTrue(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order_id=order.pk)
                self.assertEqual((settlement.paid_units, settlement.funding_units, settlement.provider_units), (4000, 800, 4800))
                self.assertEqual(OrderPointsSettlementPerson.objects.get(settlement=settlement).amount, 4800)
                self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order.pk).amount, 4000)
                self.assertEqual(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').count(), 1)
                self.assertFalse(process_order(order.pk))
            with store_context(self.first):
                self.assertFalse(OrderPointsSettlement.objects.exists())

    @override_settings(REQUIRE_RECHARGE_PROOF=False)
    def test_double_room_requires_whole_funding_then_individual_report_clocks(self):
        from django.utils import timezone
        from engagement.models import MemberProfile
        from orders.models import OrderApplication
        from orders.points_redemption_config import current_rules
        from console.points_redemption_config import save_points_terms
        from orders.tests.factories import make_boss_type, make_provider, make_service, make_user
        from users.models import EscortLevel, EscortProfile
        from wallet.services import get_wallet
        with TemporaryDirectory(prefix='xa-room-points-double-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                user = make_user()
                self.account = get_or_create_account_for_legacy_user(user)
                self.account.boss_type = make_boss_type(discount_rate=90)
                self.account.save(update_fields=['boss_type'])
                self.wallet = get_wallet(user=user, account=self.account)
                recharge_staff = make_console_user(['wallet:recharge'])
                recharge_account = get_or_create_account_for_legacy_user(recharge_staff)
                recharge_staff.admin_membership.account = recharge_account
                recharge_staff.admin_membership.save(update_fields=['account'])
                recharge_client = APIClient(HTTP_HOST='admin.esports.club',
                    HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(recharge_account)[0])
                quote = recharge_client.get('/api/admin/wallets/recharge-quote/', {'amount': 25000})
                self.assertEqual(quote.status_code, 200, quote.data)
                result = recharge_client.post('/api/admin/wallets/recharge/', {
                    'account_id': self.account.pk, 'amount': 25000,
                    'gift_amount': quote.data['data']['gift_amount'],
                    'bonus_version': quote.data['data']['version'],
                    'trade_no': 'room-double-recharge', 'client_request_id': 'room-double-recharge',
                }, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                self.wallet.refresh_from_db()
                from engagement.services import wallet_funding_parts
                self.assertEqual(wallet_funding_parts(self.wallet)['prepaid_balance'], 25000)
                self.assertTrue(Transaction.objects.filter(wallet=self.wallet, tx_type='TOPUP', amount=25000).exists())
                self.profile = MemberProfile.objects.create(account=self.account, points_balance=15000)
                self.service = make_service(price=10000, billing_provider_count=2, commission_policy='REGULAR', room_enabled=True)
                self.client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.account)[0])
                boss = self.client
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                    max_ratio_basis_points=5000, version=current_rules()['version'], audit=lambda *_: None)
                response = boss.post('/api/orders/create/', self.quoted_payload(), format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order = Order.objects.get(pk=response.data['data']['id'])
                Order.objects.filter(pk=order.pk).update(created_at=timezone.now()-timedelta(hours=1))
                accounts, players = [], []
                for rate in (20, 30):
                    provider = make_provider(deposit_mode='EARNINGS')
                    account = get_or_create_account_for_legacy_user(provider)
                    level = EscortLevel.objects.create(name=f'房间积分双陪-{rate}', commission_rate=rate)
                    EscortProfile.objects.filter(user=provider).update(account=account, level=level)
                    accounts.append(account)
                    players.append(APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0]))
                response = players[0].post(f'/api/orders/orders/{order.pk}/grab/',
                    {'team_size': 2, 'partner_id': accounts[1].pk}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                application = OrderApplication.objects.get(order=order, applicant=accounts[0])
                response = players[1].post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/action/',
                    {'action': 'CONFIRM_TEAM'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/applications/{application.pk}/select/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    for player in players:
                        response = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                        self.assertEqual(response.data['code'], 0, response.data)
                order.refresh_from_db(); self.assertEqual(order.status, 'IN_SERVICE')
                response = players[0].post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                    {'description': '双陪履约完成', 'client_request_id': 'double-room'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                pk = response.data['data']['id']
                admin = make_console_user(['order:view', 'support:edit', 'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                response = reviewer.post(f'/api/admin/room-completion-requests/{pk}/review/',
                    {'decision': 'VERIFIED', 'note': '核实通过', 'client_request_id': 'double-review'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/room/completion-requests/{pk}/confirm/',
                    {'client_request_id': 'double-confirm'}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                reports = [ProviderReport.objects.get(order=order, provider_account=account) for account in accounts]
                for player, report in zip(players, reports):
                    response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                        {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = players[0].post(f'/api/wallet/reports/{reports[0].pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                reports[0].refresh_from_db(); first = reports[0].submitted_at
                response = reviewer.post(f'/api/admin/reports/{reports[0].pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                with patch('django.utils.timezone.now', return_value=first+timedelta(hours=24)):
                    response = players[1].post(f'/api/wallet/reports/{reports[1].pk}/submit/', {}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                    response = reviewer.post(f'/api/admin/reports/{reports[1].pk}/approve/', {}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 249})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first+timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=order.pk).exists())
                self.assertFalse(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first+timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
                self.assertEqual(list(OrderPointsSettlementPerson.objects.filter(settlement__order_id=order.pk).values_list('amount', flat=True)), [6000])
                self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
            with store_context(self.second), patch('django.utils.timezone.now', return_value=first+timedelta(hours=97)):
                self.assertTrue(process_order(order.pk))
                people = OrderPointsSettlementPerson.objects.filter(settlement__order_id=order.pk)
                self.assertEqual(sorted(people.values_list('amount', flat=True)), [5250, 6000])
                self.assertTrue(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
                self.assertEqual(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').count(), 2)
                self.assertFalse(process_order(order.pk))

    @override_settings(SERVICE_ORDINARY_FUNDING_TESTING=True)
    def test_room_gift_balance_requires_store_funding_even_without_points(self):
        from orders.tests.test_service_funding_api import ServiceFundingApiTest
        with TemporaryDirectory(prefix='xa-room-gift-funding-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                ServiceFundingApiTest.setUp(self)
                self.service.room_enabled = True
                self.service.save(update_fields=['room_enabled'])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.account)[0])
                options = boss.post('/api/orders/discount-options-preview/', {'service_id': self.service.pk}, format='json')
                self.assertEqual(options.data['code'], 0, options.data)
                payload = ServiceFundingApiTest.payload(self, gift_balance_amount=400,
                    discount_selection_token=options.data['data']['options'][0]['selection_token'])
                created = boss.post('/api/orders/create/', payload, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order = Order.objects.get(pk=created.data['data']['id'])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(self.provider)[0])
                for action, key in (('start', 'entry_image'), ('complete', 'completion_image')):
                    result = player.post(f'/api/orders/orders/{order.pk}/{action}/', {key: screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                result = player.post(f'/api/orders/orders/{order.pk}/room/completion-requests/',
                    {'description': '履约完成', 'client_request_id': 'gift-room'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                pk = result.data['data']['id']
                admin = make_console_user(['order:view', 'support:edit', 'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                result = reviewer.post(f'/api/admin/room-completion-requests/{pk}/review/',
                    {'decision': 'VERIFIED', 'note': '核实完成', 'client_request_id': 'gift-review'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                result = boss.post(f'/api/orders/orders/{order.pk}/room/completion-requests/{pk}/confirm/',
                    {'client_request_id': 'gift-confirm'}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                report = ProviderReport.objects.get(order=order)
                self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind': 'RESULT', 'image': screenshot()}, format='multipart').data['code'], 0)
                self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                self.assertEqual(reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 0)
                report.refresh_from_db(); matured = report.submitted_at+timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=matured):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.client = self.platform_client
            self.assertEqual(self.verify().status_code, 200)
            response = self.client.post(f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/service-allocation',
                {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 200}, format='json',
                HTTP_AUTHORIZATION='Bearer '+self.token)
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                self.assertTrue(process_order(order.pk))
                settlement = OrderPointsSettlement.objects.get(order_id=order.pk)
                self.assertEqual((settlement.paid_units, settlement.principal_units, settlement.gift_units,
                                  settlement.funding_units, settlement.provider_units), (1000, 600, 400, 200, 800))
                self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order.pk).amount, 1000)
                self.assertEqual(PointLedger.objects.get(order_id=order.pk, kind='CONSUMPTION').amount, 900)
                self.assertFalse(process_order(order.pk))

    @override_settings(SERVICE_POINTS_NEGOTIATION_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                       NEGOTIATION_REFUND_EXECUTION_ENABLED=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
                       SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
                       SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, SERVICE_POINTS_REFUNDED_SETTLEMENT_TESTING=True,
                       SERVICE_POINTS_REFUNDED_CONSUMPTION_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True)
    def test_points_room_negotiated_refund_confirms_net_vip_only_after_funding(self):
        from engagement.models import RefundedPointsConsumptionEvidence
        from orders.models import OrderPointsRefundSettlementPerson
        self.assertEqual(self.verify().status_code, 200)
        self.assertEqual(self.fund().status_code, 200)  # First 300 of the eventual 460 shortfall.
        with TemporaryDirectory(prefix='xa-room-points-refund-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                assignment = OrderProvider.objects.get(order=order)
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(assignment.provider_account)[0])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(order.customer_account)[0])
                response = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
                response = boss.post(f'/api/orders/orders/{order.pk}/room/objections/',
                    {'reason': '对房间服务有异议', 'client_request_id': 'points-refund-room-objection'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                objection_id = response.data['data']['id']
                admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                clerk = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                response = clerk.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '协商终止', 'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'points-room-case'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                case = response.data['data']['id']
                response = clerk.post(f'/api/admin/aftersales/{case}/negotiation-proposal/', {
                    'request_id': 'points-room-proposal', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': assignment.provider_account_id, 'retained_units': 3800}],
                    'explanation': '协商保留收益并按原构成退款'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                proposal_id = response.data['data']['id']
                for actor in (boss, player):
                    response = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                        {'proposal_id': proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(response.data['code'], 0, response.data)
                execution_payload = {'proposal_id': proposal_id, 'client_request_id': 'points-room-execution'}
                response = clerk.post(f'/api/admin/aftersales/{case}/negotiation-execution/', execution_payload, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                replay = clerk.post(f'/api/admin/aftersales/{case}/negotiation-execution/', execution_payload, format='json')
                self.assertEqual(replay.data['code'], 0, replay.data)
                from orders.models import OrderRoomObjection
                objection = OrderRoomObjection.objects.get(pk=objection_id)
                self.assertEqual((objection.status, objection.resolution_proposal_id), ('RESOLVED', proposal_id))
                self.assertEqual(order.refund_records.count(), 1)
                order.refresh_from_db()
                self.assertEqual((order.status, order.payment_status), ('COMPLETED', 'PARTIALLY_REFUNDED'))
                report = ProviderReport.objects.get(order=order)
                for kind in ('COMPLETION', 'RESULT'):
                    response = player.post(f'/api/wallet/reports/{report.pk}/images/',
                        {'kind': kind, 'image': screenshot()}, format='multipart')
                    self.assertEqual(response.data['code'], 0, response.data)
                response = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                response = clerk.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                report.refresh_from_db()
                self.assertEqual(report.payout_amount, 3800)
                matured = max(order.completed_at, report.submitted_at)+timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=matured):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
                self.assertFalse(PointLedger.objects.filter(order=order, kind='CONSUMPTION').exists())
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            response = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 160})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=matured):
                self.assertTrue(process_order(order.pk))
                person = OrderPointsRefundSettlementPerson.objects.get(settlement__posting__proposal__case__order_id=order.pk)
                self.assertEqual((person.amount, person.income_transaction.amount), (3800, 3800))
                evidence = RefundedPointsConsumptionEvidence.objects.get(settlement__posting__proposal__case__order_id=order.pk)
                self.assertEqual((evidence.paid_units, evidence.refunded_coin_units, evidence.net_paid_units,
                                  evidence.net_principal_units, evidence.awarded_points), (4000, 660, 3340, 3340, 3340))
                self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order.pk).refunded_amount, 660)
                self.assertFalse(process_order(order.pk))
