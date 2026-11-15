"""Safety-box terms require each affected person's explicit consent."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import AfterSale, Order, OrderPlatformSubsidyShare
from orders.negotiation_proposals import create, decide, serialize
from orders.tests.factories import make_console_user
from orders.tests.test_safebox_settlement_clocks import SafeBoxSettlementFixture
from wallet.models import Transaction


class SubsidyAgreementFixture(SafeBoxSettlementFixture):
    def setUp(self):
        super().setUp()
        created = timezone.now()-timedelta(minutes=20)
        Order.objects.filter(pk=self.order.pk).update(status='IN_SERVICE', completed_at=None,
                                                       created_at=created, in_service_at=created+timedelta(minutes=10))
        self.order.refresh_from_db()
        from coupons.models import Coupon, UserCoupon
        coupon = Coupon.objects.create(name='安全箱资格', discount_type='RATE', amount=1, discount_rate=0,
            scope='SAFE_BOX', grant_key=f'vip-safe:{self.customer.pk}:{self.order.business_cycle}:test')
        UserCoupon.objects.create(pk=self.subsidy.coupon_id_snapshot, user=self.boss, account=self.customer,
            coupon=coupon, order=self.order, status='USED', used_at=created+timedelta(minutes=2),
            business_cycle=self.order.business_cycle)
        user = make_console_user(['support:edit'])
        self.staff = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = self.staff; user.admin_membership.save(update_fields=['account'])
        self.case = AfterSale.objects.create(order=self.order, account=self.customer, reason='协商终止',
            client_request_id='subsidy-case-1', affected_provider_account_ids=[self.accounts[1].pk])

    def propose(self, retained=2000, request_id='subsidy-proposal-1'):
        return create(self.case.pk, actor=self.staff, request_id=request_id, expected_version=0, refund_units=0,
            provider_terms=[{'account_id':self.accounts[1].pk, 'retained_units':retained}], explanation='核定已完成服务报酬')


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SubsidyAgreementTest(SubsidyAgreementFixture, TestCase):
    def test_confirmed_terms_are_versioned_and_never_move_money(self):
        proposal = self.propose()
        self.assertEqual(proposal.provider_terms, [{'account_id':self.accounts[1].pk, 'retained_units':2000,
            'original_income_units':4800, 'burden_units':2800, 'additional_units':0}])
        display = serialize(proposal, actor=self.staff, staff=True)
        self.assertEqual((display['calculation_kind'], display['rounding_to_store_units']), ('PLATFORM_SUBSIDY', 0))
        with self.assertRaises(ValidationError):
            decide(self.case.pk, actor=self.accounts[0], proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'PENDING')
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db(); self.assertEqual(proposal.status, 'ACCEPTED')
        self.assertEqual(self.propose().pk, proposal.pk)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.shares[1].refresh_from_db(); self.assertEqual(self.shares[1].amount, 4800)

    def test_retained_cannot_exceed_original_or_confirm_changed_amount(self):
        with self.assertRaises(ValidationError):
            self.propose(retained=4801)
        proposal = self.propose()
        OrderPlatformSubsidyShare.objects.filter(pk=self.shares[1].pk).update(amount=4700)
        with self.assertRaises(ValidationError):
            decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        self.assertFalse(proposal.decisions.filter(accepted=True).exists())

    def test_unused_or_wrong_coupon_cannot_create_agreement(self):
        from coupons.models import UserCoupon
        UserCoupon.objects.filter(pk=self.subsidy.coupon_id_snapshot).update(status='UNUSED')
        with self.assertRaises(ValidationError):
            self.propose()
        self.assertFalse(self.case.negotiation_proposals.exists())

    def test_execution_waits_for_both_confirmations_and_settles_once_after_clocks(self):
        from orders.subsidy_execution import execute
        from wallet.services import get_wallet
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose()
        with self.assertRaises(ValidationError):
            execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator, request_id='execution-1')
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        proposal.refresh_from_db()
        receipt = execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator, request_id='execution-1')
        self.assertEqual(execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator,
                                 request_id='execution-1').pk, receipt.pk)
        self.order.refresh_from_db(); self.case.refresh_from_db()
        self.shares[1].refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.order.completed_at,
                          self.case.status, self.shares[1].amount),
                         ('COMPLETED', 'PAID', proposal.accepted_at, 'RESOLVED', 2000))
        self.assertFalse(self.order.refund_records.exists())
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.settle(self.completed+timedelta(hours=71))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())
        self.settle(self.completed+timedelta(hours=83))
        self.settle(self.completed+timedelta(hours=84))
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertEqual([get_wallet(account=item).balance for item in self.accounts], [3200, 2000])
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 2)

    def test_failed_audit_rolls_back_order_case_and_subsidy(self):
        from orders.models import SubsidyAgreementExecution
        from orders.subsidy_execution import execute
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose()
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('审计失败')):
            with self.assertRaisesMessage(RuntimeError, '审计失败'):
                execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator, request_id='execution-rollback')
        self.order.refresh_from_db(); self.case.refresh_from_db(); self.shares[1].refresh_from_db()
        self.assertEqual((self.order.status, self.case.status, self.shares[1].amount), ('IN_SERVICE', 'OPEN', 4800))
        self.assertFalse(SubsidyAgreementExecution.objects.filter(order=self.order).exists())

    def test_tampered_agreement_cannot_release_subsidy(self):
        from orders.settlement import SettlementError
        from orders.subsidy_execution import execute
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose()
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator, request_id='execution-tamper')
        OrderPlatformSubsidyShare.objects.filter(pk=self.shares[1].pk).update(amount=2100)
        from wallet.models import ProviderReport
        from wallet.serializers import ReportSerializer
        report = ProviderReport.objects.get(order=self.order, provider_account=self.accounts[1])
        display = ReportSerializer(report).data
        self.assertEqual(display['settlement_status'], 'REVIEW')
        self.assertIsNone(display['payout_amount'])
        self.assertIsNone(display['expected_income'])
        with self.assertRaisesMessage(SettlementError, '协商凭证待核对'):
            self.settle(self.completed+timedelta(hours=83))
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())

    def test_completed_order_keeps_original_aftersale_start_when_agreement_is_executed(self):
        from orders.subsidy_execution import execute
        original_completion = timezone.now()-timedelta(minutes=2)
        Order.objects.filter(pk=self.order.pk).update(status='COMPLETED', completed_at=original_completion)
        self.order.refresh_from_db()
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose()
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        receipt = execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator,
                          request_id='completed-case-execution')
        self.order.refresh_from_db()
        self.assertTrue(receipt.completed_before_agreement)
        self.assertEqual((receipt.completion_at, self.order.completed_at), (original_completion, original_completion))

    def test_zero_retained_subsidy_closes_without_fabricating_a_payment(self):
        from orders.subsidy_execution import execute
        from wallet.services import get_wallet
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose(retained=0)
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator, request_id='execution-zero')
        self.settle(self.completed+timedelta(hours=83))
        self.order.refresh_from_db(); self.shares[1].refresh_from_db()
        self.assertIsNotNone(self.order.settled_at)
        self.assertIsNotNone(self.shares[1].paid_at)
        self.assertEqual((self.shares[1].amount, get_wallet(account=self.accounts[1]).balance), (0, 0))
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').count(), 1)


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnlessDBFeature('has_select_for_update')
class SubsidyAgreementRaceTest(SubsidyAgreementFixture, TransactionTestCase):
    def test_simultaneous_same_execution_commits_one_receipt_and_one_audit(self):
        from console.models import AdminAuditLog
        from orders.models import SubsidyAgreementExecution
        from orders.subsidy_execution import execute
        operator = make_console_user(['order:refund'])
        account = get_or_create_account_for_legacy_user(operator)
        operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
        proposal = self.propose()
        decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
        decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
        gate = Barrier(2)
        def run():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return execute(self.case.pk, proposal.pk, operator=account, legacy_operator=operator,
                               request_id='subsidy-race-execution').pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _:run(), (0, 1)))
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(SubsidyAgreementExecution.objects.filter(order=self.order).count(), 1)
        self.assertEqual(AdminAuditLog.objects.filter(path=f'/api/admin/aftersales/{self.case.pk}/subsidy-negotiation-execution/').count(), 1)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='SUBSIDY_EXPENSE').exists())


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'],
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SubsidyAgreementStoresTest(SubsidyAgreementFixture, TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def setUp(self):
        # Each tenant is initialized explicitly below.
        pass

    def test_same_order_and_case_ids_execute_only_in_authenticated_store(self):
        from club_accounts.tokens import issue_account_tokens
        from orders.models import SubsidyAgreementExecution
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                SubsidyAgreementFixture.setUp(self)
                operator = make_console_user(['order:view', 'order:refund'])
                account = get_or_create_account_for_legacy_user(operator)
                operator.admin_membership.account = account; operator.admin_membership.save(update_fields=['account'])
                proposal = self.propose()
                decide(self.case.pk, actor=self.customer, proposal_id=proposal.pk, accepted=True)
                decide(self.case.pk, actor=self.accounts[1], proposal_id=proposal.pk, accepted=True)
                entries.append((store, self.order.pk, self.case.pk, proposal.pk, issue_account_tokens(account)[0]))
        self.assertEqual(entries[0][1:4], entries[1][1:4])
        first, second = entries
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+first[4])
        result = client.post(f'/api/admin/aftersales/{first[2]}/subsidy-negotiation-execution/',
                             {'proposal_id':first[3], 'client_request_id':'tenant-subsidy-exec'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(first[0]):
            self.assertEqual(Order.objects.get(pk=first[1]).status, 'COMPLETED')
            self.assertEqual(SubsidyAgreementExecution.objects.filter(order_id=first[1]).count(), 1)
        with store_context(second[0]):
            self.assertEqual(Order.objects.get(pk=second[1]).status, 'IN_SERVICE')
            self.assertFalse(SubsidyAgreementExecution.objects.filter(order_id=second[1]).exists())


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_NEGOTIATION_ENABLED=True,
                   NEGOTIATION_PROPOSAL_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SubsidyAgreementApiTest(APITestCase):
    from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest as _flow
    setUp = _flow.setUp
    client_for = staticmethod(_flow.client_for)
    ok = _flow.ok

    def test_three_roles_confirm_execute_report_and_delayed_settlement(self):
        from coupons.benefits import grant_periodic
        from coupons.models import UserCoupon
        from engagement.models import VipTier
        from engagement.tasks import retry_completion_subsidies
        from orders.tests.factories import make_boss_type, make_provider, make_service
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, get_platform_wallet
        from wallet.services import get_wallet
        service = make_service(name='安全箱协商', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        tier = make_boss_type(name='安全箱协商会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view','order:dispatch','support:edit','order:refund','report:view','report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        player = make_provider(deposit_mode='CASH', deposit_paid=40000)
        player_account = get_or_create_account_for_legacy_user(player)
        player.escort_profile.account = player_account; player.escort_profile.save(update_fields=['account'])
        player.escort_profile.service_items.add(service)
        platform = get_platform_wallet(); platform.balance = 10000; platform.save(update_fields=['balance'])
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'subsidy-agreement-api'}, format='json'))
        operator, provider = self.client_for(staff_account), self.client_for(player_account)
        self.ok(operator.post(f"/api/admin/orders/{created['id']}/assign/", {'provider_id':player_account.pk}, format='json'))
        self.ok(provider.post(f"/api/orders/orders/{created['id']}/start/", {'entry_image':screenshot()}, format='multipart'))
        case = self.ok(operator.post('/api/admin/aftersales/open-negotiation/', {'order_id':created['id'],
            'reason':'老板请求协商终止', 'affected_provider_account_ids':[player_account.pk],
            'client_request_id':'subsidy-case-api'}, format='json'))
        base = f"/api/admin/aftersales/{case['id']}"
        preview = self.ok(operator.get(base+'/negotiation-preview/', {'refund_units':0}))
        self.assertEqual((preview['calculation_kind'], preview['provider_terms'][0]['original_income_units']),
                         ('PLATFORM_SUBSIDY', 8000))
        proposed = self.ok(operator.post(base+'/negotiation-proposal/', {'request_id':'subsidy-proposal-api',
            'expected_version':0, 'refund_units':0,
            'provider_terms':[{'account_id':player_account.pk,'retained_units':3000}],
            'explanation':'按完成内容核定报酬'}, format='json'))
        action = base+'/subsidy-negotiation-execution/'
        payload = {'proposal_id':proposed['id'], 'client_request_id':'subsidy-execution-api'}
        self.assertNotEqual(operator.post(action, payload, format='json').data.get('code', 400), 0)
        for client in (self.boss, provider):
            result = self.ok(client.post(f"/api/orders/orders/{created['id']}/negotiation-proposal/",
                                         {'proposal_id':proposed['id'], 'accepted':True}, format='json'))
        self.assertEqual(result['status'], 'ACCEPTED')
        receipt = self.ok(operator.post(action, payload, format='json'))
        self.assertEqual(self.ok(operator.post(action, payload, format='json'))['execution_id'], receipt['execution_id'])
        self.assertEqual(self.ok(operator.get(action, payload))['execution_id'], receipt['execution_id'])
        with override_settings(VIP_SAFE_BOX_NEGOTIATION_ENABLED=False, NEGOTIATION_PROPOSAL_ENABLED=False):
            self.assertEqual(self.ok(operator.get(action, payload))['execution_id'], receipt['execution_id'])
        order = Order.objects.get(pk=created['id'])
        self.assertEqual((order.status, order.payment_status), ('COMPLETED', 'PAID'))
        voucher.refresh_from_db(); self.wallet.refresh_from_db()
        self.assertEqual((voucher.status, self.wallet.balance), ('USED', 50000))
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').exists())
        reopened = self.boss.post(f'/api/orders/orders/{order.pk}/aftersale/', {'reason':'再次售后',
            'affected_provider_account_ids':[player_account.pk], 'client_request_id':'subsidy-reopen'}, format='json')
        self.assertNotEqual(reopened.data.get('code', 400), 0)
        self.ok(self.boss.post('/api/orders/evaluate/', {'order_id':order.pk, 'provider_account_id':player_account.pk,
            'score':1, 'skill_score':5, 'attitude_score':5, 'communication_score':5}, format='json'))
        from orders.models import Evaluation
        rating = Evaluation.objects.get(order=order, provider_account=player_account)
        self.assertEqual(rating.low_rating_aftersale_state, 'CLOSED')
        self.assertEqual(AfterSale.objects.get(order=order).status, 'RESOLVED')
        report = ProviderReport.objects.get(order=order, provider=player)
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'COMPLETION','image':screenshot()}, format='multipart'))
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT','image':screenshot()}, format='multipart'))
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        order.refresh_from_db(); report.refresh_from_db()
        boundary = max(order.completed_at+timedelta(hours=order.service_aftersale_hours),
                       report.submitted_at+timedelta(hours=order.service_freeze_hours))
        with patch('django.utils.timezone.now', return_value=boundary):
            self.assertEqual(retry_completion_subsidies.run(), 1)
            self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertEqual(get_wallet(account=player_account).balance, 3000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)
