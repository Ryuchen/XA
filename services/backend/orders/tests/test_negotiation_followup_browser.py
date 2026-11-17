"""Three real roles confirm and execute a follow-up negotiation through the HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from orders.models import NegotiationFollowupLink
from orders.tests import test_negotiation_followup as fixtures
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_NEGOTIATION_FOLLOWUP_BROWSER') == '1', 'Requires isolated ASGI and local front ends')
@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_FOLLOWUP_ENABLED=True,
                   NEGOTIATION_REFUND_EXECUTION_ENABLED=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class FollowupThreeRoleBrowserTest(LiveServerTestCase):
    account = fixtures.FollowupPreviewTests.account
    client_for = fixtures.FollowupPreviewTests.client_for
    case = fixtures.FollowupPreviewTests.case
    propose = fixtures.FollowupPreviewTests.propose
    accepted = fixtures.FollowupPreviewTests.accepted
    execute = fixtures.FollowupPreviewTests.execute
    setUp = fixtures.FollowupPreviewTests.setUp
    followup_case = fixtures.FollowupPreviewTests.followup_case

    def test_boss_can_only_select_unaffected_provider_after_completion(self):
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=1000)
        self.execute(case, proposal)
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data.get('code'), 0, result.data)
        opened = self.client_for(self.boss_account).post(f'/api/orders/orders/{self.order.pk}/aftersale/', {
            'reason':'继续处理 B 的服务问题', 'affected_provider_account_ids':[self.b_account.pk],
            'client_request_id':'scope-browser-aftersale'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        case.refresh_from_db()
        self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        self.assertEqual(sum(row.get('reason') == '继续处理 B 的服务问题' for row in case.history), 1)

    def test_three_roles_confirm_and_recover_lost_execution_response(self):
        case, _ = self.followup_case(1000)
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        before = get_wallet(user=self.boss, account=self.boss_account).balance
        staff_client = self.client_for(self.operator_account)
        proposal = staff_client.post(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/', {
            'request_id':'followup-browser-proposal', 'expected_version':1, 'refund_units':1000,
            'provider_terms':[{'account_id':self.b_account.pk, 'retained_units':3000}],
            'explanation':'继续履约陪玩的协商退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for account in (self.boss_account, self.b_account):
            decided = self.client_for(account).post(f'/api/orders/orders/{self.order.pk}/negotiation-proposal/',
                {'proposal_id':proposal_id, 'accepted':True}, format='json')
            self.assertEqual(decided.data['code'], 0, decided.data)
        executed = staff_client.post(f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal_id, 'client_request_id':'followup-browser-refund'}, format='json')
        self.assertEqual(executed.data['code'], 0, executed.data)
        case.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((case.status, self.order.status, self.order.payment_status),
                         ('PARTIALLY_REFUNDED', 'COMPLETED', 'PARTIALLY_REFUNDED'))
        self.assertEqual(self.order.refund_records.count(), 2)
        self.assertEqual(NegotiationFollowupLink.objects.filter(order=self.order).count(), 1)
        self.assertEqual(get_wallet(user=self.boss, account=self.boss_account).balance, before + 1000)

    @override_settings(NEGOTIATION_COMPLETED_FOLLOWUP_PREVIEW_ENABLED=True,
                       NEGOTIATION_COMPLETED_FOLLOWUP_EXECUTION_TESTING=True)
    def test_completed_low_rating_to_refund_across_three_roles(self):
        from orders.tests.test_provider_aftersale_status import image
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        case, proposal = self.accepted(amount=1000)
        self.execute(case, proposal)
        result = self.client_for(self.b_account).post(f'/api/orders/orders/{self.order.pk}/complete/',
            {'completion_image':image()}, format='multipart')
        self.assertEqual(result.data.get('code'), 0, result.data)
        self.order.refresh_from_db(); completed_at = self.order.completed_at
        result = self.client_for(self.boss_account).post('/api/orders/evaluate/', {
            'order_id':self.order.pk, 'provider_account_id':self.b_account.pk, 'score':1,
            'skill_score':5, 'attitude_score':5, 'communication_score':5}, format='json')
        self.assertEqual(result.data.get('code'), 0, result.data)
        case.refresh_from_db()
        self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        before = get_wallet(user=self.boss, account=self.boss_account).balance
        staff_client = self.client_for(self.operator_account)
        proposal = staff_client.post(f'/api/admin/aftersales/{case.pk}/negotiation-proposal/', {
            'request_id':'followup-browser-proposal', 'expected_version':1, 'refund_units':1000,
            'provider_terms':[{'account_id':self.b_account.pk, 'retained_units':3000}],
            'explanation':'继续履约陪玩的协商退款'}, format='json')
        self.assertEqual(proposal.data['code'], 0, proposal.data)
        proposal_id = proposal.data['data']['id']
        for account in (self.boss_account, self.b_account):
            decided = self.client_for(account).post(f'/api/orders/orders/{self.order.pk}/negotiation-proposal/',
                {'proposal_id':proposal_id, 'accepted':True}, format='json')
            self.assertEqual(decided.data['code'], 0, decided.data)
        executed = staff_client.post(f'/api/admin/aftersales/{case.pk}/negotiation-execution/',
            {'proposal_id':proposal_id, 'client_request_id':'followup-browser-refund'}, format='json')
        self.assertEqual(executed.data['code'], 0, executed.data)
        self.order.refresh_from_db(); case.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at, case.status),
                         ('COMPLETED', completed_at, 'PARTIALLY_REFUNDED'))
        self.assertEqual(self.order.refund_records.count(), 2)
        self.assertEqual(NegotiationFollowupLink.objects.filter(order=self.order).count(), 1)
        self.assertEqual(get_wallet(user=self.boss, account=self.boss_account).balance, before + 1000)
