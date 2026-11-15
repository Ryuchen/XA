"""Real prior negotiations feed a locked, read-only follow-up calculation."""
from datetime import timedelta
from django.test import override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from orders.negotiation_followup import preview_followup
from orders.tests import test_negotiation_termination as fixtures
from wallet.models import Transaction
from wallet.services import get_wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                   NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class FollowupPreviewTests(APITestCase):
    account = fixtures.NegotiationTerminationTests.account
    client_for = fixtures.NegotiationTerminationTests.client_for
    case = fixtures.NegotiationTerminationTests.case
    propose = fixtures.NegotiationTerminationTests.propose
    accepted = fixtures.NegotiationTerminationTests.accepted
    execute = fixtures.NegotiationTerminationTests.execute
    setUp = fixtures.NegotiationTerminationTests.setUp

    def followup_case(self, first_refund):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=first_refund)
        receipt = self.execute(case, proposal)
        response = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'reason':'老板请求协商剩余陪玩的服务',
            'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'next-followup'}, format='json')
        self.assertEqual(response.data.get('code'), 0, response.data)
        self.followup_open_message = response.data['msg']
        case.refresh_from_db()
        return case, receipt

    def test_prior_partial_refund_sets_remaining_and_provider_bears_new_amount(self):
        case, receipt = self.followup_case(1000)
        boss = get_wallet(user=self.boss, account=self.boss_account)
        player = get_wallet(user=self.b, account=self.b_account)
        before = (boss.balance, player.balance, Transaction.objects.count(), self.order.refund_records.count())
        result = preview_followup(case.pk, actor=self.staff, refund_units=5000)
        self.assertEqual(result, {'prior_execution_id':receipt.pk, 'remaining_refundable_units':9000,
            'refund_units':5000, 'rounding_to_store_units':0, 'provider_terms':[{
                'account_id':self.b_account.pk, 'original_income_units':4000, 'burden_units':5000,
                'retained_units':0, 'additional_units':1000}], 'projected_ledger':{
                'previous_refund_units':1000, 'previous_recovered_units':0, 'previous_retained_units':3000,
                'current_retained_units':0, 'current_additional_units':1000, 'store_income_units':2000,
                'inviter_income_units':0, 'rounding_units':0, 'customer_net_units':4000}})
        self.assertEqual(preview_followup(case.pk, actor=self.staff, refund_units=5000), result)
        boss.refresh_from_db(); player.refresh_from_db()
        self.assertEqual((boss.balance, player.balance, Transaction.objects.count(), self.order.refund_records.count()), before)

    def test_confirmed_terminated_provider_cannot_be_reopened_while_teammate_serves(self):
        from stores.db import transaction
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        for first_refund in (0, 1000):
            with self.subTest(first_refund=first_refund), transaction.atomic():
                case, proposal = self.accepted(amount=first_refund)
                self.execute(case, proposal)
                client = self.client_for(self.staff)
                payload = {'order_id':self.order.pk, 'reason':'再次请求售后',
                           'affected_provider_account_ids':[self.a_account.pk], 'client_request_id':'reopen-a'}
                rejected = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
                self.assertEqual(rejected.status_code, 400, rejected.data)
                case.refresh_from_db()
                self.assertNotEqual(case.status, 'OPEN')
                payload.update(affected_provider_account_ids=[self.b_account.pk], client_request_id='open-b')
                allowed = client.post('/api/admin/aftersales/open-negotiation/', payload, format='json')
                self.assertEqual(allowed.data.get('code'), 0, allowed.data)
                self.assertEqual(allowed.data['data']['affected_provider_account_ids'], [self.b_account.pk])
                transaction.set_rollback(True)

    def test_completed_order_only_allows_unaffected_provider_to_open_aftersale(self):
        from stores.db import transaction
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        for first_refund in (0, 1000):
            with self.subTest(first_refund=first_refund), transaction.atomic():
                case, proposal = self.accepted(amount=first_refund)
                receipt = self.execute(case, proposal)
                completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
                    {'completion_image':image()}, format='multipart')
                self.assertEqual(completed.data.get('code'), 0, completed.data)
                customer = self.client_for(self.boss_account)
                url = f'/api/orders/orders/{self.order.pk}/aftersale/'
                details = customer.get(url)
                self.assertEqual(details.data['data']['confirmed_termination_provider_ids'], [self.a_account.pk])
                for scope in (None, [self.a_account.pk], [self.a_account.pk, self.b_account.pk]):
                    rejected = customer.post(url, {'reason':'申请重新处理', 'affected_provider_account_ids':scope,
                                                    'client_request_id':'cannot-reopen-a'}, format='json')
                    self.assertEqual(rejected.status_code, 400, rejected.data)
                case.refresh_from_db(); self.assertNotEqual(case.status, 'OPEN')
                payload = {'reason':'处理 B 的服务', 'affected_provider_account_ids':[self.b_account.pk],
                           'client_request_id':'post-completion-b'}
                accepted = customer.post(url, payload, format='json')
                self.assertEqual(accepted.data.get('code'), 0, accepted.data)
                self.assertEqual(accepted.data['data']['affected_provider_account_ids'], [self.b_account.pk])
                self.assertEqual(customer.get(url).data['data']['confirmed_termination_provider_ids'], [self.a_account.pk])
                self.assertEqual(customer.post(url, payload, format='json').data.get('code'), 0)
                self.order.refresh_from_db()
                submitted = self.order.completed_at
                settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, submitted)
                settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, submitted)
                due = submitted + timedelta(hours=73)
                self.assertEqual(settlement_fixture.NegotiationSettlementTest.settle(self, receipt, due), [self.a.pk])
                self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000 - first_refund)
                self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
                transaction.set_rollback(True)

    def test_tampered_prior_negotiation_does_not_open_or_guess_aftersale_scope(self):
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=1000)
        self.execute(case, proposal)
        completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(completed.data.get('code'), 0, completed.data)
        case.refresh_from_db(); case.history[-1]['resolution'] = '已篡改'
        case.save(update_fields=['history'])
        customer = self.client_for(self.boss_account)
        url = f'/api/orders/orders/{self.order.pk}/aftersale/'
        self.assertEqual(customer.get(url).status_code, 400)
        result = customer.post(url, {'reason':'处理 B 的服务', 'affected_provider_account_ids':[self.b_account.pk],
                                     'client_request_id':'invalid-history'}, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        case.refresh_from_db(); self.assertNotEqual(case.status, 'OPEN')

    def test_missing_negotiation_marker_does_not_allow_scope_bypass(self):
        from stores.db import transaction
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        for first_refund in (0, 1000):
            with self.subTest(first_refund=first_refund), transaction.atomic():
                case, proposal = self.accepted(amount=first_refund)
                receipt = self.execute(case, proposal)
                completed = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
                    {'completion_image':image()}, format='multipart')
                self.assertEqual(completed.data.get('code'), 0, completed.data)
                if receipt.refund_id:
                    refund = receipt.refund
                    del refund.benefit_effects['negotiation_execution_version']
                    refund.save(update_fields=['benefit_effects'])
                else:
                    self.order.refresh_from_db()
                    del self.order.funding_terms['zero_negotiation_v1']
                    self.order.save(update_fields=['funding_terms'])
                customer = self.client_for(self.boss_account)
                url = f'/api/orders/orders/{self.order.pk}/aftersale/'
                self.assertEqual(customer.get(url).status_code, 400)
                result = customer.post(url, {'reason':'再次申请处理', 'affected_provider_account_ids':[self.a_account.pk],
                                             'client_request_id':'marker-missing'}, format='json')
                self.assertEqual(result.status_code, 400, result.data)
                case.refresh_from_db(); self.assertNotEqual(case.status, 'OPEN')
                transaction.set_rollback(True)

    def test_ordinary_aftersale_read_works_before_negotiation_schema_upgrade(self):
        from unittest.mock import patch
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from orders.models import NegotiationExecution
        self.case()
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        customer = self.client_for(self.boss_account)
        url = f'/api/orders/orders/{self.order.pk}/aftersale/'
        tables = connection.introspection.table_names
        with patch.object(connection.introspection, 'table_names',
                          side_effect=lambda cursor=None, **kwargs: [name for name in tables(cursor, **kwargs)
                              if name != NegotiationExecution._meta.db_table]):
            self.assertEqual(customer.get(url).data['data']['confirmed_termination_provider_ids'], [])
        describe = connection.introspection.get_table_description
        def without_zero_columns(cursor, table):
            return [column for column in describe(cursor, table)
                    if column.name not in {'zero_order_id', 'zero_request_id', 'zero_allocation'}]
        with patch.object(connection.introspection, 'get_table_description', side_effect=without_zero_columns), \
                CaptureQueriesContext(connection) as captured:
            self.assertEqual(customer.get(url).data['data']['confirmed_termination_provider_ids'], [])
        receipt_queries = [row['sql'].lower() for row in captured.captured_queries
                           if 'from "orders_negotiationexecution"' in row['sql'].lower()]
        self.assertTrue(all('zero_order_id' not in sql for sql in receipt_queries))

    def test_prior_receipt_on_old_schema_requires_review_without_new_column_queries(self):
        from unittest.mock import patch
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=1000)
        self.execute(case, proposal)
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data.get('code'), 0, result.data)
        describe = connection.introspection.get_table_description
        customer = self.client_for(self.boss_account)
        url = f'/api/orders/orders/{self.order.pk}/aftersale/'
        for missing in ({'zero_order_id', 'zero_request_id', 'zero_allocation'}, {'zero_request_id'}):
            def incomplete_columns(cursor, table):
                return [column for column in describe(cursor, table) if column.name not in missing]
            with self.subTest(missing=missing), \
                    patch.object(connection.introspection, 'get_table_description', side_effect=incomplete_columns), \
                    CaptureQueriesContext(connection) as captured:
                self.assertEqual(customer.get(url).status_code, 400)
                self.assertEqual(customer.post(url, {'reason':'处理 B 的服务',
                    'affected_provider_account_ids':[self.b_account.pk], 'client_request_id':'old-schema'}, format='json').status_code, 400)
            receipt_queries = [row['sql'].lower() for row in captured.captured_queries
                               if 'from "orders_negotiationexecution"' in row['sql'].lower()]
            self.assertTrue(all(not any(column in sql for column in missing) for sql in receipt_queries))
        case.refresh_from_db(); self.assertNotEqual(case.status, 'OPEN')

    def test_zero_termination_preserves_full_refundable_amount(self):
        case, receipt = self.followup_case(0)
        result = preview_followup(case.pk, actor=self.staff, refund_units=3000)
        self.assertEqual((result['prior_execution_id'], result['remaining_refundable_units']), (receipt.pk, 10000))
        self.assertEqual(result['provider_terms'][0]['retained_units'], 1000)
        self.assertFalse(self.order.refund_records.exists())

    def test_previous_wallet_and_deposit_deductions_are_in_the_projection(self):
        from orders.tests.test_negotiation_execution import NegotiationExecutionPostingTests
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        NegotiationExecutionPostingTests.cash_deposit(self, 500)
        case, receipt = self.followup_case(5000)
        self.assertEqual(receipt.refund.recovered_amount, 1000)
        result = preview_followup(case.pk, actor=self.staff, refund_units=3000)
        ledger = result['projected_ledger']
        self.assertEqual((ledger['previous_refund_units'], ledger['previous_recovered_units'],
                          ledger['previous_retained_units'], ledger['current_retained_units'],
                          ledger['customer_net_units']), (5000, 1000, 0, 1000, 2000))
        self.assertEqual(ledger['customer_net_units'] + ledger['previous_recovered_units']
                         + ledger['current_additional_units'],
                         ledger['previous_retained_units'] + ledger['current_retained_units']
                         + ledger['store_income_units'] + ledger['inviter_income_units'] + ledger['rounding_units'])

    def test_prior_full_refund_leaves_only_zero_refund_followup(self):
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 6000; wallet.save(update_fields=['balance'])
        case, _ = self.followup_case(10000)
        result = preview_followup(case.pk, actor=self.staff, refund_units=0)
        self.assertEqual(result['remaining_refundable_units'], 0)
        self.assertEqual(result['provider_terms'][0]['retained_units'], 4000)
        self.assertEqual(result['projected_ledger']['previous_recovered_units'], 6000)
        with self.assertRaisesMessage(ValidationError, '剩余可退'):
            preview_followup(case.pk, actor=self.staff, refund_units=10)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_staff_can_view_only_the_followup_estimate_through_real_api(self):
        case, _ = self.followup_case(1000)
        self.assertIn('暂未接通', self.followup_open_message)
        client = self.client_for(self.staff)
        proposal_url = f'/api/admin/aftersales/{case.pk}/negotiation-proposal/'
        preview_url = f'/api/admin/aftersales/{case.pk}/negotiation-preview/'
        current = client.get(proposal_url)
        self.assertEqual(current.data['code'], 0, current.data)
        self.assertTrue(current.data['data']['followup_preview_only'])
        self.assertFalse(current.data['data']['execution_available'])
        response = client.get(preview_url, {'refund_units':5000})
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertTrue(response.data['data']['read_only'])
        self.assertEqual(response.data['data']['remaining_refundable_units'], 9000)
        self.assertEqual(response.data['data']['provider_terms'][0]['additional_units'], 1000)
        self.assertNotIn('projected_ledger', response.data['data'])
        self.assertNotIn('prior_execution_id', response.data['data'])
        self.assertIn('尚未开放', response.data['msg'])
        rejected = client.post(proposal_url, {'request_id':'not-open', 'expected_version':1,
            'refund_units':5000, 'provider_terms':[{'account_id':self.b_account.pk, 'retained_units':0}],
            'explanation':'尝试提交追加方案'}, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertEqual(self.client_for(self.viewer).get(preview_url, {'refund_units':5000}).status_code, 403)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_zero_followup_estimate_is_also_read_only_through_real_api(self):
        case, _ = self.followup_case(0)
        client = self.client_for(self.staff)
        current = client.get(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/')
        self.assertTrue(current.data['data']['followup_preview_only'])
        response = client.get(f'/api/admin/aftersales/{case.pk}/negotiation-preview/', {'refund_units':3000})
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual((response.data['data']['read_only'], response.data['data']['remaining_refundable_units']),
                         (True, 10000))
        self.assertFalse(self.order.refund_records.exists())

    def test_authorized_staff_can_check_current_players_funding_without_debit(self):
        case, _ = self.followup_case(1000)
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 300; wallet.save(update_fields=['balance'])
        profile = self.b.escort_profile
        profile.deposit_paid = 200; profile.save(update_fields=['deposit_paid'])
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        path = f'/api/admin/aftersales/{case.pk}/negotiation-funding/'
        response = self.client_for(self.operator_account).get(path, {'refund_units':4500})
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['providers'], [{'account_id':self.b_account.pk,
            'pending_income_units':4000, 'wallet_units':300, 'deposit_units':200, 'shortfall_units':0}])
        self.assertTrue(response.data['data']['read_only_followup'])
        self.assertFalse(response.data['data']['executable'])
        self.assertEqual(self.client_for(self.staff).get(path, {'refund_units':4500}).status_code, 403)
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid, self.order.refund_records.count()), (300, 200, 1))

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_followup_proposal_and_both_decisions_are_recorded_without_moving_funds(self):
        case, prior = self.followup_case(1000)
        self.assertIn('提交方案', self.followup_open_message)
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        before = (boss_wallet.balance, Transaction.objects.count(), self.order.refund_records.count())
        client = self.client_for(self.staff)
        response = client.post(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/', {
            'request_id':'followup-proposal', 'expected_version':1, 'refund_units':5000,
            'provider_terms':[{'account_id':self.b_account.pk, 'retained_units':0}],
            'explanation':'双方确认当前陪玩的退款责任'}, format='json')
        self.assertEqual(response.data.get('code'), 0, response.data)
        proposal_id = response.data['data']['id']
        decision_url = f'/api/orders/orders/{self.order.pk}/negotiation-proposal/'
        denied = self.client_for(self.a_account).post(decision_url, {'proposal_id':proposal_id, 'accepted':True}, format='json')
        self.assertEqual(denied.status_code, 404)
        for account in (self.boss_account, self.b_account):
            result = self.client_for(account).post(decision_url, {'proposal_id':proposal_id, 'accepted':True}, format='json')
            self.assertEqual(result.data.get('code'), 0, result.data)
        self.assertEqual(result.data['data']['status'], 'ACCEPTED')
        boss_wallet.refresh_from_db(); prior.refresh_from_db()
        self.assertEqual((boss_wallet.balance, Transaction.objects.count(), self.order.refund_records.count()), before)
        self.assertIsNone(prior.finalized_at)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_zero_followup_proposal_keeps_prior_payment_untouched(self):
        case, _ = self.followup_case(0)
        proposal = self.propose(case, version=1, request_id='followup-zero', scope=[self.b_account.pk], refund=0)
        from orders.negotiation_proposals import decide
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'ACCEPTED')
        self.assertFalse(self.order.refund_records.exists())

    def test_followup_cannot_obtain_consent_when_required_execution_switch_is_off(self):
        case, _ = self.followup_case(1000)
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
            with self.assertRaisesMessage(ValidationError, '新售后方案尚未接通'):
                self.propose(case, version=1, request_id='refund-off', scope=[self.b_account.pk], refund=1000)
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=False):
            with self.assertRaisesMessage(ValidationError, '零退款追加协商尚未开放'):
                self.propose(case, version=1, request_id='zero-off', scope=[self.b_account.pk], refund=0)
        self.assertEqual(case.negotiation_proposals.count(), 1)

    def test_refund_cannot_exceed_remaining_or_use_fractional_store_coin(self):
        case, _ = self.followup_case(1000)
        for amount in (9001, 9010, -10, True):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                preview_followup(case.pk, actor=self.staff, refund_units=amount)
        self.assertEqual(preview_followup(case.pk, actor=self.staff, refund_units=9000)['remaining_refundable_units'], 9000)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_original_payment_or_prior_case_tampering_prevents_calculation(self):
        case, _ = self.followup_case(1000)
        payment = Transaction.objects.get(order=self.order, tx_type='PAY')
        payment.amount += 10; payment.save(update_fields=['amount'])
        with self.assertRaisesMessage(ValidationError, '原付款凭证待核对'):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)
        payment.amount -= 10; payment.save(update_fields=['amount'])
        case.refresh_from_db(); case.history[-2]['resolution'] = '已被修改'; case.save(update_fields=['history'])
        with self.assertRaises(ValidationError):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)
        self.assertEqual(self.order.refund_records.count(), 1)

    def test_released_provider_wrong_actor_and_case_time_are_rejected(self):
        case, _ = self.followup_case(1000)
        with self.assertRaises(ValidationError):
            preview_followup(case.pk, actor=self.viewer, refund_units=1000)
        case.affected_provider_account_ids = [self.a_account.pk]
        case.save(update_fields=['affected_provider_account_ids'])
        with self.assertRaises(ValidationError):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)
        case.affected_provider_account_ids = [self.b_account.pk]; case.created_at = timezone.now() + timedelta(days=1)
        case.save(update_fields=['affected_provider_account_ids', 'created_at'])
        with self.assertRaises(ValidationError):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)

    def test_unrelated_refund_or_income_requires_manual_reconciliation(self):
        from orders.models import OrderRefund
        case, _ = self.followup_case(1000)
        extra = OrderRefund.objects.create(order=self.order, amount=10, reason='异常记录')
        with self.assertRaises(ValidationError):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)
        extra.delete()
        wallet = get_wallet(user=self.b, account=self.b_account)
        Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=10,
                                   balance_before=wallet.balance, balance_after=wallet.balance + 10)
        with self.assertRaisesMessage(ValidationError, '已有收益流水'):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)

    def test_missing_financial_mutex_is_not_created_by_read_only_preview(self):
        from orders.models import OrderFinancialLock
        case, _ = self.followup_case(1000)
        OrderFinancialLock.objects.all().delete()
        with self.assertRaisesMessage(ValidationError, '资金互斥记录待核对'):
            preview_followup(case.pk, actor=self.staff, refund_units=1000)
        self.assertFalse(OrderFinancialLock.objects.exists())

    def accepted_followup(self, first_refund=1000, second_refund=1000):
        from orders.negotiation_proposals import decide
        case, previous = self.followup_case(first_refund)
        proposal = self.propose(case, version=1, request_id='followup-accounting-proposal',
                                scope=[self.b_account.pk], refund=second_refund)
        for actor in (self.boss_account, self.b_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        return case, previous, proposal

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_second_refund_and_link_are_idempotent(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        case, previous, proposal = self.accepted_followup()
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = boss.balance
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='followup-post')
            retry = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                    legacy_operator=self.operator, request_id='followup-post')
            self.assertEqual(retry.pk, link.pk)
            old_share, new_share = verify_followup_accounting(link)
            self.assertEqual((link.previous_id, old_share.retained_income, new_share.retained_income),
                             (previous.pk, 3000, 3000))
        boss.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((boss.balance, self.order.payment_status, self.order.refund_records.count()),
                         (before + 1000, 'PARTIALLY_REFUNDED', 2))

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_link_rejects_changed_payment_status_or_ledger(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        case, _, proposal = self.accepted_followup()
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='followup-integrity')
        for change in ('payment', 'status', 'ledger'):
            with self.subTest(change=change), transaction.atomic():
                if change == 'payment':
                    payment = Transaction.objects.get(order=self.order, tx_type='PAY')
                    payment.amount += 10; payment.save(update_fields=['amount'])
                elif change == 'status':
                    type(self.order).objects.filter(pk=self.order.pk).update(payment_status='PAID')
                else:
                    link.projected_ledger['customer_net_units'] += 10
                    link.save(update_fields=['projected_ledger'])
                with self.assertRaises(ValidationError):
                    verify_followup_accounting(link)
                transaction.set_rollback(True)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_zero_followup_has_no_fabricated_money_rows(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        case, _, proposal = self.accepted_followup(first_refund=0, second_refund=0)
        before = Transaction.objects.count()
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='followup-zero-post')
            old_share, new_share = verify_followup_accounting(link)
            self.assertEqual((old_share.retained_income, new_share.retained_income), (4000, 4000))
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(Transaction.objects.count(), before)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_insufficient_funds_does_not_post(self):
        from stores.db import transaction
        from orders.models import NegotiationFollowupLink
        from orders.negotiation_followup_accounting import _apply_followup
        case, _, proposal = self.accepted_followup(second_refund=5000)
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = (boss.balance, Transaction.objects.count(), self.order.refund_records.count())
        with self.assertRaisesMessage(ValidationError, '不足'), transaction.atomic():
            _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                            legacy_operator=self.operator, request_id='followup-insufficient')
        boss.refresh_from_db()
        self.assertEqual((boss.balance, Transaction.objects.count(), self.order.refund_records.count()), before)
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_supports_both_zero_and_paid_prior_receipts(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        for first, second in ((0, 1000), (1000, 0)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                       legacy_operator=self.operator, request_id='mixed-followup')
                prior, current = verify_followup_accounting(link)
                self.assertEqual((prior.retained_income, current.retained_income), (4000-first, 4000-second))
                self.assertEqual(self.order.refund_records.count(), int(first > 0) + int(second > 0))
                transaction.set_rollback(True)

    def setup_b_cash_deposit(self, amount=500):
        from wallet.models import CashDepositTopup, get_platform_wallet
        profile = self.b.escort_profile
        profile.deposit_mode = 'CASH'; profile.deposit_paid = amount
        profile.save(update_fields=['deposit_mode', 'deposit_paid'])
        platform = get_platform_wallet(); before = platform.balance
        platform.balance += amount; platform.save(update_fields=['balance'])
        source = Transaction.objects.create(wallet=platform, tx_type='DEPOSIT_INCOME', amount=amount,
                                            balance_before=before, balance_after=platform.balance)
        CashDepositTopup.objects.create(profile=profile, account=self.b_account, operator=self.operator_account,
            amount=amount, paid_before=0, paid_after=amount, target_snapshot=amount,
            reason='隔离测试收款', reference='b-cash-proof', client_request_id='b-cash-proof', platform_transaction=source)
        return profile

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_debits_wallet_then_cash_deposit(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        from wallet.models import RefundDepositDeficit
        case, _, proposal = self.accepted_followup(second_refund=5000)
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile = self.setup_b_cash_deposit()
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='b-wallet-deposit')
            _, share = verify_followup_accounting(link)
            self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction), (4000, 600, 400))
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid), (0, 100))
        self.assertEqual(RefundDepositDeficit.objects.get(refund=link.execution.refund, account=self.b_account).remaining, 400)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                       ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_second_refund_deposit_gap_is_repaid_by_next_real_order(self):
        from unittest.mock import patch
        from engagement.confirmation import process_order
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.tests.test_provider_aftersale_status import image
        from wallet.models import ProviderReport, RefundDepositDeficit, RefundDepositRecoveryPermit, RefundDepositRepayment
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile = self.setup_b_cash_deposit(amount=40000)
        case, _, proposal = self.accepted_followup(second_refund=5000)
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='b-recovery-refund')
        deficit = RefundDepositDeficit.objects.get(refund=link.execution.refund, account=self.b_account)
        profile.refresh_from_db()
        self.assertEqual((profile.deposit_paid, deficit.remaining), (39600, 400))
        boss_wallet = get_wallet(user=self.boss, account=self.boss_account)
        boss_wallet.balance = 50000; boss_wallet.save(update_fields=['balance'])
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'report:view', 'report:audit', 'order:settle']
        role.save(update_fields=['permissions'])
        customer = self.client_for(self.boss_account)
        payload = {'service_id':self.service.pk, 'provider_id':self.b_account.pk,
                   'client_request_id':'b-recovery-order'}
        created = customer.post('/api/orders/create/', payload, format='json')
        self.assertEqual(created.data.get('code'), 0, created.data)
        order_id = created.data['data']['id']
        permit = RefundDepositRecoveryPermit.objects.get(order_id=order_id)
        blocked = customer.post('/api/orders/create/', {**payload, 'client_request_id':'b-recovery-blocked'}, format='json')
        self.assertNotEqual(blocked.data.get('code'), 0, blocked.data)
        player = self.client_for(self.b_account)
        for action, name in (('start', 'entry_image'), ('complete', 'completion_image')):
            result = player.post(f'/api/orders/orders/{order_id}/{action}/', {name:image()}, format='multipart')
            self.assertEqual(result.data.get('code'), 0, result.data)
        report = ProviderReport.objects.get(order_id=order_id, provider=self.b)
        uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT', 'image':image()}, format='multipart')
        self.assertEqual(uploaded.data.get('code'), 0, uploaded.data)
        submitted = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
        self.assertEqual(submitted.data.get('code'), 0, submitted.data)
        approved = self.client_for(self.operator_account).post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
        self.assertEqual(approved.data.get('code'), 0, approved.data)
        report.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=71)):
            self.assertFalse(process_order(order_id))
        deficit.refresh_from_db(); profile.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((deficit.remaining, profile.deposit_paid, permit.closed_at), (400, 39600, None))
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
            self.assertFalse(process_order(order_id))
        deficit.refresh_from_db(); profile.refresh_from_db(); permit.refresh_from_db()
        self.assertEqual((deficit.remaining, profile.deposit_paid), (0, 40000))
        self.assertIsNotNone(permit.closed_at)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 7600)
        repayment = RefundDepositRepayment.objects.get(deficit=deficit)
        self.assertEqual((repayment.amount, repayment.application.source_transaction.order_id), (400, order_id))

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_failure_rolls_back_wallet_and_refund(self):
        from unittest.mock import patch
        from stores.db import transaction
        from orders.models import NegotiationFollowupLink
        from orders.negotiation_followup_accounting import _apply_followup
        case, _, proposal = self.accepted_followup(second_refund=5000)
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 600; wallet.save(update_fields=['balance'])
        profile = self.setup_b_cash_deposit()
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = (boss.balance, wallet.balance, profile.deposit_paid, Transaction.objects.count())
        with patch('wallet.refund_deposit.charge_refund_deposit', side_effect=RuntimeError('模拟押金失败')):
            with self.assertRaisesMessage(RuntimeError, '模拟押金失败'), transaction.atomic():
                _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='followup-rollback')
        boss.refresh_from_db(); wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((boss.balance, wallet.balance, profile.deposit_paid, Transaction.objects.count()), before)
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_cumulative_full_refund_restores_eligible_coupon(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        from orders.tests.factories import make_coupon, make_user_coupon
        coupon = make_user_coupon(self.boss, make_coupon(), status='USED', account=self.boss_account,
                                  order=self.order, restore_on_refund=True)
        case, _, proposal = self.accepted_followup(first_refund=1000, second_refund=9000)
        coupon.refresh_from_db(); self.assertEqual(coupon.status, 'USED')
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 5000; wallet.save(update_fields=['balance'])
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='followup-full')
            verify_followup_accounting(link)
        coupon.refresh_from_db(); self.order.refresh_from_db(); wallet.refresh_from_db()
        self.assertEqual((coupon.status, coupon.order_id, self.order.payment_status, wallet.balance),
                         ('UNUSED', None, 'REFUNDED', 0))
        self.assertEqual(link.execution.refund.benefit_effects['order_coupon_results'][0]['outcome'], 'RETURNED')

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_link_cannot_cross_orders_and_retry_key_cannot_change(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import _apply_followup, verify_followup_accounting
        from orders.tests.factories import make_order
        case, _, proposal = self.accepted_followup()
        with transaction.atomic():
            link = _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                   legacy_operator=self.operator, request_id='followup-original')
            with self.assertRaises(ValidationError):
                _apply_followup(case.pk, proposal.pk, operator=self.operator_account,
                                legacy_operator=self.operator, request_id='different-key')
        other = make_order(self.boss, service=self.service, provider=self.a)
        with transaction.atomic():
            link.order = other; link.save(update_fields=['order'])
            with self.assertRaisesMessage(ValidationError, '前后凭证不一致'):
                verify_followup_accounting(link)
            transaction.set_rollback(True)
        self.assertEqual(self.order.refund_records.count(), 2)

    def test_followup_internal_post_is_disabled_by_default(self):
        from orders.models import NegotiationFollowupLink
        from orders.negotiation_followup_accounting import _apply_followup
        with self.assertRaisesMessage(ValidationError, '权限或操作凭证无效'):
            _apply_followup(1, 1, operator=self.operator_account,
                            legacy_operator=self.operator, request_id='disabled-followup')
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_terminates_second_provider_and_audits_once(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal, verify_followup_termination
        from console.models import AdminAuditLog
        case, previous, proposal = self.accepted_followup()
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = boss.balance
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='complete-followup')
        retry = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='complete-followup')
        self.assertEqual(retry.pk, link.pk)
        verify_followup_termination(link)
        boss.refresh_from_db(); self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, self.order.status, self.order.payment_status, case.status),
                         (before + 1000, 'COMPLETED', 'PARTIALLY_REFUNDED', 'PARTIALLY_REFUNDED'))
        self.assertEqual(self.order.completed_at, proposal.accepted_at)
        self.assertEqual(self.order.providers.get(provider_account=self.a_account).released_at, previous.accepted_at)
        self.assertEqual(self.order.providers.get(provider_account=self.b_account).released_at, proposal.accepted_at)
        self.assertEqual(self.order.refund_records.count(), 2)
        self.assertEqual(AdminAuditLog.objects.filter(request_body__link_id=link.pk).count(), 1)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_zero_followup_terminates_without_refund(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal, verify_followup_termination
        case, _, proposal = self.accepted_followup(first_refund=0, second_refund=0)
        before = Transaction.objects.count()
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='complete-zero-followup')
        verify_followup_termination(link)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, case.status), ('COMPLETED', 'PAID', 'RESOLVED'))
        self.assertFalse(self.order.refund_records.exists())
        self.assertEqual(Transaction.objects.count(), before)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_disabling_zero_execution_after_consent_blocks_new_post(self):
        from orders.models import NegotiationFollowupLink
        case, _, proposal = self.accepted_followup(first_refund=1000, second_refund=0)
        before = Transaction.objects.count()
        with override_settings(NEGOTIATION_ZERO_EXECUTION_ENABLED=False):
            response = self.client_for(self.operator_account).post(
                f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
                {'proposal_id':proposal.pk, 'client_request_id':'zero-disabled-after-consent'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, case.status), ('IN_SERVICE', 'OPEN'))
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertEqual(Transaction.objects.count(), before)
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_execution_readiness_follows_followup_switch_after_consent(self):
        from orders.models import NegotiationFollowupLink
        case, _, proposal = self.accepted_followup()
        client = self.client_for(self.operator_account)
        url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        self.assertTrue(client.get(url).data['data']['execution_enabled'])
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=False):
            self.assertFalse(client.get(url).data['data']['execution_enabled'])
            denied = client.post(url, {'proposal_id':proposal.pk, 'client_request_id':'followup-disabled'}, format='json')
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_changed_legacy_audit_operator_blocks_followup_verification(self):
        from console.models import AdminAuditLog
        from orders.negotiation_followup_accounting import terminate_followup_internal, verify_followup_termination
        case, _, proposal = self.accepted_followup()
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='audit-operator-check')
        audit = AdminAuditLog.objects.get(request_body__link_id=link.pk)
        audit.operator = self.boss
        audit.save(update_fields=['operator'])
        with self.assertRaisesMessage(ValidationError, '审计凭证'):
            verify_followup_termination(link)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_audit_failure_rolls_back_everything(self):
        from unittest.mock import patch
        from orders.models import NegotiationFollowupLink
        from orders.negotiation_followup_accounting import terminate_followup_internal
        case, _, proposal = self.accepted_followup()
        boss = get_wallet(user=self.boss, account=self.boss_account)
        before = (boss.balance, Transaction.objects.count())
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('模拟审计失败')):
            with self.assertRaisesMessage(RuntimeError, '模拟审计失败'):
                terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='followup-audit-failure')
        boss.refresh_from_db(); self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((boss.balance, Transaction.objects.count()), before)
        self.assertEqual((self.order.status, self.order.payment_status, case.status), ('IN_SERVICE', 'PARTIALLY_REFUNDED', 'OPEN'))
        self.assertEqual(self.order.refund_records.count(), 1)
        self.assertFalse(NegotiationFollowupLink.objects.exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_followup_settlement_entry_uses_linked_receipts_even_after_flag_disabled(self):
        from unittest.mock import patch
        from stores.db import transaction
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.services import settle_order
        from orders.tests import test_negotiation_settlement as settlement_fixture
        for first, second in ((0, 0), (1000, 0), (0, 1000), (1000, 1000)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='settlement-guard')
                self.order.refresh_from_db()
                start = self.order.completed_at
                settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
                settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start)
                with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=False), \
                        patch('django.utils.timezone.now', return_value=start + timedelta(hours=73)):
                    self.assertEqual(set(settle_order(self.order)), {self.a.pk, self.b.pk})
                self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000-first)
                self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000-second)
                transaction.set_rollback(True)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_missing_followup_marker_never_reverts_to_single_receipt_settlement(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import FOLLOWUP_MARKER, terminate_followup_internal
        from orders.services import settle_order
        from orders.settlement import SettlementError
        for first, second in ((0, 0), (1000, 0), (0, 1000), (1000, 1000)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='marker-removal')
                self.order.refresh_from_db()
                del self.order.funding_terms[FOLLOWUP_MARKER]
                self.order.save(update_fields=['funding_terms'])
                with self.assertRaises(SettlementError):
                    settle_order(self.order)
                self.assertFalse(self.order.transactions.filter(tx_type__in=['INCOME', 'SHOP_INCOME']).exists())
                transaction.set_rollback(True)

    def settle_link(self, link, when):
        from stores.db import transaction
        from orders.negotiation_followup_settlement import settle_followup_internal
        with transaction.atomic():
            order = type(self.order).objects.select_for_update().get(pk=self.order.pk)
            return settle_followup_internal(order, link, now=when)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_followup_referral_uses_net_paid_after_both_refunds(self):
        from unittest.mock import patch
        from engagement.models import PointFundingLot
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        from orders.tests.factories import make_user
        inviter_user = make_user()
        inviter = self.account(inviter_user)
        self.order.inviter, self.order.inviter_account = inviter_user, inviter
        self.order.inviter_commission_rate_snapshot = 10
        self.order.inviter_commission, self.order.shop_income = 1000, 1000
        self.order.save(update_fields=['inviter', 'inviter_account', 'inviter_commission_rate_snapshot',
                                       'inviter_commission', 'shop_income'])
        case, _, proposal = self.accepted_followup(first_refund=1000, second_refund=1000)
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='followup-referral')
        self.order.refresh_from_db()
        start = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start)
        with patch('django.utils.timezone.now', return_value=start + timedelta(hours=73)):
            self.assertEqual(set(self.settle_link(link, start + timedelta(hours=73))), {self.a.pk, self.b.pk})
            self.assertEqual(self.settle_link(link, start + timedelta(hours=74)), [])
        link.execution.refresh_from_db()
        self.assertEqual((link.execution.inviter_transaction.amount, link.execution.shop_transaction.amount), (800, 1200))
        self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 800)
        lot = PointFundingLot.objects.get(source_key=f'negotiation-referral:{link.execution_id}')
        self.assertEqual((lot.account_id, lot.kind, lot.remaining), (inviter.pk, 'PREPAID', 800))

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_releases_each_provider_by_own_report_clock(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, _, proposal = self.accepted_followup()
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='settle-followup')
        self.order.refresh_from_db()
        start = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start + timedelta(hours=2))
        with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=False):
            self.assertEqual(self.settle_link(link, start + timedelta(hours=73)), [self.a.pk])
            self.assertEqual(self.settle_link(link, start + timedelta(hours=73)), [])
            self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 3000)
            self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
            self.assertFalse(self.order.transactions.filter(tx_type='SHOP_INCOME').exists())
            self.assertEqual(self.settle_link(link, start + timedelta(hours=75)), [self.b.pk])
            self.assertEqual(self.settle_link(link, start + timedelta(hours=75)), [])
        self.order.refresh_from_db(); link.execution.refresh_from_db()
        self.assertEqual(link.execution.finalized_at, self.order.settled_at)
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 3000)
        self.assertEqual(self.order.transactions.filter(tx_type='SHOP_INCOME').count(), 1)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_tampered_prior_case_blocks_remaining_settlement(self):
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.settlement import SettlementError
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, _, proposal = self.accepted_followup()
        link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                           legacy_operator=self.operator, request_id='settle-tampered')
        self.order.refresh_from_db()
        start = self.order.completed_at
        settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start + timedelta(hours=2))
        self.assertEqual(self.settle_link(link, start + timedelta(hours=73)), [self.a.pk])
        case.refresh_from_db(); case.history[1]['resolution'] = '已篡改'; case.save(update_fields=['history'])
        with self.assertRaisesMessage(SettlementError, '追加协商凭证待核对'):
            self.settle_link(link, start + timedelta(hours=75))
        self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 0)
        self.assertFalse(self.order.transactions.filter(tx_type='SHOP_INCOME').exists())

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_internal_followup_preserves_zero_and_paid_receipt_combinations(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        for first, second in ((0, 0), (1000, 0), (0, 1000), (1000, 1000)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                                  legacy_operator=self.operator, request_id='settle-combination')
                self.order.refresh_from_db(); start = self.order.completed_at
                settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
                settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start)
                self.assertEqual(set(self.settle_link(link, start + timedelta(hours=73))), {self.a.pk, self.b.pk})
                self.assertEqual(get_wallet(user=self.a, account=self.a_account).balance, 4000-first)
                self.assertEqual(get_wallet(user=self.b, account=self.b_account).balance, 4000-second)
                self.assertEqual(self.order.transactions.filter(tx_type='SHOP_INCOME').count(), 1)
                transaction.set_rollback(True)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_both_confirmed_terminations_cannot_reopen_after_order_completion(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import terminate_followup_internal
        for first, second in ((0, 0), (0, 1000), (1000, 0), (1000, 1000)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                            legacy_operator=self.operator, request_id='followup-then-case')
                customer = self.client_for(self.boss_account)
                url = f'/api/orders/orders/{self.order.pk}/aftersale/'
                self.assertEqual(set(customer.get(url).data['data']['confirmed_termination_provider_ids']),
                                 {self.a_account.pk, self.b_account.pk})
                for scope in ([self.a_account.pk], [self.b_account.pk], None):
                    result = customer.post(url, {'reason':'再次申请处理', 'affected_provider_account_ids':scope,
                                                 'client_request_id':'later-scoped-case'}, format='json')
                    self.assertEqual(result.status_code, 400, result.data)
                case.refresh_from_db(); self.assertNotEqual(case.status, 'OPEN')
                transaction.set_rollback(True)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_report_views_show_each_linked_negotiated_share(self):
        from stores.db import transaction
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.negotiation_settlement import report_allowed, report_income, report_settlement_snapshot
        from orders.tests import test_negotiation_settlement as settlement_fixture
        for first, second in ((0, 1000), (1000, 0)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, _, proposal = self.accepted_followup(first_refund=first, second_refund=second)
                link = terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                                  legacy_operator=self.operator, request_id='followup-report')
                self.order.refresh_from_db(); start = self.order.completed_at
                first_report = settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
                second_report = settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start + timedelta(hours=2))
                for report, expected in ((first_report, 4000-first), (second_report, 4000-second)):
                    self.assertTrue(report_allowed(self.order, report))
                    self.assertEqual(report_income(report), (True, expected))
                    self.assertEqual(report_settlement_snapshot(report), ('WAITING', None))
                self.assertEqual(self.settle_link(link, start + timedelta(hours=73)), [self.a.pk])
                self.assertEqual(report_settlement_snapshot(first_report)[0], 'SETTLED')
                self.assertEqual(report_settlement_snapshot(second_report), ('WAITING', None))
                transaction.set_rollback(True)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True)
    def test_confirmation_worker_settles_then_awards_only_net_principal(self):
        from unittest.mock import patch
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation, PointLedger
        from orders.negotiation_followup_accounting import terminate_followup_internal
        from orders.tests import test_negotiation_settlement as settlement_fixture
        case, _, proposal = self.accepted_followup()
        terminate_followup_internal(case.pk, proposal.pk, operator=self.operator_account,
                                    legacy_operator=self.operator, request_id='followup-worker')
        self.order.refresh_from_db(); start = self.order.completed_at
        a_report = settlement_fixture.NegotiationSettlementTest.report(self, self.a_account, start)
        b_report = settlement_fixture.NegotiationSettlementTest.report(self, self.b_account, start + timedelta(hours=3))
        with patch('django.utils.timezone.now', return_value=start + timedelta(hours=73)):
            self.assertFalse(process_order(self.order.pk))
        self.assertFalse(PointLedger.objects.filter(order=self.order, kind='CONSUMPTION').exists())
        for account, report, expected in ((self.a_account, a_report, 'SETTLED'), (self.b_account, b_report, 'WAITING')):
            response = self.client_for(account).get('/api/wallet/reports/')
            self.assertEqual(response.data['code'], 0, response.data)
            own = next(row for row in response.data['data'] if row['id'] == report.pk)
            self.assertEqual(own['settlement_status'], expected)
            self.assertEqual(own['refund_summary']['refund_count'], 1)
        with patch('django.utils.timezone.now', return_value=start + timedelta(hours=76)):
            self.assertTrue(process_order(self.order.pk))
            self.assertFalse(process_order(self.order.pk))
        confirmation = ConsumptionConfirmation.objects.get(order=self.order)
        self.assertEqual((confirmation.amount, confirmation.refunded_amount), (10000, 2000))
        self.assertEqual(PointLedger.objects.get(order=self.order, kind='CONSUMPTION').amount, 8000)

    @override_settings(NEGOTIATION_FOLLOWUP_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
                       NEGOTIATION_ZERO_EXECUTION_ENABLED=True)
    def test_real_followup_apis_execute_and_recover_both_receipts(self):
        from stores.db import transaction
        for first, second in ((0, 0), (1000, 0), (0, 1000), (1000, 1000)):
            with self.subTest(first=first, second=second), transaction.atomic():
                case, previous = self.followup_case(first)
                proposal_url = f'/api/admin/aftersales/{case.pk}/negotiation-proposal/'
                support = self.client_for(self.staff)
                current = support.get(proposal_url)
                self.assertFalse(current.data['data']['followup_preview_only'])
                created = support.post(proposal_url, {'request_id':'api-followup', 'expected_version':1,
                    'refund_units':second, 'provider_terms':[{'account_id':self.b_account.pk,
                    'retained_units':4000-second}], 'explanation':'双方确认继续陪玩的退款方案'}, format='json')
                self.assertEqual(created.data.get('code'), 0, created.data)
                proposal_id = created.data['data']['id']
                for actor in (self.boss_account, self.b_account):
                    response = self.client_for(actor).post(f'/api/orders/orders/{self.order.pk}/negotiation-proposal/',
                        {'proposal_id':proposal_id, 'accepted':True}, format='json')
                    self.assertEqual(response.data.get('code'), 0, response.data)
                staff = self.client_for(self.operator_account)
                url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
                payload = {'proposal_id':proposal_id, 'client_request_id':'api-followup-execute'}
                done = staff.post(url, payload, format='json')
                self.assertEqual(done.data.get('code'), 0, done.data)
                self.assertEqual(done.data['data']['refund_units'], second)
                retry = staff.post(url, payload, format='json')
                self.assertEqual(retry.data['data']['execution_id'], done.data['data']['execution_id'])
                old_payload = {'proposal_id':previous.proposal_id, 'client_request_id':'termination-test'}
                with override_settings(NEGOTIATION_FOLLOWUP_ENABLED=False, NEGOTIATION_PROPOSAL_ENABLED=False,
                                       NEGOTIATION_REFUND_EXECUTION_ENABLED=False):
                    for query, expected in ((payload, done.data['data']['execution_id']), (old_payload, previous.pk)):
                        recovered = staff.get(url, query)
                        self.assertEqual(recovered.data['data']['execution_id'], expected, recovered.data)
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, 'COMPLETED')
                self.assertEqual(self.order.refund_records.count(), int(first > 0) + int(second > 0))
                transaction.set_rollback(True)
