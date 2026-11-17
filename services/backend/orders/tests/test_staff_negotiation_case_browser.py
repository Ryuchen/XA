"""Actual admin negotiation case registration through the isolated HTTP API."""
import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from orders.models import AfterSale
from orders.tests.test_staff_negotiation_case import StaffNegotiationCaseTests


@skipUnless(os.environ.get('XA_STAFF_CASE_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffNegotiationCaseBrowserTest(LiveServerTestCase):
    account = StaffNegotiationCaseTests.account
    client_for = StaffNegotiationCaseTests.client_for
    setUp = StaffNegotiationCaseTests.setUp

    def test_staff_opens_case_from_order_detail(self):
        opened = self.client_for(self.staff).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'affected_provider_account_ids':[self.a_account.pk],
            'reason':'老板提出协商终止服务', 'client_request_id':'staff-case-browser'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        case = AfterSale.objects.get(order=self.order)
        self.assertEqual((case.reason, case.affected_provider_account_ids, case.operator_id),
                         ('老板提出协商终止服务', [self.a_account.pk], self.staff.pk))


@skipUnless(os.environ.get('XA_STAFF_CASE_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ContinuingNegotiationCaseBrowserTest(LiveServerTestCase):
    from orders.tests.test_negotiation_termination import NegotiationTerminationTests as fixture
    account = fixture.account
    client_for = fixture.client_for
    case = fixture.case
    propose = fixture.propose
    accepted = fixture.accepted
    execute = fixture.execute
    setUp = fixture.setUp

    def test_staff_records_remaining_teammates_case_after_negotiated_refund(self):
        self.order.requires_boss_selection = True
        self.order.save(update_fields=['requires_boss_selection'])
        old_case, proposal = self.accepted()
        self.execute(old_case, proposal)
        role = self.operator.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']; role.save(update_fields=['permissions'])
        opened = self.client_for(self.operator_account).post('/api/admin/aftersales/open-negotiation/', {
            'order_id':self.order.pk, 'affected_provider_account_ids':[self.b_account.pk],
            'reason':'老板提出协商终止服务', 'client_request_id':'continuing-case-browser'}, format='json')
        self.assertEqual(opened.data['code'], 0, opened.data)
        old_case.refresh_from_db()
        self.assertEqual((old_case.status, old_case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        self.assertEqual(self.order.refund_records.count(), 1)
