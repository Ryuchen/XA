from datetime import timedelta
from unittest.mock import patch
from tempfile import TemporaryDirectory

from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from orders.models import NegotiationExecution, Order, OrderParticipationSegment
from orders.negotiation_proposals import decide
from orders.negotiation_termination import execute_and_terminate
from orders.tests.test_negotiation_execution import NegotiationExecutionPostingTests
from wallet.models import ProviderReport, Transaction
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiationTerminationTests(APITestCase):
    account = NegotiationExecutionPostingTests.account
    client_for = NegotiationExecutionPostingTests.client_for
    case = NegotiationExecutionPostingTests.case
    propose = NegotiationExecutionPostingTests.propose

    def setUp(self):
        NegotiationExecutionPostingTests.setUp(self)
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self.media.name))
        self.order.settlement_mode = 'REPORT_APPROVAL'
        self.order.in_service_at = timezone.now() - timedelta(hours=1)
        self.order.service_clock_version = 1
        self.order.service_aftersale_hours = 72
        self.order.service_freeze_hours = 72
        self.order.service_deposit_percent = 60
        self.order.funding_terms = {'vip_enabled': True, 'points_enabled': True,
                                    'points_rate': 100, 'birthday_multiplier': 1}
        self.order.save(update_fields=['settlement_mode', 'in_service_at', 'service_clock_version',
                                       'service_aftersale_hours', 'service_freeze_hours', 'service_deposit_percent',
                                       'funding_terms'])
        from engagement.models import OrderFundingAllocation, PointFundingLot
        lot = PointFundingLot.objects.create(account=self.boss_account, source_key='negotiated-termination-test',
                                             kind='PREPAID', remaining=0)
        OrderFundingAllocation.objects.create(order=self.order, lot=lot, kind='PREPAID', amount=self.order.amount)
        get_wallet(user=self.a, account=self.a_account)
        get_wallet(user=self.b, account=self.b_account)

    def accepted(self, *, scope=None, amount=1000):
        scope = scope or [self.a_account.pk]
        case = self.case(scope)
        proposal = self.propose(case, scope=scope, refund=amount)
        for account in [self.boss_account, *[self.a_account if pk == self.a_account.pk else self.b_account for pk in scope]]:
            decide(case.pk, actor=account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        return case, proposal

    def execute(self, case, proposal):
        return execute_and_terminate(case.pk, proposal.pk, operator=self.operator_account,
                                     legacy_operator=self.operator, request_id='termination-test')

    def test_only_affected_provider_stops_and_other_provider_continues(self):
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        self.assertEqual(self.execute(case, proposal).pk, receipt.pk)
        self.order.refresh_from_db(); case.refresh_from_db()
        a = self.order.providers.get(provider_account=self.a_account)
        b = self.order.providers.get(provider_account=self.b_account)
        self.assertEqual((self.order.status, self.order.provider_account_id), ('IN_SERVICE', self.b_account.pk))
        self.assertEqual(a.released_at, proposal.accepted_at)
        self.assertIsNone(b.released_at)
        self.assertEqual(case.status, 'PARTIALLY_REFUNDED')
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(ProviderReport.objects.filter(order=self.order).count(), 2)
        self.assertEqual(OrderParticipationSegment.objects.get(attribution__order=self.order,
            provider_account=self.a_account).released_at, proposal.accepted_at)
        self.assertFalse(receipt.completed_by_negotiation)

    def test_legacy_refund_preview_cannot_ignore_existing_negotiated_refund(self):
        from orders.refunds import RefundError, build_refund_plan
        stale = Order.objects.prefetch_related('refund_records').get(pk=self.order.pk)
        self.assertEqual(list(stale.refund_records.all()), [])
        case, proposal = self.accepted()
        self.execute(case, proposal)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = boss_wallet.balance
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False, NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
            with self.assertRaises(RefundError) as rejected:
                build_refund_plan(stale, refund_amount=9000)
        self.assertEqual(rejected.exception.code, 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE')
        boss_wallet.refresh_from_db()
        self.assertEqual(boss_wallet.balance, before)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_legacy_refund_cannot_ignore_existing_negotiated_refund(self):
        from orders.refunds import RefundError
        from orders.services import refund_to_customer
        stale = Order.objects.prefetch_related('refund_records').get(pk=self.order.pk)
        self.assertEqual(list(stale.refund_records.all()), [])
        case, proposal = self.accepted()
        self.execute(case, proposal)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = boss_wallet.balance
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False, NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
            with self.assertRaises(RefundError) as rejected:
                refund_to_customer(stale, refund_amount=9000, client_request_id='legacy-additional-refund')
        self.assertEqual(rejected.exception.code, 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE')
        boss_wallet.refresh_from_db()
        self.assertEqual(boss_wallet.balance, before)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_missing_negotiation_marker_still_blocks_legacy_refund_using_receipt(self):
        from django.test.utils import CaptureQueriesContext
        from orders.refunds import RefundError, build_refund_plan
        from stores.db import transaction
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        effects = dict(receipt.refund.benefit_effects)
        del effects['negotiation_execution_version']
        receipt.refund.benefit_effects = effects
        receipt.refund.save(update_fields=['benefit_effects'])
        with CaptureQueriesContext(transaction.get_connection()) as queries:
            with self.assertRaises(RefundError) as rejected:
                build_refund_plan(self.order, refund_amount=9000)
        self.assertEqual(rejected.exception.code, 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE')
        receipt_queries = [row['sql'] for row in queries if 'orders_negotiationexecution' in row['sql'].lower()]
        self.assertTrue(receipt_queries)
        self.assertTrue(all('zero_order_id' not in sql and 'zero_request_id' not in sql
                            and 'zero_allocation' not in sql for sql in receipt_queries))
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_legacy_store_without_execution_table_allows_ordinary_refund_history(self):
        from orders.models import OrderRefund
        from orders.refunds import reject_additional_negotiation_refund
        from stores.db import transaction
        OrderRefund.objects.create(order=self.order, amount=1000, reason='旧订单普通退款')
        connection = transaction.get_connection()
        with patch.object(connection.introspection, 'table_names', return_value=[]):
            reject_additional_negotiation_refund(self.order)

    def test_staff_legacy_refund_endpoints_do_not_post_additional_refund(self):
        case, proposal = self.accepted()
        self.execute(case, proposal)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = boss_wallet.balance
        client = self.client_for(self.operator_account)
        preview = client.get(f'/api/admin/orders/{self.order.pk}/refund-preview/', {'refund_amount':9000})
        self.assertEqual(preview.data.get('errorCode'), 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE', preview.data)
        result = client.post(f'/api/admin/orders/{self.order.pk}/refund/', {
            'reason':'追加退款', 'refund_amount':9000, 'client_request_id':'legacy-extra'}, format='json')
        self.assertEqual(result.data.get('errorCode'), 'NEGOTIATION_ADDITIONAL_REFUND_UNAVAILABLE', result.data)
        boss_wallet.refresh_from_db()
        self.assertEqual(boss_wallet.balance, before)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_staff_can_record_new_case_for_remaining_provider_after_partial_refund(self):
        from orders.negotiation_execution import verify_execution
        from stores.db import transaction as store_transaction
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = boss_wallet.balance
        client = self.client_for(self.staff)
        detail = client.get(f'/api/admin/orders/{self.order.pk}/')
        self.assertTrue(detail.data['data']['negotiation_continuation_supported'])
        payload = {'order_id':self.order.pk, 'reason':'老板申请处理另一位陪玩的服务',
                   'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'remaining-case'}
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False, NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
            result = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.assertIn('退款暂未接通', result.data['msg'])
        self.assertEqual(result.data['data']['id'], case.pk)
        case.refresh_from_db()
        self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        self.assertTrue(any(row['client_request_id'] == proposal.case_request_id
                            and row['status'] == 'PARTIALLY_REFUNDED' for row in case.history))
        with store_transaction.atomic():
            verify_execution(receipt)
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=True):
            with self.assertRaisesMessage(ValidationError, '退款后的新售后方案尚未接通'):
                self.propose(case, version=proposal.version, request_id='unsupported-next-proposal',
                             scope=[self.b_account.pk], refund=1000)
        boss_wallet.refresh_from_db()
        self.assertEqual(boss_wallet.balance, before)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_corrupt_prior_refund_cannot_open_case_for_remaining_provider(self):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        receipt.customer_transaction.amount -= 10
        receipt.customer_transaction.save(update_fields=['amount'])
        result = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'reason':'请求处理另一位陪玩的服务',
            'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'bad-prior'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        case.refresh_from_db()
        self.assertEqual(case.status, 'PARTIALLY_REFUNDED')
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_corrupt_prior_case_history_cannot_open_case_for_remaining_provider(self):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        self.execute(case, proposal)
        case.refresh_from_db()
        case.history[-1]['resolution'] = '已被修改'; case.save(update_fields=['history'])
        result = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'reason':'请求处理另一位陪玩的服务',
            'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'bad-prior-history'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        case.refresh_from_db()
        self.assertEqual(case.status, 'PARTIALLY_REFUNDED')

    def test_fully_refunded_scoped_order_can_record_remaining_provider_issue(self):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 6000; wallet.save(update_fields=['balance'])
        case, proposal = self.accepted(amount=10000)
        self.execute(case, proposal)
        client = self.client_for(self.staff)
        payload = {'order_id':self.order.pk, 'reason':'核对继续履约的陪玩',
                   'affected_provider_account_ids':[self.a_account.pk], 'client_request_id':'full-refund-next'}
        invalid = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)
        case.refresh_from_db(); self.assertEqual(case.status, 'REFUNDED')
        payload['affected_provider_account_ids'] = [self.b_account.pk]
        result = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.payment_status, case.status, case.affected_provider_account_ids),
                         ('REFUNDED', 'OPEN', [self.b_account.pk]))
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_new_remaining_provider_case_blocks_only_their_settlement(self):
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        opened = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'reason':'处理继续服务的陪玩',
            'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'remaining-settlement'}, format='json')
        self.assertEqual(opened.data.get('code'), 0, opened.data)
        completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(completed.data.get('code'), 0, completed.data)
        self.order.refresh_from_db()
        submitted = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
        due = submitted + timedelta(hours=73)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
        self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 3000)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'order:settle']; role.save(update_fields=['permissions'])
        resolved = self.client_for(self.operator_account).post(f'/api/admin/aftersales/{case.pk}/resolve/',
            {'resolution':'已核实并处理'}, format='json')
        self.assertEqual(resolved.data.get('code'), 0, resolved.data)
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.b.pk])
        self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [])
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000)

    def test_new_remaining_provider_case_notifies_only_boss_and_affected_provider(self):
        from site_messages.models import Message
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        self.execute(case, proposal)
        client = self.client_for(self.staff)
        payload = {'order_id':self.order.pk, 'reason':'对继续履约陪玩提出售后',
                   'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'case-notice-new'}
        with self.captureOnCommitCallbacks(execute=True):
            first = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        with self.captureOnCommitCallbacks(execute=True):
            retry = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
        self.assertEqual((first.data.get('code'), retry.data.get('code')), (0, 0))
        notices = Message.objects.filter(related_order_id=self.order.pk,
            title__in=['售后已登记', '订单售后处理中'])
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.boss_account.pk, self.b_account.pk})
        self.assertEqual(notices.count(), 2)
        self.assertNotIn('对继续履约陪玩提出售后', str(list(notices.values('preview', 'detail'))))

    def test_unaffected_team_member_can_complete_after_scoped_refund(self):
        from orders.services import count_capacity_orders
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        self.execute(case, proposal)
        self.assertEqual(count_capacity_orders(account_id=self.a_account.pk, provider_id=self.a.pk), 0)
        self.assertEqual(count_capacity_orders(account_id=self.b_account.pk, provider_id=self.b.pk), 1)
        rejected = self.client_for(self.a_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(rejected.data['code'], 403)
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')
        self.assertEqual(ProviderReport.objects.filter(order=self.order).count(), 2)

    def test_remaining_team_cannot_complete_with_corrupt_refund_receipt(self):
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        tx = receipt.customer_transaction; tx.amount -= 10; tx.save(update_fields=['amount'])
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data['code'], 400, result.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')

    def test_unfinished_points_integration_cannot_post_any_refund(self):
        from orders.points_service_payment import MARKER
        case, proposal = self.accepted()
        self.order.funding_terms = {**self.order.funding_terms, MARKER: {'receipt_id': 1}}
        self.order.save(update_fields=['funding_terms'])
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = wallet.balance
        with self.assertRaisesMessage(ValidationError, '积分抵扣订单的协商退款尚未接通'):
            self.execute(case, proposal)
        wallet.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.balance, case.status), (before, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    def test_unreconciled_historical_sources_cannot_post_any_refund(self):
        case, proposal = self.accepted()
        self.order.funding_terms = None
        self.order.save(update_fields=['funding_terms'])
        with self.assertRaisesMessage(ValidationError, '历史付款来源尚未完成核对'):
            self.execute(case, proposal)
        self.assertFalse(self.order.refund_records.exists())

    def test_related_order_and_previously_settled_share_fail_before_refund(self):
        from orders.tests.factories import make_order
        case, proposal = self.accepted()
        addon = make_order(self.boss, service=self.service, provider=self.a, source_order=self.order,
                           status='COMPLETED', payment_status='PAID')
        with self.assertRaisesMessage(ValidationError, '有关联服务订单'):
            self.execute(case, proposal)
        self.assertFalse(self.order.refund_records.exists())
        addon.source_order = None; addon.save(update_fields=['source_order'])
        self.order.providers.filter(provider_account=self.b_account).update(settled_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, '未完成订单的收益已结算'):
            self.execute(case, proposal)
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_staff_execution_posts_wallet_and_cash_deposit_once(self):
        from wallet.models import RefundDepositDeficit
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile, platform = NegotiationExecutionPostingTests.cash_deposit(self, 500)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = boss_wallet.balance
        case, proposal = self.accepted(amount=5000)
        client = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'staff-wallet-deposit'}
        result = client.post(url, payload, format='json')
        self.assertEqual((result.status_code, result.data['code']), (200, 0), result.data)
        self.assertEqual(client.post(url, payload, format='json').data['data']['execution_id'],
                         result.data['data']['execution_id'])
        boss_wallet.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db(); platform.refresh_from_db()
        self.assertEqual((boss_wallet.balance, wallet.balance, profile.deposit_paid), (before + 5000, 0, 100))
        deficit = RefundDepositDeficit.objects.get(refund__order=self.order, account=self.a_account)
        self.assertEqual((deficit.amount, deficit.remaining), (400, 400))
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_legacy_single_identity_wallet_is_resolved_in_refund_transaction(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.account = None; wallet.balance = 1000
        wallet.save(update_fields=['account', 'balance'])
        case, proposal = self.accepted(amount=5000)
        receipt = self.execute(case, proposal)
        wallet.refresh_from_db()
        self.assertEqual((wallet.account_id, wallet.user_id, wallet.balance), (self.a_account.pk, self.a.pk, 0))
        self.assertEqual(receipt.shares.get(account=self.a_account).wallet_deduction, 1000)

    def test_conflicting_legacy_wallet_cannot_be_backfilled_or_refunded(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.account = self.viewer; wallet.balance = 1000
        wallet.save(update_fields=['account', 'balance'])
        case, proposal = self.accepted(amount=5000)
        with self.assertRaisesMessage(ValidationError, '钱包或押金归属不完整'):
            self.execute(case, proposal)
        wallet.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.account_id, wallet.balance, case.status), (self.viewer.pk, 1000, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    def test_wallet_identity_backfill_rolls_back_with_failed_refund(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.account = None; wallet.balance = 1000
        wallet.save(update_fields=['account', 'balance'])
        case, proposal = self.accepted(amount=5000)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesMessage(RuntimeError, 'audit failure'):
                self.execute(case, proposal)
        wallet.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.account_id, wallet.balance, case.status), (None, 1000, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    def test_missing_legacy_account_mapping_cannot_bind_or_refund(self):
        from club_accounts.models import LegacyAccountMap
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.account = None; wallet.balance = 1000
        wallet.save(update_fields=['account', 'balance'])
        case, proposal = self.accepted(amount=5000)
        LegacyAccountMap.objects.filter(account=self.a_account, legacy_user_id=self.a.pk).delete()
        with self.assertRaisesMessage(ValidationError, '钱包或押金归属不完整'):
            self.execute(case, proposal)
        wallet.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.account_id, wallet.balance, case.status), (None, 1000, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=False)
    def test_disabled_deposit_accounting_rejects_before_any_refund(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile, _ = NegotiationExecutionPostingTests.cash_deposit(self, 500)
        boss = get_wallet(user=self.boss, account=self.boss_account); before = boss.balance
        case, proposal = self.accepted(amount=5000)
        response = self.client_for(self.operator_account).post(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'disabled-deposit'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('押金处理尚未启用', str(response.data))
        boss.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((boss.balance, wallet.balance, profile.deposit_paid), (before, 600, 500))
        self.assertFalse(self.order.refund_records.exists())

    def test_failed_audit_rolls_back_refund_and_termination(self):
        case, proposal = self.accepted()
        wallet = get_wallet(user=self.boss, account=self.boss_account); before = wallet.balance
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('audit failure')):
            with self.assertRaisesMessage(RuntimeError, 'audit failure'):
                self.execute(case, proposal)
        wallet.refresh_from_db(); self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.balance, self.order.status, case.status), (before, 'IN_SERVICE', 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertFalse(self.order.providers.filter(released_at__isnull=False).exists())

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_missing_business_audit_blocks_replay_and_result_lookup(self):
        from console.models import AdminAuditLog
        case, proposal = self.accepted()
        receipt = self.execute(case, proposal)
        wallet = get_wallet(user=self.boss, account=self.boss_account); before = wallet.balance
        AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/').delete()
        with self.assertRaisesMessage(ValidationError, '审计凭证待核对'):
            self.execute(case, proposal)
        result = self.client_for(self.operator_account).get(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'termination-test'})
        self.assertEqual(result.status_code, 400)
        self.assertIn('审计凭证待核对', str(result.data))
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, before)
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(receipt.refund_id, self.order.refund_records.get().pk)

    @override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
    def test_missing_payment_snapshot_blocks_result_lookup(self):
        case, proposal = self.accepted()
        self.execute(case, proposal)
        self.order.funding_terms = None; self.order.save(update_fields=['funding_terms'])
        result = self.client_for(self.operator_account).get(
            f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal.pk, 'client_request_id':'termination-test'})
        self.assertEqual(result.status_code, 400)
        self.assertIn('付款来源待核对', str(result.data))

    def test_all_affected_order_completes_at_agreement_time(self):
        scope = sorted([self.a_account.pk, self.b_account.pk])
        case, proposal = self.accepted(scope=scope, amount=510)
        receipt = self.execute(case, proposal)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', proposal.accepted_at))
        self.assertTrue(receipt.completed_by_negotiation)
        self.assertEqual(self.order.providers.filter(released_at=proposal.accepted_at).count(), 2)
        self.assertEqual(ProviderReport.objects.filter(order=self.order).count(), 2)
        self.assertEqual(case.status, 'PARTIALLY_REFUNDED')
        self.assertFalse(self.order.settled_at)

    def test_already_completed_order_keeps_its_original_completion_time(self):
        case = self.case(); proposal = self.propose(case)
        completed = timezone.now()
        self.order.status = 'COMPLETED'; self.order.completed_at = completed
        self.order.save(update_fields=['status', 'completed_at'])
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        receipt = self.execute(case, proposal)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at), ('COMPLETED', completed))
        self.assertFalse(receipt.completed_by_negotiation)
        self.assertEqual(case.status, 'PARTIALLY_REFUNDED')

    def test_invalid_clock_rolls_back_refund_and_does_not_close_case(self):
        case, proposal = self.accepted()
        self.order.service_freeze_hours = None; self.order.save(update_fields=['service_freeze_hours'])
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = (wallet.balance, Transaction.objects.count())
        with self.assertRaisesMessage(ValidationError, '结算规则待核对'):
            self.execute(case, proposal)
        wallet.refresh_from_db(); case.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((wallet.balance, Transaction.objects.count()), before)
        self.assertEqual((case.status, self.order.status, self.order.payment_status), ('OPEN', 'IN_SERVICE', 'PAID'))
        self.assertFalse(NegotiationExecution.objects.exists())

    def test_report_creation_failure_rolls_back_money_and_participation(self):
        case, proposal = self.accepted()
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = wallet.balance
        with patch('orders.services.ensure_fulfillment_reports', side_effect=RuntimeError('报单失败')):
            with self.assertRaisesMessage(RuntimeError, '报单失败'):
                self.execute(case, proposal)
        wallet.refresh_from_db(); self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((wallet.balance, self.order.status, case.status), (before, 'IN_SERVICE', 'OPEN'))
        self.assertFalse(self.order.providers.filter(released_at__isnull=False).exists())
        self.assertFalse(self.order.refund_records.exists())

    def test_completed_order_with_future_completion_cannot_refund(self):
        case, proposal = self.accepted()
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now() + timedelta(hours=1)
        self.order.save(update_fields=['status', 'completed_at'])
        boss = get_wallet(user=self.boss, account=self.boss_account); before = boss.balance
        with self.assertRaisesMessage(ValidationError, '协商终止时间'):
            self.execute(case, proposal)
        boss.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, case.status), (before, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())

    def test_invalid_deposit_percent_rolls_back_refund(self):
        case, proposal = self.accepted()
        self.order.service_deposit_percent = 101; self.order.save(update_fields=['service_deposit_percent'])
        boss = get_wallet(user=self.boss, account=self.boss_account); before = boss.balance
        with self.assertRaisesMessage(ValidationError, '结算规则待核对'):
            self.execute(case, proposal)
        boss.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, case.status), (before, 'OPEN'))
        self.assertFalse(self.order.refund_records.exists())


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NegotiationTerminationActualOrderTest(APITransactionTestCase):
    from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest as flow
    setUp = flow.setUp
    login = flow.login
    ok = flow.ok

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_actual_refund_deducts_deposit_and_next_paid_order_replenishes_it(self):
        from engagement.confirmation import process_order
        from orders.tests.test_provider_aftersale_status import image
        from wallet.models import RefundDepositDeficit, RefundDepositRecoveryPermit
        profile = self.provider.escort_profile
        profile.deposit_mode = 'CASH'; profile.deposit_paid = 0
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund', 'escort:deposit_topup']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
            'amount':40000, 'reason':'隔离测试线下押金', 'reference':'TEST-NEG-DEPOSIT',
            'client_request_id':'negotiation-deposit-topup'}, format='multipart'))
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'deposit-gap-original'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'协商退部分款',
            'client_request_id':'deposit-gap-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'deposit-gap-proposal', 'expected_version':0, 'refund_units':9000,
            'provider_terms':[{'account_id':self.accounts[self.provider.pk].pk, 'retained_units':0}],
            'explanation':'承担退款并终止服务'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id':proposal['id'], 'client_request_id':'deposit-gap-refund'}, format='json'))
        profile.refresh_from_db()
        deficit = RefundDepositDeficit.objects.get(refund__order_id=order_id)
        self.assertEqual((profile.deposit_paid, deficit.remaining), (39000, 1000))
        self.login(self.boss)
        order2 = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'deposit-gap-recovery'}, format='json'))['id']
        permit = RefundDepositRecoveryPermit.objects.get(order_id=order2)
        blocked = self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'deposit-gap-blocked'}, format='json')
        self.assertNotEqual(blocked.data['code'], 0)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order2}/start/', {'entry_image':image()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order2}/complete/', {'completion_image':image()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order2, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=71)):
            self.assertFalse(process_order(order2))
        deficit.refresh_from_db(); profile.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((deficit.remaining, profile.deposit_paid), (1000, 39000))
        self.assertIsNone(permit.closed_at)
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order2))
            self.assertFalse(process_order(order2))  # Already confirmed; do not post again.
        deficit.refresh_from_db(); profile.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((deficit.remaining, profile.deposit_paid), (0, 40000))
        self.assertIsNotNone(permit.closed_at)
        self.assertEqual(get_wallet(user=self.provider, account=self.accounts[self.provider.pk]).balance, 7000)
        from console.roi_period import ordinary_negotiation_cost
        now = timezone.now()
        costs = ordinary_negotiation_cost(Order.objects.get(pk=order_id), now - timedelta(days=1), now + timedelta(days=1))
        self.assertIsNotNone(costs)
        self.assertEqual(costs['provider'], -1000)

    def test_real_order_refund_terminates_service_at_agreement_time(self):
        from orders.models import NegotiationProposal
        from orders.tests.test_provider_aftersale_status import image
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'termination-real-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        get_wallet(user=self.provider, account=self.accounts[self.provider.pk])
        wallet = get_wallet(user=self.boss, account=self.accounts[self.boss.pk]); before = wallet.balance
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'order:refund', 'dashboard:view']; role.save(update_fields=['permissions'])
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'联系协商',
            'client_request_id':'termination-real-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'termination-real-proposal', 'expected_version':0, 'refund_units':5000,
            'provider_terms':[{'account_id':self.accounts[self.provider.pk].pk, 'retained_units':3000}],
            'explanation':'退款并保留已履约收益'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        url = f"/api/admin/aftersales/{case['id']}/negotiation-execution/"
        payload = {'proposal_id':proposal['id'], 'client_request_id':'termination-real-refund'}
        self.login(self.provider)
        self.assertEqual(self.client.post(url, payload, format='json').status_code, 403)
        self.login(self.staff)
        with override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
            self.assertEqual(self.client.post(url, payload, format='json').status_code, 404)
        self.assertEqual(self.ok(self.client.get(url))['proposal']['status'], 'ACCEPTED')
        self.assertEqual(self.ok(self.client.get(url, payload))['status'], 'NOT_FOUND')
        data = self.ok(self.client.post(url, payload, format='json'))
        self.assertEqual((data['status'], data['refund_units']), ('COMPLETED', 5000))
        self.assertEqual(self.ok(self.client.post(url, payload, format='json'))['execution_id'], data['execution_id'])
        self.assertEqual(self.ok(self.client.get(url, payload))['execution_id'], data['execution_id'])
        from console.models import AdminAuditLog
        self.assertEqual(AdminAuditLog.objects.filter(path=url, operator_account=self.accounts[self.staff.pk]).count(), 1)
        with override_settings(NEGOTIATION_REFUND_EXECUTION_ENABLED=False, NEGOTIATION_PROPOSAL_ENABLED=False):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, payload, format='json').status_code, 404)
            self.assertEqual(self.ok(self.client.get(url, payload))['execution_id'], data['execution_id'])
        self.assertEqual(self.client.get(url, {**payload, 'client_request_id':'different'}).status_code, 400)
        result = NegotiationExecution.objects.get(pk=data['execution_id'])
        accepted = NegotiationProposal.objects.get(pk=proposal['id']).accepted_at
        order = Order.objects.get(pk=order_id); wallet.refresh_from_db()
        self.assertEqual((order.status, order.completed_at, wallet.balance), ('COMPLETED', accepted, before + 5000))
        self.assertTrue(result.completed_by_negotiation)
        self.assertEqual(ProviderReport.objects.get(order=order, provider=self.provider).status, 'DRAFT')
        self.assertFalse(order.settled_at)
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.login(self.provider)
        draft = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual(draft['expected_income'], 3000)
        refund_tx = result.customer_transaction
        refund_tx.amount -= 1; refund_tx.save(update_fields=['amount'])
        denied = self.client.post(f'/api/wallet/reports/{report.pk}/images/',
            {'kind':'COMPLETION', 'image':image()}, format='multipart')
        self.assertEqual(denied.data['code'], 400)
        refund_tx.amount += 1; refund_tx.save(update_fields=['amount'])
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind':kind, 'image':image()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.staff)
        from orders.models import OrderFinancialLock
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        OrderFinancialLock.objects.filter(pk=1).delete()
        with CaptureQueriesContext(connection) as queries:
            dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual(dashboard['pending_report_order_blocked_total'], 1)
        self.assertFalse([query['sql'] for query in queries.captured_queries
                          if query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))])
        self.assertFalse(OrderFinancialLock.objects.filter(pk=1).exists())
        OrderFinancialLock.objects.create(pk=1)
        refund_tx.amount -= 1; refund_tx.save(update_fields=['amount'])
        detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertIn('待核对', detail['order_approval_block_reason'])
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (1, 1))
        blocked = self.ok(self.client.get('/api/admin/reports/', {'order_blocked':'1'}))
        self.assertEqual([row['id'] for row in blocked['list']], [report.pk])
        self.assertEqual(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json').data['code'], 400)
        refund_tx.amount += 1; refund_tx.save(update_fields=['amount'])
        detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual(detail['order_approval_block_reason'], '')
        dashboard = self.ok(self.client.get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['pending_report_total'], dashboard['pending_report_order_blocked_total']), (1, 0))
        blocked = self.ok(self.client.get('/api/admin/reports/', {'order_blocked':'1'}))
        self.assertEqual(blocked['total'], 0)
        self.assertEqual(detail['expected_income'], 3000)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        self.assertEqual((report.status, report.payout_amount), ('APPROVED', 8000))
        detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual(detail['payout_amount'], 3000)
        self.login(self.provider)
        detail = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((detail['expected_income'], detail['payout_amount']), (3000, 3000))
        from engagement.confirmation import process_order
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        order.refresh_from_db()
        self.assertTrue(order.settled_at)
        self.assertEqual(get_wallet(user=self.provider, account=self.accounts[self.provider.pk]).balance, 3000)


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NegotiationTerminationActualTeamTest(APITransactionTestCase):
    from orders.tests import test_release_flow as flow
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_scoped_case_stays_open_while_other_provider_settles_then_refund_completes(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.confirmation import process_order
        from orders.models import OrderProvider
        from orders.tests.factories import make_provider
        from orders.tests.test_release_flow import screenshot
        second = make_provider(display_name='已结算陪玩', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id':self.card.pk, 'client_request_id':'prior-paid-team'}, format='json'))['id']
        for player in (self.provider, second):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        affected = self.accounts[self.provider.pk]
        income = OrderProvider.objects.get(order_id=order_id, provider_account=affected).provider_income
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[affected.pk], 'reason':'仅一位陪玩协商',
            'client_request_id':'prior-paid-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'prior-paid-proposal', 'expected_version':0, 'refund_units':1000,
            'provider_terms':[{'account_id':affected.pk, 'retained_units':income - 1000}],
            'explanation':'双方同意部分退款'}, format='json'))
        for actor in (self.boss, self.provider):
            self.login(actor)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        for player in (self.provider, second):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image':screenshot()}, format='multipart'))
        reports = []
        for player in (self.provider, second):
            report = ProviderReport.objects.get(order_id=order_id, provider=player)
            self.login(player)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            report.refresh_from_db(); reports.append(report)
        paid_at = max(report.submitted_at for report in reports) + timedelta(hours=73)
        with patch('django.utils.timezone.now', return_value=paid_at):
            self.assertFalse(process_order(order_id))
            unaffected = OrderProvider.objects.get(order_id=order_id, provider=second)
            self.assertIsNotNone(unaffected.settled_at)
            self.assertIsNone(OrderProvider.objects.get(order_id=order_id, provider=self.provider).settled_at)
            self.login(self.admin)
            payload = {'proposal_id':proposal['id'], 'client_request_id':'prior-paid-refund'}
            url = f"/api/admin/aftersales/{case['id']}/negotiation-execution/"
            first = self.ok(self.client.post(url, payload, format='json'))
            self.assertEqual(self.ok(self.client.post(url, payload, format='json'))['execution_id'], first['execution_id'])
            self.assertTrue(process_order(order_id))
        self.assertEqual(get_wallet(user=self.provider, account=affected).balance, income - 1000)
        other_account = self.accounts[second.pk]
        self.assertEqual(get_wallet(user=second, account=other_account).balance, unaffected.provider_income)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='INCOME', wallet__account=other_account).count(), 1)
        self.assertEqual(Transaction.objects.filter(order_id=order_id, tx_type='REFUND', amount__gt=0).count(), 1)

    def test_actual_team_remaining_member_completes_after_refund(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import OrderProvider
        from orders.tests.factories import make_provider
        from orders.tests.test_release_flow import screenshot
        second = make_provider(display_name='继续履约陪玩', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2; self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_ids':[self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id':self.card.pk, 'client_request_id':'negotiation-real-team'}, format='json'))['id']
        for player in (self.provider, second):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
            get_wallet(user=player, account=self.accounts[player.pk])
        self.login(second)
        stats = self.ok(self.client.get('/api/users/provider-stats/'))
        self.assertEqual((stats['today_orders'], stats['serving']), (1, 1))
        affected = self.accounts[self.provider.pk]
        income = OrderProvider.objects.get(order_id=order_id, provider_account=affected).provider_income
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[affected.pk], 'reason':'协商结束其中一位',
            'client_request_id':'negotiation-team-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'negotiation-team-proposal', 'expected_version':0, 'refund_units':1000,
            'provider_terms':[{'account_id':affected.pk, 'retained_units':income - 1000}],
            'explanation':'双方同意部分退款'}, format='json'))
        for actor in (self.boss, self.provider):
            self.login(actor)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id':proposal['id'], 'client_request_id':'negotiation-team-refund'}, format='json'))
        from console.roi_period import ordinary_negotiation_cost
        now = timezone.now()
        cost_window = (now - timedelta(days=1), now + timedelta(days=1))
        initial_cost = ordinary_negotiation_cost(Order.objects.get(pk=order_id), *cost_window)
        self.assertIsNotNone(initial_cost)
        self.assertEqual(initial_cost['provider'], sum(OrderProvider.objects.filter(order_id=order_id)
                         .values_list('provider_income', flat=True)) - 1000)
        from orders.grab_original_facts import original_amount_facts
        from orders.grab_ranking import SubmittedCandidate, SubmittedMember, build_rankable_candidates, decide as rank_decide
        members = [affected.pk, self.accounts[second.pk].pk]
        facts = original_amount_facts(members, as_of=timezone.now())
        segments = {row.provider_account_id: row for row in OrderParticipationSegment.objects.filter(
            attribution__order_id=order_id, started_at__isnull=False)}
        self.assertEqual((facts[members[0]].amount, facts[members[1]].amount),
                         (segments[members[0]].original_share - 1000, segments[members[1]].original_share))
        candidates = [SubmittedCandidate(1, (SubmittedMember(members[0], 'GOLD'),), timezone.now()),
                      SubmittedCandidate(2, (SubmittedMember(members[1], 'GOLD'),), timezone.now())]
        self.assertEqual(rank_decide(build_rankable_candidates(candidates, as_of=timezone.now())).winner_id, 2)
        self.login(self.provider)
        stats = self.ok(self.client.get('/api/users/provider-stats/'))
        self.assertEqual((stats['today_orders'], stats['serving']), (1, 0))
        report_a = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report_a.pk}/images/',
            {'kind':'COMPLETION', 'image':screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report_a.pk}/images/',
            {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
        self.assertEqual(self.client.post(f'/api/wallet/reports/{report_a.pk}/submit/').data['code'], 400)
        self.login(second)
        detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(detail['team_progress']['total_count'], 1)
        self.assertIn('COMPLETE', detail['allowed_actions'])
        self.assertIsNone(detail['refund_summary'])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image':screenshot()}, format='multipart'))
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, 'COMPLETED')
        for player in (self.provider, second):
            self.login(player)
            self.assertEqual(self.ok(self.client.get('/api/users/provider-stats/'))['total_completed'], 1)
        reports = []
        for player in (self.provider, second):
            report = ProviderReport.objects.get(order=order, provider=player)
            self.login(player)
            if player == second:
                self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                    {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/'))
            self.login(self.admin)
            detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
            self.assertEqual(detail['order_approval_block_reason'], '')
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
            report.refresh_from_db(); reports.append(report)
        from engagement.confirmation import process_order
        reports[1].submitted_at = reports[0].submitted_at + timedelta(hours=3)
        reports[1].save(update_fields=['submitted_at'])
        with patch('django.utils.timezone.now', return_value=reports[0].submitted_at + timedelta(hours=74)):
            self.assertFalse(process_order(order_id))
        from stores.db import transaction
        from orders.negotiation_settlement import _verify_settled_assignment
        with transaction.atomic():
            check_order = Order.objects.get(pk=order_id)
            check_execution = NegotiationExecution.objects.get(refund__order_id=order_id)
            check_share = check_execution.shares.get(account=affected)
            _verify_settled_assignment(check_order, check_execution, check_share.assignment, check_share,
                                       ProviderReport.objects.get(pk=reports[0].pk))
        self.login(self.provider)
        from orders.negotiation_settlement import report_settlement_snapshot
        current = ProviderReport.objects.get(pk=reports[0].pk)
        self.assertEqual(report_settlement_snapshot(current)[0], 'SETTLED',
            (check_order.service_clock_version, check_order.service_aftersale_hours, check_order.service_freeze_hours))
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            first = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == reports[0].pk)
        self.assertFalse([query['sql'] for query in queries.captured_queries
                          if query['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE '))])
        self.assertEqual(first['settlement_status'], 'SETTLED')
        self.assertTrue(first['negotiated_settlement'])
        self.assertEqual(first['refund_summary']['amount'], 1000)
        self.assertEqual(first['refund_summary']['negotiated_provider_retained'], income - 1000)
        self.assertIsNotNone(first['settled_at'])
        self.login(second)
        second_report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == reports[1].pk)
        self.assertEqual(second_report['settlement_status'], 'WAITING')
        self.assertIsNone(second_report['settled_at'])
        self.assertIsNone(second_report['refund_summary'])
        self.login(self.boss)
        boss_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(boss_order['refund_summary']['amount'], 1000)
        self.login(self.admin)
        first_admin = self.ok(self.client.get(f'/api/admin/reports/{reports[0].pk}/'))
        self.assertEqual(first_admin['settlement_status'], 'SETTLED')
        self.assertTrue(first_admin['negotiated_settlement'])
        self.assertIsNotNone(first_admin['settled_at'])
        receipt = NegotiationExecution.objects.get(refund__order_id=order_id)
        first_tx = receipt.shares.get(account=affected).income_transaction
        first_tx.amount -= 1; first_tx.save(update_fields=['amount'])
        first_admin = self.ok(self.client.get(f'/api/admin/reports/{reports[0].pk}/'))
        self.assertEqual(first_admin['settlement_status'], 'REVIEW')
        self.assertIsNone(first_admin['settled_at'])
        first_tx.amount += 1; first_tx.save(update_fields=['amount'])
        with patch('django.utils.timezone.now', return_value=reports[1].submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
        order.refresh_from_db()
        self.assertTrue(order.settled_at)
        self.assertEqual(ordinary_negotiation_cost(order, *cost_window), initial_cost)
        self.assertEqual(get_wallet(user=self.provider, account=affected).balance, income - 1000)
        other_income = OrderProvider.objects.get(order=order, provider=second).provider_income
        self.assertEqual(get_wallet(user=second, account=self.accounts[second.pk]).balance, other_income)
        self.login(second)
        paid = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == reports[1].pk)
        self.assertEqual(paid['settlement_status'], 'SETTLED')
        self.assertIsNotNone(paid['settled_at'])
        self.assertIsNone(paid['refund_summary'])


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NegotiationTerminationConcurrencyTest(APITransactionTestCase):
    """Exercise real database row locks; SQLite cannot establish this property."""
    account = NegotiationTerminationTests.account
    client_for = NegotiationTerminationTests.client_for
    case = NegotiationTerminationTests.case
    propose = NegotiationTerminationTests.propose
    accepted = NegotiationTerminationTests.accepted
    setUp = NegotiationTerminationTests.setUp

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_retry_posts_one_refund_and_one_audit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from console.models import AdminAuditLog

        case, proposal = self.accepted()
        boss = get_wallet(user=self.boss, account=self.boss_account)
        balance_before = boss.balance
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        payload = {'proposal_id':proposal.pk, 'client_request_id':'simultaneous-refund'}
        barrier = Barrier(2, timeout=20)

        def submit():
            close_old_connections()
            try:
                client = self.client_for(self.operator_account)
                barrier.wait()
                response = client.post(url, payload, format='json')
                return response.status_code, response.data
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=40) for future in (executor.submit(submit), executor.submit(submit))]
        self.assertEqual([item[0] for item in results], [200, 200], results)
        self.assertEqual(results[0][1]['data']['execution_id'], results[1][1]['data']['execution_id'])
        boss.refresh_from_db()
        self.assertEqual(boss.balance, balance_before + 1000)
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(path=url, resource='aftersales', object_id=str(case.pk)).count(), 1)
