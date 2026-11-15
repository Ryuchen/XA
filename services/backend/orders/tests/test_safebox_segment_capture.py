"""Safety-box funding basis and provider entry rules are recorded at assignment."""
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.models import UserCoupon
from coupons import test_coupon_availability_workflow as coupon_workflow
from orders.tests import test_game_commission_payment as game_workflow
from orders.models import Order, OrderPlatformSubsidyShare, OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   VIP_SAFE_BOX_NEGOTIATION_ENABLED=True, NEGOTIATION_PROPOSAL_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SafeBoxSegmentCaptureTest(TestCase):
    setUp = coupon_workflow.CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(coupon_workflow.CouponAvailabilityWorkflowTest.client_for)
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok

    def prepare(self, *, fixed=None):
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        service = make_service(name='分段安全箱', price=10000, benefit_kind='SAFEBOX_33', platform='PC', commission_rate=20)
        if fixed is not None:
            service.commission_override = True; service.commission_type = 'FIXED'; service.commission_fixed = fixed
            service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        tier = make_boss_type(name='安全箱会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier; self.account.save(update_fields=['boss_type']); grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'support:edit', 'order:refund', 'report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        players = []
        for name in ('原陪玩', '接替陪玩'):
            user = make_provider(display_name=name, deposit_paid=40000)
            account = get_or_create_account_for_legacy_user(user)
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service); players.append(account)
        created = self.ok(self.boss.post('/api/orders/create/', {'service_id':service.pk, 'user_coupon_id':voucher.pk,
            'dispatch_route':'STAFF', 'client_request_id':'safebox-segment'}, format='json'))
        return service, players, self.client_for(staff_account), created['id']

    def test_assignment_saves_funded_basis_and_exact_entry_commission(self):
        _, players, staff, order_id = self.prepare()
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order_id=order_id)
        self.assertEqual((coverage.source, coverage.deal_amount, coverage.status), ('PLATFORM_SUBSIDY', 10000, 'PENDING'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        coverage.refresh_from_db()
        share = OrderPlatformSubsidyShare.objects.get(subsidy__order_id=order_id)
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution=coverage.attribution)
        self.assertEqual((coverage.status, snapshot.incoming_basis, snapshot.income_at_entry), ('ACTIVE', 10000, share.amount))
        self.assertEqual((snapshot.commission_type, snapshot.commission_rate, snapshot.income_at_entry), ('PERCENT', 20, 8000))

    def test_prestart_reassignment_records_new_rule_without_reusing_old_income(self):
        service, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        service.commission_rate = 30; service.save(update_fields=['commission_rate'])
        result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk}]}, format='json')
        self.ok(result)
        coverage = OrderSegmentFinancialCoverage.objects.get(attribution__order_id=order_id)
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution=coverage.attribution).order_by('pk'))
        self.assertEqual(coverage.status, 'ACTIVE')
        self.assertEqual([(s.segment.provider_account_id, s.incoming_basis, s.retained_basis, s.income_at_entry)
                          for s in snapshots], [(players[0].pk, 10000, 0, 8000), (players[1].pk, 10000, None, 7000)])
        self.assertEqual(list(OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).order_by('pk')
                              .values_list('amount', flat=True)), [0, 7000])

    def test_changed_old_snapshot_rolls_back_prestart_handoff(self):
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order_id=order_id)
        OrderSegmentFinancialSnapshot.objects.filter(pk=snapshot.pk).update(income_at_entry=7900)
        result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk}]}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.provider_account_id, players[0].pk)
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(subsidy__order=order).amount, 8000)
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).count(), 1)

    def test_missing_assignment_snapshot_rolls_back_assignment(self):
        from orders.services import sync_single_provider_share
        _, players, staff, order_id = self.prepare()
        def discard_snapshot(order):
            sync_single_provider_share(order)
            del order._subsidy_entry_terms
        with patch('console.views.sync_single_provider_share', side_effect=discard_snapshot):
            result = staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, 'PENDING')
        self.assertFalse(order.providers.exists())

    def test_tampered_funded_amount_cannot_be_used_as_original_entitlement(self):
        from orders.settlement import SettlementError
        from orders.subsidies import _locked_subsidy_shares
        from stores.db import transaction
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).update(amount=8500)
        with transaction.atomic():
            with self.assertRaisesMessage(SettlementError, '预期补贴与接单抽成不一致'):
                _locked_subsidy_shares(Order.objects.select_for_update().get(pk=order_id))

    def test_valid_prestart_snapshots_reconcile_against_funded_shares(self):
        from orders.subsidies import _locked_subsidy_shares
        from stores.db import transaction
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk}]}, format='json'))
        with transaction.atomic():
            shares = _locked_subsidy_shares(Order.objects.select_for_update().get(pk=order_id))
        self.assertEqual([s.amount for s in shares], [0, 8000])

    def test_paused_capture_cannot_reassign_an_already_captured_order(self):
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        with override_settings(VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=False, ORIGINAL_ATTRIBUTION_ENABLED=False,
                               SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=False):
            result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
                {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk}]}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, players[0].pk)

    def test_captured_in_service_transfer_does_not_use_legacy_share_split(self):
        from orders.tests.test_release_flow import screenshot
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':50}]}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, players[0].pk)
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(subsidy__order_id=order_id).amount, 8000)

    @override_settings(VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=False, SEGMENT_STANDARD_TRANSFER_ENABLED=False,
                       SEGMENT_ECONOMICS_CAPTURE_ENABLED=False, SEGMENT_ECONOMICS_ENFORCE_ENABLED=False)
    def test_legacy_subsidy_cannot_use_unverified_manual_income_split(self):
        from orders.tests.test_release_flow import screenshot
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':50}]}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, players[0].pk)
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(subsidy__order_id=order_id).amount, 8000)

    @override_settings(VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_in_service_transfer_uses_original_share_and_each_entry_commission(self):
        from orders.models import OrderOriginalShareTransfer, OrderSegmentTransferBasis
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction
        service, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        service.commission_rate = 30; service.save(update_fields=['commission_rate'])
        result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json')
        self.ok(result)
        shares = list(OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id)
                      .order_by('pk').values_list('amount', 'retained_amount'))
        self.assertEqual(shares, [(3200, 3200), (4200, 0)])
        event = OrderOriginalShareTransfer.objects.get(previous_segment__attribution__order_id=order_id)
        basis = OrderSegmentTransferBasis.objects.get(transfer=event)
        self.assertEqual((basis.retained_basis, basis.transferred_basis), (4000, 6000))
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=order_id)
                         .order_by('segment_id'))
        self.assertEqual([(s.commission_rate, s.income_at_entry) for s in snapshots], [(20, 8000), (30, 4200)])
        incoming = event.next_segment
        self.ok(self.client_for(players[1]).post(f'/api/orders/orders/{order_id}/participation-start/',
            {'segment_id':incoming.pk, 'entry_image':screenshot()}, format='multipart'))
        previous = event.previous_segment
        old_client = self.client_for(players[0])
        self.ok(old_client.post(f'/api/orders/orders/{order_id}/participation-evidence/',
            {'segment_id':previous.pk, 'completion_image':screenshot()}, format='multipart'))
        path = f'/api/orders/orders/{order_id}/segment-report/'
        denied = self.client_for(players[1]).post(path, {'segment_id':previous.pk, 'action':'open',
                                                        'client_request_id':'other-person'}, format='json')
        self.assertEqual(denied.status_code, 404)
        self.ok(old_client.post(path, {'segment_id':previous.pk, 'action':'open', 'client_request_id':'old-open'}, format='json'))
        self.ok(old_client.post(path, {'segment_id':previous.pk, 'action':'upload', 'client_request_id':'old-upload',
                                      'image':screenshot()}, format='multipart'))
        self.ok(old_client.post(path, {'segment_id':previous.pk, 'action':'submit', 'version':0,
                                      'client_request_id':'old-submit'}, format='json'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/segments/{previous.pk}/report-review/',
                           {'action':'approve', 'version':1}, format='json'))
        from orders.models import OrderSegmentReport
        self.assertEqual(OrderSegmentReport.objects.get(segment=previous).status, 'APPROVED')
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转回', 'transfers':[
            {'old_provider_id':players[1].pk, 'new_provider_id':players[0].pk, 'split_value':0,
             'original_share':{'retained':2000, 'transferred':4000, 'agreement':'双方确认再次分配'}}]}, format='json'))
        shares = {row.assignment.provider_account_id:(row.amount, row.retained_amount)
                  for row in OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).select_related('assignment')}
        self.assertEqual(shares, {players[0].pk:(6000, 3200), players[1].pk:(1400, 1400)})
        self.assertEqual(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=order_id).count(), 2)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='SUBSIDY_EXPENSE').exists())

    @override_settings(VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_fixed_commission_preserves_seat_rounding_on_transfer(self):
        from orders.tests.test_release_flow import screenshot
        _, players, staff, order_id = self.prepare(fixed=2003)
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json'))
        shares = list(OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).order_by('pk')
                      .values_list('amount', 'retained_amount'))
        snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=order_id)
                         .order_by('segment_id').values_list('commission_type', 'commission_fixed', 'fixed_rule_total', 'income_at_entry'))
        self.assertEqual(shares, [(3199, 3199), (4798, 0)], snapshots)

    @override_settings(VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_changed_fixed_commission_uses_each_entry_rule_and_ceil_for_replacement(self):
        from orders.tests.test_release_flow import screenshot
        service, players, staff, order_id = self.prepare(fixed=2003)
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        service.commission_fixed = 3001; service.save(update_fields=['commission_fixed'])
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json'))
        shares = list(OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).order_by('pk')
                      .values_list('amount', 'retained_amount'))
        self.assertEqual(shares, [(3199, 3199), (4199, 0)])

    @override_settings(VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_failed_transfer_reconciliation_rolls_back_all_mutations(self):
        from orders.settlement import SettlementError
        from orders.models import OrderOriginalShareTransfer
        from orders.tests.test_release_flow import screenshot
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                {'entry_image':screenshot()}, format='multipart'))
        with patch('orders.subsidy_transfer_capture.transfer_amounts', side_effect=SettlementError('凭证异常')):
            result = staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
                {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk, 'split_value':0,
                 'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json')
        self.assertNotEqual(result.data.get('code'), 0)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.provider_account_id, players[0].pk)
        self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())
        self.assertEqual(OrderPlatformSubsidyShare.objects.get(subsidy__order=order).amount, 8000)
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).count(), 1)

    @override_settings(REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_reassigned_order_can_negotiate_and_settle_against_snapshots(self):
        from datetime import timedelta
        from django.utils import timezone
        from engagement.tasks import retry_completion_subsidies
        from orders.tests.test_release_flow import screenshot
        from wallet.models import ProviderReport, Transaction, get_platform_wallet
        from wallet.services import get_wallet
        _, players, staff, order_id = self.prepare()
        self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
        self.ok(staff.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'更换陪玩', 'transfers':[
            {'old_provider_id':players[0].pk, 'new_provider_id':players[1].pk}]}, format='json'))
        provider = self.client_for(players[1])
        self.ok(provider.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart'))
        case = self.ok(staff.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'reason':'老板请求协商终止', 'affected_provider_account_ids':[players[1].pk],
            'client_request_id':'segment-case'}, format='json'))
        base = f"/api/admin/aftersales/{case['id']}"
        proposal = self.ok(staff.post(base+'/negotiation-proposal/', {'request_id':'segment-proposal',
            'expected_version':0, 'refund_units':0, 'provider_terms':[{'account_id':players[1].pk,'retained_units':3000}],
            'explanation':'按完成内容核定报酬'}, format='json'))
        for client in (self.boss, provider):
            self.ok(client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                                {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
        self.ok(staff.post(base+'/subsidy-negotiation-execution/', {'proposal_id':proposal['id'],
                     'client_request_id':'segment-execution'}, format='json'))
        report = ProviderReport.objects.get(order_id=order_id, provider_account=players[1])
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'COMPLETION','image':screenshot()}, format='multipart'))
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/images/', {'kind':'RESULT','image':screenshot()}, format='multipart'))
        self.ok(provider.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.ok(staff.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        platform = get_platform_wallet(); platform.balance = 3000; platform.save(update_fields=['balance'])
        order = Order.objects.get(pk=order_id); report.refresh_from_db()
        deadline = max(order.completed_at+timedelta(hours=order.service_aftersale_hours),
                       report.submitted_at+timedelta(hours=order.service_freeze_hours))
        with patch('django.utils.timezone.now', return_value=deadline):
            self.assertEqual(retry_completion_subsidies.run(), 1)
            self.assertEqual(retry_completion_subsidies.run(), 0)
        self.assertEqual(get_wallet(account=players[1]).balance, 3000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='SUBSIDY_EXPENSE').count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SafeBoxSegmentCaptureStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    prepare = SafeBoxSegmentCaptureTest.prepare
    ok = coupon_workflow.CouponAvailabilityWorkflowTest.ok

    @staticmethod
    def client_for(account):
        from club_accounts.tokens import issue_account_tokens
        from rest_framework.test import APIClient
        from stores.context import current_store
        host = ('admin.esports.club' if account.account_type == 'STAFF' else
                'xa.esports.club' if current_store().slug == 'xinan' else 'sl.esports.club')
        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])

    def test_assignment_only_records_snapshot_in_authenticated_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                _, players, staff, order_id = self.prepare()
                entries.append((store, players[0].pk, staff, order_id))
        first, second = entries
        self.assertEqual((first[1], first[3]), (second[1], second[3]))
        self.ok(first[2].post(f'/api/admin/orders/{first[3]}/assign/', {'provider_id':first[1]}, format='json'))
        with store_context(first[0]):
            self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order_id=first[3]).status, 'ACTIVE')
            self.assertEqual(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=first[3]).count(), 1)
        with store_context(second[0]):
            self.assertEqual(OrderSegmentFinancialCoverage.objects.get(attribution__order_id=second[3]).status, 'PENDING')
            self.assertFalse(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=second[3]).exists())

    @override_settings(VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, SEGMENT_STANDARD_TRANSFER_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True)
    def test_transfer_does_not_change_same_numbered_order_in_other_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from orders.tests.test_release_flow import screenshot
        from orders.models import OrderOriginalShareTransfer
        entries = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                coupon_workflow.CouponAvailabilityWorkflowTest.setUp(self)
                _, players, staff, order_id = self.prepare()
                self.ok(staff.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id':players[0].pk}, format='json'))
                self.ok(self.client_for(players[0]).post(f'/api/orders/orders/{order_id}/start/',
                                                        {'entry_image':screenshot()}, format='multipart'))
                entries.append((store, [p.pk for p in players], staff, order_id))
        first, second = entries
        self.assertEqual((first[1], first[3]), (second[1], second[3]))
        result = first[2].post(f'/api/admin/orders/{first[3]}/transfer/', {'reason':'双方协商转单', 'transfers':[
            {'old_provider_id':first[1][0], 'new_provider_id':first[1][1], 'split_value':0,
             'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认原价份额'}}]}, format='json')
        self.ok(result)
        with store_context(first[0]):
            self.assertEqual(Order.objects.get(pk=first[3]).provider_account_id, first[1][1])
            self.assertEqual(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=first[3]).count(), 1)
        with store_context(second[0]):
            self.assertEqual(Order.objects.get(pk=second[3]).provider_account_id, second[1][0])
            self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=second[3]).exists())


@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, VIP_SAFE_BOX_SEGMENT_CAPTURE_ENABLED=True,
                   VIP_SAFE_BOX_SEGMENT_TRANSFER_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, SEGMENT_GAME_TRANSFER_ENABLED=True,
                   SEGMENT_REPORT_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True, GAME_COMMISSION_PAYMENT_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class SafeBoxGameTransferTest(APITestCase):
    setUp = game_workflow.GameCommissionPaymentTest.setUp
    link = game_workflow.GameCommissionPaymentTest.link
    auth = game_workflow.GameCommissionPaymentTest.auth

    def test_game_rule_is_snapshotted_at_each_transfer_entry(self):
        from tempfile import TemporaryDirectory
        from coupons.benefits import grant_periodic
        from engagement.models import VipTier
        from orders.tests.test_release_flow import screenshot
        from wallet.models import Transaction
        self.service.benefit_kind = 'SAFEBOX_33'; self.service.platform = 'PC'
        self.service.save(update_fields=['benefit_kind', 'platform'])
        tier = make_boss_type(name='游戏安全箱会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[self.service.pk])
        self.boss_account.boss_type = tier; self.boss_account.save(update_fields=['boss_type'])
        grant_periodic(self.boss_account)
        coupon = UserCoupon.objects.get(account=self.boss_account, coupon__scope='SAFE_BOX')
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, REQUIRE_ORDER_EVIDENCE_IMAGES=True):
            self.auth(self.boss_account)
            created = self.client.post('/api/orders/create/', {'service_id':self.service.pk, 'user_coupon_id':coupon.pk,
                'provider_id':self.accounts[0].pk, 'client_request_id':'game-safe-box'}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            order_id = created.data['data']['id']
            self.auth(self.accounts[0])
            started = self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            self.config.rules = {'companion':11, 'grind':50}; self.config.version = 2; self.config.save()
            staff = make_console_user(['order:view', 'order:dispatch'])
            account = get_or_create_account_for_legacy_user(staff)
            staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
            self.auth(account)
            result = self.client.post(f'/api/admin/orders/{order_id}/transfer/', {'reason':'双方协商转单', 'transfers':[
                {'old_provider_id':self.accounts[0].pk, 'new_provider_id':self.accounts[1].pk, 'split_value':0,
                 'original_share':{'retained':2000, 'transferred':3000, 'agreement':'双方确认原价份额'}}]}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order_id=order_id)
                             .order_by('segment_id'))
            self.assertEqual([(s.commission_rate, s.game_commission_snapshot['version']) for s in snapshots], [(30, 1), (50, 2)])
            self.assertEqual(list(OrderPlatformSubsidyShare.objects.filter(subsidy__order_id=order_id).order_by('pk')
                                  .values_list('amount', flat=True)), [1400, 1500])
            self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='SUBSIDY_EXPENSE').exists())
