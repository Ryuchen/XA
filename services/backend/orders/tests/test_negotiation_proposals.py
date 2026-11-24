from unittest import skipUnless
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase, APITransactionTestCase

from orders.models import AfterSale, NegotiationDecision, NegotiationProposal, Order
from orders.negotiation_proposals import create, decide
from orders.tests.test_staff_negotiation_case import StaffNegotiationCaseTests
from wallet.models import Transaction, Wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class NegotiationProposalTests(APITestCase):
    account = StaffNegotiationCaseTests.account
    client_for = StaffNegotiationCaseTests.client_for

    def setUp(self):
        from django.utils import timezone
        from orders.models import OrderOriginalAttribution, OrderParticipationSegment
        from wallet.services import get_wallet
        StaffNegotiationCaseTests.setUp(self)
        self.order.original_amount = 10000; self.order.provider_income = 8000; self.order.shop_income = 2000
        self.order.save(update_fields=['original_amount', 'provider_income', 'shop_income'])
        self.order.providers.update(commission_rate=20)
        wallet = get_wallet(user=self.boss, account=self.boss_account)
        wallet.balance = 9000; wallet.save(update_fields=['balance'])
        Transaction.objects.create(wallet=wallet, order=self.order, tx_type='PAY', amount=-10000,
                                   balance_before=19000, balance_after=9000)
        attribution = OrderOriginalAttribution.objects.create(order=self.order, original_amount=10000, status='ACTIVE')
        from orders.models import OrderWorkStint
        started = timezone.now()
        for index, account in enumerate((self.a_account, self.b_account), 1):
            OrderParticipationSegment.objects.create(attribution=attribution, provider_account=account, slot=index,
                original_share=5000, accepted_at=started, started_at=started,
                provider_business_cycle=account.business_cycle)
            OrderWorkStint.objects.create(order=self.order, account=account, started_at=started)

    def case(self, scope=None):
        payload = {**self.payload, 'affected_provider_account_ids':scope or [self.a_account.pk]}
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        return AfterSale.objects.get(pk=response.data['data']['id'])

    def propose(self, case, *, version=0, request_id='proposal-1', scope=None, refund=1000):
        affected = scope or [self.a_account.pk]
        burden = refund if len(affected) == 1 else ((refund + 19) // 20) * 10
        return create(case.pk, actor=self.staff, request_id=request_id, expected_version=version,
                      refund_units=refund, provider_terms=[{'account_id':pk, 'retained_units':max(4000-burden, 0)} for pk in affected],
                      explanation='按协商保留收益并退还余额')

    def test_scoped_boss_and_affected_provider_confirm_without_money_or_order_changes(self):
        case = self.case()
        proposal = self.propose(case)
        self.assertEqual(proposal.status, 'PENDING')
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.b_account, proposal_id=proposal.pk, accepted=True)
        decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'PENDING')
        decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'ACCEPTED')
        self.assertIsNotNone(proposal.accepted_at)
        self.assertEqual(proposal.decisions.filter(accepted=True).count(), 2)
        decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=False)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('IN_SERVICE', 'PAID'))
        self.assertEqual(list(Transaction.objects.filter(order=self.order).values_list('tx_type', flat=True)), ['PAY'])
        self.assertFalse(self.order.refund_records.exists())

    def test_scoped_private_notices_follow_commit_and_are_not_duplicated_on_retry(self):
        from site_messages.models import Message
        case = self.case()
        with self.captureOnCommitCallbacks(execute=True):
            proposal = self.propose(case)
        notices = Message.objects.filter(related_order_id=self.order.pk, title='订单协商方案待确认')
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.boss_account.pk, self.a_account.pk})
        self.assertEqual(notices.count(), 2)
        self.assertFalse(notices.filter(recipient_account=self.b_account).exists())
        self.assertNotIn(case.reason, str(list(notices.values('preview', 'detail'))))
        with self.captureOnCommitCallbacks(execute=True):
            self.propose(case)  # Lost response: same request, no new notice.
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertEqual(notices.count(), 2)
        self.assertFalse(Message.objects.filter(related_order_id=self.order.pk, title='订单协商方案已确认').exists())
        with self.captureOnCommitCallbacks(execute=True):
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
        finished = Message.objects.filter(related_order_id=self.order.pk, title='订单协商方案已确认')
        self.assertEqual(finished.count(), 2)
        self.assertEqual(set(finished.values_list('recipient_account_id', flat=True)),
                         {self.boss_account.pk, self.a_account.pk})
        self.assertFalse(finished.filter(recipient_account=self.b_account).exists())
        self.assertIn('退款结果以实际流水为准', finished.first().preview)
        self.assertFalse(self.order.refund_records.exists())

    def test_rejection_notice_does_not_reveal_who_objected(self):
        from site_messages.models import Message
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        proposal = self.propose(case, scope=scope)
        with self.captureOnCommitCallbacks(execute=True):
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=False)
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=False)
        notices = Message.objects.filter(related_order_id=self.order.pk, title='订单协商方案有异议')
        self.assertEqual(notices.count(), 3)
        self.assertEqual(set(notices.values_list('recipient_account_id', flat=True)),
                         {self.boss_account.pk, self.a_account.pk, self.b_account.pk})
        self.assertNotIn(self.a.nickname, str(list(notices.values('preview', 'detail'))))

    def test_rolled_back_proposal_does_not_create_notices(self):
        from site_messages.models import Message
        from stores.db import transaction
        case = self.case()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.propose(case)
                    raise RuntimeError('simulate rollback')
        self.assertFalse(NegotiationProposal.objects.exists())
        self.assertFalse(Message.objects.filter(related_order_id=self.order.pk, title='订单协商方案待确认').exists())

    def test_staff_cannot_close_case_while_proposal_pending_or_accepted(self):
        from orders.tests.factories import make_console_user
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case(); proposal = self.propose(case)
        url = f'/api/admin/aftersales/{case.pk}/resolve/'
        client = self.client_for(resolver)
        payload = {'resolution':'完成售后'}
        self.assertNotEqual(client.post(url, payload, format='json').data.get('code', 400), 0)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        self.assertNotEqual(client.post(url, payload, format='json').data.get('code', 400), 0)
        case.refresh_from_db(); self.assertEqual(case.status, 'OPEN')
        self.assertFalse(self.order.refund_records.exists())

    def test_disabled_switch_does_not_discard_existing_accepted_agreement(self):
        from orders.tests.factories import make_console_user
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case(); proposal = self.propose(case)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False):
            result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
                {'resolution':'完成售后'}, format='json')
        self.assertNotEqual(result.data.get('code', 400), 0, result.data)
        case.refresh_from_db(); self.assertEqual(case.status, 'OPEN')

    def test_rejected_unaccepted_proposal_allows_regular_resolution(self):
        from orders.tests.factories import make_console_user
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case(); proposal = self.propose(case)
        decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=False)
        result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
            {'resolution':'方案未达成，客户已知悉'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        case.refresh_from_db(); self.assertEqual(case.status, 'RESOLVED')

    def test_new_rejected_revision_does_not_erase_accepted_agreement(self):
        from orders.tests.factories import make_console_user
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case(); first = self.propose(case)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=first.pk, accepted=True)
        second = self.propose(case, version=1, request_id='revision-after-accepted', refund=2000)
        decide(case.pk, actor=self.a_account, proposal_id=second.pk, accepted=False)
        result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
            {'resolution':'完成售后'}, format='json')
        # 现行批复规则：旧方案已同意未执行、新方案被拒绝时允许客服结案，
        # 但原确认记录保留不被抹除（18-盘点协商售后结案补充）。
        self.assertEqual(result.data['code'], 0, result.data)
        case.refresh_from_db(); self.assertEqual(case.status, 'RESOLVED')
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('ACCEPTED', 'REJECTED'))

    def test_legacy_store_without_proposal_tables_can_resolve_when_feature_disabled(self):
        from unittest.mock import patch
        from orders.tests.factories import make_console_user
        from stores.db import connection
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case()
        with override_settings(NEGOTIATION_PROPOSAL_ENABLED=False), patch.object(connection.introspection, 'table_names', return_value=[]):
            result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
                {'resolution':'已核实普通售后'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)

    def test_enabled_feature_with_missing_schema_cannot_silently_resolve(self):
        from unittest.mock import patch
        from orders.tests.factories import make_console_user
        from stores.db import connection
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case()
        with patch.object(connection.introspection, 'table_names', return_value=[]):
            result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
                {'resolution':'已核实普通售后'}, format='json')
        self.assertNotEqual(result.data.get('code', 400), 0, result.data)
        case.refresh_from_db(); self.assertEqual(case.status, 'OPEN')

    def test_accepted_proposal_from_earlier_episode_does_not_block_new_case(self):
        from orders.tests.factories import make_console_user
        resolver = self.account(make_console_user(['order:view', 'order:settle']))
        case = self.case(); proposal = self.propose(case)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        case.status = 'RESOLVED'; case.save(update_fields=['status'])  # historical closed episode
        renewed = {**self.payload, 'client_request_id':'later-episode'}
        self.assertEqual(self.client.post(self.url, renewed, format='json').data['code'], 0)
        result = self.client_for(resolver).post(f'/api/admin/aftersales/{case.pk}/resolve/',
            {'resolution':'新售后已核实'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)

    def test_preview_calculates_retained_and_additional_burden_and_rejects_manual_mismatch(self):
        case = self.case()
        url = f'/api/admin/aftersales/{case.pk}/negotiation-preview/'
        response = self.client.get(url, {'refund_units':5000})
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['provider_terms'], [{'account_id':self.a_account.pk,
            'original_income_units':4000, 'burden_units':5000, 'retained_units':0, 'additional_units':1000}])
        with self.assertRaises(ValidationError):
            create(case.pk, actor=self.staff, request_id='incorrect-retention', expected_version=0,
                   refund_units=5000, provider_terms=[{'account_id':self.a_account.pk, 'retained_units':1000}],
                   explanation='不一致的方案')
        self.assertFalse(NegotiationProposal.objects.exists())

    def test_funding_preview_reports_order_wallet_deposit_and_shortfall_without_posting(self):
        from orders.tests.factories import make_console_user
        from wallet.services import get_wallet
        case = self.case()
        accountant = self.account(make_console_user(['order:view', 'order:refund']))
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.balance = 300; wallet.save(update_fields=['balance'])
        profile = self.a.escort_profile
        profile.deposit_paid = 600; profile.save(update_fields=['deposit_paid'])
        url = f'/api/admin/aftersales/{case.pk}/negotiation-funding/'
        self.assertEqual(self.client.get(url, {'refund_units':5000}).status_code, 403)
        self.assertEqual(self.client_for(self.boss_account).get(url, {'refund_units':5000}).status_code, 403)
        result = self.client_for(accountant).get(url, {'refund_units':5000})
        self.assertEqual(result.data['code'], 0, result.data)
        data = result.data['data']
        self.assertEqual(data['providers'], [{'account_id':self.a_account.pk, 'pending_income_units':4000,
            'wallet_units':300, 'deposit_units':600, 'shortfall_units':100}])
        self.assertFalse(data['book_amounts_sufficient'])
        self.assertFalse(data['executable'])
        wallet.refresh_from_db(); profile.refresh_from_db()
        self.assertEqual((wallet.balance, profile.deposit_paid), (300, 600))
        self.assertFalse(self.order.refund_records.exists())
        wallet.balance = 400; wallet.save(update_fields=['balance'])
        updated = self.client_for(accountant).get(url, {'refund_units':5000}).data['data']
        self.assertTrue(updated['book_amounts_sufficient'])
        self.assertFalse(updated['executable'])
        self.assertEqual(updated['providers'][0]['shortfall_units'], 0)

    def test_funding_preview_rejects_inactive_or_mismatched_wallet(self):
        from orders.tests.factories import make_console_user
        from wallet.services import get_wallet
        case = self.case()
        accountant = self.account(make_console_user(['order:view', 'order:refund']))
        wallet = get_wallet(user=self.a, account=self.a_account)
        wallet.is_active = False; wallet.save(update_fields=['is_active'])
        result = self.client_for(accountant).get(f'/api/admin/aftersales/{case.pk}/negotiation-funding/', {'refund_units':5000})
        self.assertEqual(result.status_code, 400)
        self.assertIn('钱包或押金', str(result.data))

    def test_team_funding_checks_each_provider_separately(self):
        from orders.tests.factories import make_console_user
        from wallet.services import get_wallet
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        accountant = self.account(make_console_user(['order:view', 'order:refund']))
        for user, account, balance, deposit in ((self.a, self.a_account, 20, 10), (self.b, self.b_account, 40, 0)):
            wallet = get_wallet(user=user, account=account)
            wallet.balance = balance; wallet.save(update_fields=['balance'])
            profile = user.escort_profile; profile.deposit_paid = deposit; profile.save(update_fields=['deposit_paid'])
        result = self.client_for(accountant).get(f'/api/admin/aftersales/{case.pk}/negotiation-funding/', {'refund_units':8100})
        self.assertEqual(result.data['code'], 0, result.data)
        rows = {row['account_id']:row for row in result.data['data']['providers']}
        self.assertEqual((rows[self.a_account.pk]['wallet_units'], rows[self.a_account.pk]['deposit_units'],
                          rows[self.a_account.pk]['shortfall_units']), (20, 10, 20))
        self.assertEqual((rows[self.b_account.pk]['wallet_units'], rows[self.b_account.pk]['deposit_units'],
                          rows[self.b_account.pk]['shortfall_units']), (40, 0, 10))
        self.assertFalse(result.data['data']['book_amounts_sufficient'])

    def test_two_provider_preview_rounds_each_burden_up(self):
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        response = self.client.get(f'/api/admin/aftersales/{case.pk}/negotiation-preview/', {'refund_units':510})
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        self.assertEqual(data['rounding_to_store_units'], 10)
        self.assertEqual([(row['burden_units'], row['retained_units']) for row in data['provider_terms']],
                         [(260, 3740), (260, 3740)])

    def test_confirmed_example_160_income_less_100_refund_retains_60(self):
        self.order.providers.filter(provider_account=self.b_account).delete()
        self.order.providers.filter(provider_account=self.a_account).update(settlement_base=2000, provider_income=1600)
        self.order.amount = 2000; self.order.original_amount = 2000
        self.order.provider_income = 1600; self.order.shop_income = 400
        self.order.save(update_fields=['amount', 'original_amount', 'provider_income', 'shop_income'])
        Transaction.objects.filter(order=self.order, tx_type='PAY').update(amount=-2000, balance_before=11000, balance_after=9000)
        self.order.original_attribution.segments.filter(provider_account=self.b_account).delete()
        self.order.original_attribution.segments.update(original_share=2000)
        self.order.original_attribution.original_amount = 2000
        self.order.original_attribution.save(update_fields=['original_amount'])
        case = self.case()
        result = self.client.get(f'/api/admin/aftersales/{case.pk}/negotiation-preview/', {'refund_units':1000})
        self.assertEqual(result.data['data']['provider_terms'][0]['retained_units'], 600)
        self.assertEqual(result.data['data']['provider_terms'][0]['burden_units'], 1000)

    def test_team_requires_both_affected_providers(self):
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        proposal = self.propose(case, scope=scope)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'PENDING')
        decide(case.pk, actor=self.b_account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'ACCEPTED')

    def test_each_affected_provider_only_sees_own_pay_terms(self):
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        self.propose(case, scope=scope)
        url = f'/api/orders/orders/{self.order.pk}/negotiation-proposal/'
        boss_data = self.client_for(self.boss_account).get(url).data['data']
        self.assertEqual(len(boss_data['provider_terms']), 2)
        for account in (self.a_account, self.b_account):
            data = self.client_for(account).get(url).data['data']
            self.assertEqual([row['account_id'] for row in data['provider_terms']], [account.pk])
            self.assertEqual([row['account_id'] for row in data['decisions']], [account.pk])
            self.assertNotIn('request_id', data)
            self.assertNotIn('created_by_id', data)
            self.assertNotIn('rounding_to_store_units', data)

    def test_rejection_revision_and_idempotence_preserve_prior_decisions(self):
        case = self.case(); first = self.propose(case)
        decide(case.pk, actor=self.a_account, proposal_id=first.pk, accepted=False)
        first.refresh_from_db(); self.assertEqual(first.status, 'REJECTED')
        again = self.propose(case); self.assertEqual(again.pk, first.pk)
        with self.assertRaises(ValidationError): self.propose(case, refund=2000)
        second = self.propose(case, version=1, request_id='proposal-2')
        self.assertEqual((second.version, second.status), (2, 'PENDING'))
        self.assertIsNone(second.decisions.get(account=self.a_account).accepted)
        with self.assertRaises(ValidationError): self.propose(case)
        with self.assertRaises(ValidationError): self.propose(case, version=1, request_id='proposal-3')
        with self.assertRaises(ValidationError): decide(case.pk, actor=self.boss_account, proposal_id=first.pk, accepted=True)
        self.assertEqual(NegotiationProposal.objects.count(), 2)

    def test_revision_after_agreement_requires_new_decisions_and_invalidates_old_submission(self):
        case = self.case(); first = self.propose(case)
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=first.pk, accepted=True)
        second = self.propose(case, version=1, request_id='revision-after-acceptance', refund=2000)
        first.refresh_from_db()
        self.assertEqual(first.status, 'ACCEPTED')
        self.assertEqual(second.decisions.filter(accepted__isnull=True).count(), 2)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.a_account, proposal_id=first.pk, accepted=True)
        second.refresh_from_db(); self.assertEqual(second.status, 'PENDING')

    def test_inconsistent_decision_records_are_not_accepted(self):
        case = self.case(); proposal = self.propose(case)
        proposal.decisions.filter(account=self.a_account).delete()
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)

    def test_case_or_assignments_changed_block_old_decision(self):
        case = self.case(); proposal = self.propose(case)
        self.order.providers.filter(provider_account=self.a_account).update(released_at=self.order.created_at)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertEqual(NegotiationDecision.objects.filter(proposal=proposal, accepted__isnull=False).count(), 0)

    def test_reopened_case_requires_new_proposal(self):
        case = self.case(); first = self.propose(case)
        case.status = 'RESOLVED'; case.save(update_fields=['status'])
        renewed = {**self.payload, 'client_request_id':'renewed-case'}
        self.assertEqual(self.client.post(self.url, renewed, format='json').data['code'], 0)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=first.pk, accepted=True)
        case.refresh_from_db()
        next_proposal = self.propose(case, version=1, request_id='renewed-proposal')
        self.assertEqual((next_proposal.version, next_proposal.status), (2, 'PENDING'))

    def test_changed_terms_cannot_be_approved(self):
        case = self.case(); proposal = self.propose(case)
        NegotiationProposal.objects.filter(pk=proposal.pk).update(refund_units=2000)
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertFalse(NegotiationDecision.objects.filter(proposal=proposal, accepted__isnull=False).exists())

    def test_changed_original_income_blocks_later_confirmation(self):
        case = self.case(); proposal = self.propose(case)
        decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.order.providers.filter(provider_account=self.a_account).update(commission_rate=22, provider_income=3900)
        self.order.provider_income = 7900; self.order.shop_income = 2100
        self.order.save(update_fields=['provider_income', 'shop_income'])
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.a_account, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'PENDING')
        self.assertIsNone(proposal.decisions.get(account=self.a_account).accepted)

    def test_in_service_case_remains_actionable_after_order_completes(self):
        from django.utils import timezone
        case = self.case(); proposal = self.propose(case)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'ACCEPTED')
        self.assertFalse(self.order.refund_records.exists())

    def test_unaffected_settled_provider_does_not_prevent_affected_negotiation(self):
        from django.utils import timezone
        from wallet.services import get_wallet
        case = self.case(); proposal = self.propose(case)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        share = self.order.providers.get(provider_account=self.b_account)
        share.settled_at = timezone.now(); share.save(update_fields=['settled_at'])
        wallet = get_wallet(user=self.b, account=self.b_account)
        wallet.balance = 4000; wallet.save(update_fields=['balance'])
        credit = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=4000,
            balance_before=0, balance_after=4000, remark=f'订单收入：{self.order.order_no}')
        for actor in (self.boss_account, self.a_account):
            decide(case.pk, actor=actor, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'ACCEPTED')
        wallet.refresh_from_db(); self.assertEqual(wallet.balance, 4000)
        self.assertEqual(Transaction.objects.filter(pk=credit.pk).count(), 1)
        self.assertFalse(self.order.refund_records.exists())

    def test_unrelated_credit_must_match_settled_share_before_confirmation(self):
        from django.utils import timezone
        from wallet.services import get_wallet
        case = self.case(); proposal = self.propose(case)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        self.order.providers.filter(provider_account=self.b_account).update(settled_at=timezone.now())
        wallet = get_wallet(user=self.b, account=self.b_account)
        Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=3990,
            balance_before=0, balance_after=3990, remark=f'订单收入：{self.order.order_no}')
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertFalse(proposal.decisions.filter(accepted__isnull=False).exists())

    def test_completed_order_rejects_case_opened_after_completion(self):
        from datetime import timedelta
        case = self.case(); proposal = self.propose(case)
        self.order.status = 'COMPLETED'; self.order.completed_at = case.created_at - timedelta(seconds=1)
        self.order.save(update_fields=['status', 'completed_at'])
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertFalse(proposal.decisions.filter(accepted__isnull=False).exists())

    def test_affected_provider_settlement_blocks_old_proposal(self):
        from django.utils import timezone
        case = self.case(); proposal = self.propose(case)
        self.order.status = 'COMPLETED'; self.order.completed_at = timezone.now()
        self.order.save(update_fields=['status', 'completed_at'])
        self.order.providers.filter(provider_account=self.a_account).update(settled_at=timezone.now())
        with self.assertRaises(ValidationError):
            decide(case.pk, actor=self.boss_account, proposal_id=proposal.pk, accepted=True)
        self.assertFalse(proposal.decisions.filter(accepted__isnull=False).exists())

    def test_missing_original_team_shares_or_invalid_payment_blocks_preview(self):
        scope = sorted([self.a_account.pk, self.b_account.pk]); case = self.case(scope)
        self.order.original_attribution.status = 'REVIEW'
        self.order.original_attribution.save(update_fields=['status'])
        url = f'/api/admin/aftersales/{case.pk}/negotiation-preview/'
        self.assertEqual(self.client.get(url, {'refund_units':1000}).status_code, 400)
        self.order.original_attribution.status = 'ACTIVE'
        self.order.original_attribution.save(update_fields=['status'])
        Transaction.objects.filter(order=self.order, tx_type='PAY').update(balance_after=8999)
        self.assertEqual(self.client.get(url, {'refund_units':1000}).status_code, 400)
        self.assertFalse(NegotiationProposal.objects.exists())

    def test_three_role_api_confirms_only_affected_people_and_rejects_old_version(self):
        case = self.case()
        staff_url = f'/api/admin/aftersales/{case.pk}/negotiation-proposal/'
        user_url = f'/api/orders/orders/{self.order.pk}/negotiation-proposal/'
        payload = {'request_id':'api-proposal-1', 'expected_version':0, 'refund_units':1000,
                   'provider_terms':[{'account_id':self.a_account.pk, 'retained_units':3000}],
                   'explanation':'双方协商本次终止'}
        response = self.client.post(staff_url, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        proposal_id = response.data['data']['id']
        self.assertEqual(self.client_for(self.b_account).get(user_url).status_code, 404)
        self.assertEqual(self.client_for(self.viewer).get(staff_url).status_code, 403)
        for account in (self.boss_account, self.a_account):
            customer = self.client_for(account)
            self.assertEqual(customer.get(user_url).data['data']['id'], proposal_id)
            response = customer.post(user_url, {'proposal_id':proposal_id, 'accepted':True}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        self.assertEqual(response.data['data']['status'], 'ACCEPTED')
        self.assertEqual(self.client.get(staff_url).data['data']['version'], 1)
        self.assertFalse(self.order.refund_records.exists())

    def test_invalid_inputs_and_unauthorized_staff_never_create_proposal(self):
        case = self.case()
        for kwargs in ({'refund':1}, {'refund':10010}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError): self.propose(case, **kwargs)
        with self.assertRaises(ValidationError):
            create(case.pk, actor=self.viewer, request_id='not-allowed', expected_version=0, refund_units=1000,
                   provider_terms=[{'account_id':self.a_account.pk, 'retained_units':1500}], explanation='核对说明')
        self.assertFalse(NegotiationProposal.objects.exists())

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=False)
    def test_disabled_does_not_create(self):
        case = self.case()
        with self.assertRaises(ValidationError): self.propose(case)
        self.assertEqual(self.client.get(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/').status_code, 404)
        self.assertEqual(self.client_for(self.boss_account).get(
            f'/api/orders/orders/{self.order.pk}/negotiation-proposal/').status_code, 404)
        self.assertFalse(NegotiationProposal.objects.exists())


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class NegotiationActualOrderTests(APITransactionTestCase):
    from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest as flow
    setUp = flow.setUp
    login = flow.login
    ok = flow.ok

    def test_order_payment_case_and_both_account_confirmations(self):
        from orders.tests.test_provider_aftersale_status import image
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'proposal-real-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        balance = Wallet.objects.get(user=self.boss).balance
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        self.login(self.staff)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'联系协商',
            'client_request_id':'proposal-real-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'proposal-real-1', 'expected_version':0, 'refund_units':5000,
            'provider_terms':[{'account_id':self.accounts[self.provider.pk].pk, 'retained_units':3000}],
            'explanation':'退款并保留已履约收益'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            result = self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        self.assertEqual(result['status'], 'ACCEPTED')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, balance)
        order = Order.objects.get(pk=order_id)
        self.assertEqual((order.status, order.payment_status), ('IN_SERVICE', 'PAID'))
        self.assertFalse(order.refund_records.exists())


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class NegotiationStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_number_does_not_expose_other_store_proposal(self):
        from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from orders.models import OrderProvider
        from wallet.services import get_wallet
        from rest_framework.test import APIClient
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, player = make_user(), make_provider()
                boss_account = get_or_create_account_for_legacy_user(boss)
                player_account = get_or_create_account_for_legacy_user(player)
                link_legacy_relations(boss, boss_account); link_legacy_relations(player, player_account)
                player.escort_profile.account = player_account; player.escort_profile.save(update_fields=['account'])
                staff = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                order = make_order(boss, service=make_service(price=1000), provider=player, status='IN_SERVICE',
                                   customer_account=boss_account, provider_account=player_account, payment_status='PAID',
                                   original_amount=1000, provider_income=800, shop_income=200)
                OrderProvider.objects.create(order=order, provider=player, provider_account=player_account,
                    settlement_base=1000, provider_income=800, commission_rate=20)
                wallet = get_wallet(user=boss, account=boss_account)
                wallet.balance = 2000; wallet.save(update_fields=['balance'])
                player_wallet = get_wallet(user=player, account=player_account)
                player_wallet.balance = 30 if host.startswith('sl.') else 80
                player_wallet.save(update_fields=['balance'])
                player.escort_profile.deposit_paid = 0
                player.escort_profile.save(update_fields=['deposit_paid'])
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-1000,
                                           balance_before=3000, balance_after=2000)
                admin = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
                customer = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
                rows.append((store, order.pk, player_account.pk, admin, customer))
        self.assertEqual(rows[0][1], rows[1][1])
        with store_context(rows[0][0]):
            _, order_id, player_id, admin, _ = rows[0]
            case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
                'affected_provider_account_ids':[player_id], 'reason':'门店咨询', 'client_request_id':'store-case'}, format='json').data['data']
            with self.captureOnCommitCallbacks(using='default', execute=True):
                response = admin.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
                    'request_id':'store-proposal', 'expected_version':0, 'refund_units':100,
                    'provider_terms':[{'account_id':player_id, 'retained_units':700}], 'explanation':'本店协商说明'}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            self.assertEqual(NegotiationProposal.objects.count(), 1)
            from site_messages.models import Message
            self.assertEqual(Message.objects.filter(title='订单协商方案待确认').count(), 2)
            funding = admin.get(f"/api/admin/aftersales/{case['id']}/negotiation-funding/", {'refund_units':900})
            self.assertEqual(funding.data['data']['providers'][0]['shortfall_units'], 70)
            from django.utils import timezone
            Order.objects.filter(pk=order_id).update(status='COMPLETED', completed_at=timezone.now())
            decision = rows[0][4].post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id':response.data['data']['id'], 'accepted':True}, format='json')
            self.assertEqual(decision.data['code'], 0, decision.data)
            blocked = admin.post(f"/api/admin/aftersales/{case['id']}/resolve/", {'resolution':'结案'}, format='json')
            self.assertNotEqual(blocked.data.get('code', 400), 0, blocked.data)
        with store_context(rows[1][0]):
            self.assertFalse(NegotiationProposal.objects.exists())
            self.assertFalse(Message.objects.filter(title='订单协商方案待确认').exists())
            self.assertEqual(rows[1][4].get(f'/api/orders/orders/{rows[1][1]}/negotiation-proposal/').status_code, 404)
            _, order_id, player_id, admin, _ = rows[1]
            case = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
                'affected_provider_account_ids':[player_id], 'reason':'门店咨询', 'client_request_id':'store-case'}, format='json').data['data']
            funding = admin.get(f"/api/admin/aftersales/{case['id']}/negotiation-funding/", {'refund_units':900})
            self.assertEqual(funding.data['data']['providers'][0]['shortfall_units'], 20)
            resolved = admin.post(f"/api/admin/aftersales/{case['id']}/resolve/", {'resolution':'本店已核实'}, format='json')
            self.assertEqual(resolved.data['code'], 0, resolved.data)
        with store_context(rows[0][0]):
            self.assertEqual(AfterSale.objects.get(order_id=rows[0][1]).status, 'OPEN')
