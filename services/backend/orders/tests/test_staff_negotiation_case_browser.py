"""Actual admin UI against isolated ASGI and SQLite; no external messages."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.tokens import issue_account_tokens
from orders.models import AfterSale
from orders.tests.test_staff_negotiation_case import StaffNegotiationCaseTests
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_STAFF_CASE_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffNegotiationCaseBrowserTest(LiveServerTestCase):
    account = StaffNegotiationCaseTests.account
    client_for = StaffNegotiationCaseTests.client_for
    setUp = StaffNegotiationCaseTests.setUp

    def test_staff_opens_case_from_order_detail(self):
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/staff-negotiation-case-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'order':self.order.pk, 'provider':self.a.nickname,
                    'staff':{'id':self.staff.pk, 'token':issue_account_tokens(self.staff)[0],
                             'permissions':['order:view', 'support:edit']}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
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
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/staff-negotiation-case-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'order':self.order.pk, 'provider':self.b.nickname,
                    'absentProvider':self.a.nickname, 'continuing':True, 'funding':True,
                    'staff':{'id':self.operator_account.pk, 'token':issue_account_tokens(self.operator_account)[0],
                             'permissions':['order:view', 'support:edit', 'order:refund']}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        old_case.refresh_from_db()
        self.assertEqual((old_case.status, old_case.affected_provider_account_ids), ('OPEN', [self.b_account.pk]))
        self.assertEqual(self.order.refund_records.count(), 1)
