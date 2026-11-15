"""Legacy refund endpoints must not bypass the current agreement workflow."""
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from orders.tests import test_negotiated_transfer_refund as old
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_order, make_provider
from wallet.models import Transaction, Wallet


@override_settings(ORDER_SETTLEMENT_MODE='COMPLETION', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacyNegotiationGuardTest(APITestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    prepare_transfer = old.NegotiatedTransferRefundTest.prepare_transfer

    def test_old_transfer_preview_and_execution_cannot_bypass_participant_agreement(self):
        with patch('orders.tests.test_negotiated_transfer_refund.make_provider',
                   side_effect=lambda: make_provider(deposit_paid=40000)):
            order, incoming = self.prepare_transfer()
        base = f'/api/admin/orders/{order.pk}/'
        rows = self.ok(self.client.get(base + 'negotiated-refund-options/'))
        allocation = [{'key':row['key'], 'amount':1000 if row['role'] == 'PLATFORM' or
                       row['account_id'] == self.accounts[self.provider.pk].pk else 3000} for row in rows]
        payload = {'refund_amount':5000, 'allocations':allocation, 'reason':'旧转单协商',
                   'client_request_id':'legacy-agreement-guard', 'reviewed':True}
        before_wallets=list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        before_tx=list(Transaction.objects.filter(order=order).order_by('pk').values())
        preview=self.ok(self.client.post(base + 'negotiated-refund-preview/', payload, format='json'))
        self.assertFalse(preview['eligible'])
        self.assertTrue(preview['funds_eligible'])
        self.assertEqual(preview['execution_block_code'], 'LEGACY_NEGOTIATION_AGREEMENT_REQUIRED')
        for token in (preview['plan_token'], 'obsolete-legacy-plan'):
            result=self.client.post(base + 'negotiated-refund/', {**payload, 'plan_token':token}, format='json')
            self.assertEqual(result.data['errorCode'], 'LEGACY_NEGOTIATION_AGREEMENT_REQUIRED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('IN_SERVICE', 'PAID'))
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_wallets)
        self.assertEqual(list(Transaction.objects.filter(order=order).order_by('pk').values()), before_tx)

    @override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True)
    def test_old_subsidy_preview_does_not_authorize_unconfirmed_termination(self):
        from django.utils import timezone
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare, OrderProvider
        order=make_order(self.boss, service=self.service, provider=self.provider, amount=0,
            status='IN_SERVICE', payment_status='PAID', in_service_at=timezone.now(),
            customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk])
        assignment=OrderProvider.objects.create(order=order, provider=self.provider,
            provider_account=self.accounts[self.provider.pk], settlement_base=0, provider_income=0)
        subsidy=OrderPlatformSubsidy.objects.create(order=order, coupon_id_snapshot=1, basis=10000)
        share=OrderPlatformSubsidyShare.objects.create(subsidy=subsidy, assignment=assignment, amount=8000)
        self.login(self.admin)
        base=f'/api/admin/orders/{order.pk}/'
        allocation=[{'share_id':share.pk, 'amount':6000}]
        preview=self.ok(self.client.post(base+'subsidy-negotiation-preview/', {'allocations':allocation}, format='json'))
        self.assertFalse(preview['execution_available'])
        self.assertEqual(preview['execution_block_code'], 'SUBSIDY_NEGOTIATION_AGREEMENT_REQUIRED')
        before_wallets=list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        before_tx=list(Transaction.objects.order_by('pk').values())
        result=self.client.post(base+'subsidy-negotiation-confirm/', {
            'allocations':allocation, 'plan_token':preview['plan_token'], 'reason':'旧安全箱协商',
            'client_request_id':'subsidy-agreement-guard', 'reviewed':True}, format='json')
        self.assertEqual(result.data['errorCode'], 'SUBSIDY_NEGOTIATION_AGREEMENT_REQUIRED')
        order.refresh_from_db(); share.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, share.amount), ('IN_SERVICE', 'PAID', 8000))
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_wallets)
        self.assertEqual(list(Transaction.objects.order_by('pk').values()), before_tx)

    def test_completed_legacy_requests_remain_idempotent_without_reposting(self):
        from orders.models import OrderRefund
        from orders.negotiated_refunds import apply as apply_transfer, _intent
        from orders.refunds import request_fingerprint
        from orders.subsidy_negotiation import apply as apply_subsidy
        order=make_order(self.boss, service=self.service, provider=self.provider, amount=10000,
            status='CANCELLED', payment_status='PARTIALLY_REFUNDED',
            customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk])
        allocation=[{'key':'provider:42', 'amount':1000}]
        reason='历史协商退款'
        OrderRefund.objects.create(order=order, amount=1000, reason=reason,
            client_request_id='previous-refund', request_fingerprint=request_fingerprint(1000, reason, _intent(allocation)))
        before=list(Transaction.objects.order_by('pk').values())
        result=apply_transfer(order.pk, amount=1000, allocation=allocation, reason=reason,
            request_id='previous-refund', plan_token='', operator=self.admin, operator_account=self.accounts[self.admin.pk])
        self.assertTrue(result._refund_replayed)
        self.assertEqual(order.refund_records.count(), 1)
        self.assertEqual(list(Transaction.objects.order_by('pk').values()), before)
        subsidy_order=make_order(self.boss, service=self.service, provider=self.provider, amount=0,
            status='CANCELLED', payment_status='REFUNDED',
            customer_account=self.accounts[self.boss.pk], provider_account=self.accounts[self.provider.pk])
        subsidy_allocation=[{'share_id':12, 'amount':6000}]
        OrderRefund.objects.create(order=subsidy_order, amount=0, reason=reason,
            client_request_id='previous-subsidy', benefit_effects={'subsidy_negotiated':True},
            request_fingerprint=request_fingerprint(0, reason, subsidy_allocation))
        result=apply_subsidy(subsidy_order.pk, subsidy_allocation, reason=reason,
            request_id='previous-subsidy', plan_token='', operator=self.admin, operator_account=self.accounts[self.admin.pk])
        self.assertTrue(result._refund_replayed)
        self.assertEqual(subsidy_order.refund_records.count(), 1)
        self.assertEqual(list(Transaction.objects.order_by('pk').values()), before)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacyNegotiationGuardStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_subsidy_guard_uses_the_selected_store_without_posting_money(self):
        from django.utils import timezone
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import OrderPlatformSubsidy, OrderPlatformSubsidyShare, OrderProvider
        from orders.refunds import RefundError
        from orders.subsidy_negotiation import apply, preview
        from orders.tests.factories import make_console_user, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        rows=[]
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss, player, staff = make_user(), make_provider(), make_console_user(['order:view', 'order:refund'])
                customer, provider, operator = [get_or_create_account_for_legacy_user(user) for user in (boss, player, staff)]
                staff.admin_membership.account=operator; staff.admin_membership.save(update_fields=['account'])
                order=make_order(boss, service=make_service(), provider=player, amount=0, status='IN_SERVICE',
                    payment_status='PAID', in_service_at=timezone.now(), customer_account=customer, provider_account=provider)
                assignment=OrderProvider.objects.create(order=order, provider=player, provider_account=provider,
                                                        settlement_base=0, provider_income=0)
                subsidy=OrderPlatformSubsidy.objects.create(order=order, coupon_id_snapshot=1, basis=10000)
                share=OrderPlatformSubsidyShare.objects.create(subsidy=subsidy, assignment=assignment, amount=8000)
                rows.append((store, order.pk, share.pk, staff, operator))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        for store, pk, share_id, staff, operator in rows:
            with store_context(store):
                order=type(order).objects.get(pk=pk)
                allocation=[{'share_id':share_id, 'amount':6000}]
                before=list(Transaction.objects.order_by('pk').values())
                details=preview(order, allocation)
                self.assertFalse(details['execution_available'])
                with self.assertRaises(RefundError) as denied:
                    apply(pk, allocation, reason='门店协商核对', request_id='same-request',
                          plan_token=details['plan_token'], operator=staff, operator_account=operator)
                self.assertEqual(denied.exception.code, 'SUBSIDY_NEGOTIATION_AGREEMENT_REQUIRED')
                self.assertFalse(order.refund_records.exists())
                self.assertEqual(list(Transaction.objects.order_by('pk').values()), before)
