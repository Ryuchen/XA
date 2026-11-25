from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from orders.tests import test_release_flow as flow
from orders.tests import test_ordinary_refund_without_addon_funding as prior


@override_settings(**prior.OrdinaryRefundWithoutAddonFundingTest._overridden_settings)
@override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=False,
    SERVICE_ORDINARY_FUNDING_TESTING=False, SERVICE_POINTS_FUNDING_TESTING=False,
    ADDON_PAYMENT_INTERNAL_TESTING=False, ADDON_ORDER_FUNDING_INTERNAL_TESTING=False,
    ADDON_SETTLEMENT_INTERNAL_TESTING=False, ADDON_REFUND_EXECUTION_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False,
    ADDON_COMPLETION_INTERNAL_TESTING=False, ADDON_HANDOVER_INTERNAL_TESTING=False)
class SingleStackedAddonRefundTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def scenario(self, amount, *, deposit_case=False, wallet_case=False, double=False):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.models import (GameCategory, Order, OrderAddonConsumption, OrderAddonFundingReleaseRun, OrderAddonRefund,
            ServiceAddonConfiguration, ServiceAddonPriceOption)
        from orders.tests.factories import make_provider, make_service, make_user
        from stores.context import current_store, resolve_store, store_context
        from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
        from wallet.models import ProviderReport
        from wallet.services import get_wallet
        from engagement.confirmation import process_order

        owner = self.accounts[self.provider.pk]
        second = None
        if double:
            second = make_provider(deposit_paid=40000, wechat_avatar_legacy_exempt=True)
            self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
            second.escort_profile.account = self.accounts[second.pk]
            second.escort_profile.deposit_business_cycle = self.accounts[second.pk].business_cycle
            second.escort_profile.save(update_fields=['account', 'deposit_business_cycle'])
            self.service.billing_provider_count = 2
            self.service.fulfillment_provider_count = 2
            self.service.save(update_fields=['billing_provider_count', 'fulfillment_provider_count'])
        boss = self.accounts[self.boss.pk]
        inviter_user = make_user()
        inviter = get_or_create_account_for_legacy_user(inviter_user)
        boss.inviter, boss.inviter_commission_rate = inviter, 20
        boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'escort:deposit_topup', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        if deposit_case:
            profile = self.provider.escort_profile
            profile.deposit_mode, profile.deposit_paid = 'CASH', 0
            profile.save(update_fields=['deposit_mode', 'deposit_paid'])
            self.login(self.admin)
            initial_deposit = 200 if wallet_case else 1000
            self.ok(self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': initial_deposit, 'earnings_target': initial_deposit + 500, 'expected_version': 0}, format='json'))
            self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': initial_deposit, 'reason': '隔离押金', 'reference': 'stacked-deposit-1',
                'client_request_id': 'stacked-deposit-1'}, format='multipart'))
        game = GameCategory.objects.create(name='单陪两次退款')
        self.service.game_category = game
        self.service.save(update_fields=['game_category'])
        self.provider.escort_profile.service_items.add(self.service)
        if second:
            second.escort_profile.service_items.add(self.service)
        extra = make_service(name='退款加购', game_category=game, is_addon=True)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=extra, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        self.ok(self.recharge())
        if double and wallet_case:
            self.ok(self.recharge(trade_no='wallet-extra', request_id='wallet-extra'))
        if wallet_case:
            from orders.models import ServiceCategory
            gift_category = ServiceCategory.objects.create(name='隔离礼物', is_gift=True)
            gift = make_service(name='钱包来源礼物', price=800, commission_rate=20, service_category=gift_category)
            self.login(self.boss)
            self.ok(self.client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
                'provider_id': owner.pk, 'expected_amount': 800, 'client_request_id': 'wallet-source-gift'}, format='json'))
            from wallet.income import release_due_income
            release_due_income(now=timezone.now() + timedelta(days=10))
            self.assertEqual(get_wallet(user=self.provider, account=owner).balance, 800)
        self.login(self.boss)
        selection = ({'provider_ids': [owner.pk, self.accounts[second.pk].pk]} if second else {'provider_id': owner.pk})
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            **selection, 'support_contact_id': self.card.pk, 'client_request_id': 'stacked-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        if second:
            self.login(second)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        path = f'/api/orders/orders/{order_id}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': extra.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': owner.pk, 'request_id': 'stacked-addon',
            'expected_amount': 1000, 'gift_balance_amount': 1000, 'compensation_balance_amount': 0}, format='json'))
        payment_id = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
        tenant = current_store() or resolve_store()
        finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
        if tenant.slug == 'xinan':
            with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                finance.setUp()
        else:
            finance.setUp()
        receipt = finance.post(tenant.slug, {**finance.data, 'amount': 1000})
        self.assertEqual(receipt.status_code, 200, receipt.data)
        claim = receipt.data['data']['claim_id']
        self.assertEqual(finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=tenant.slug).status_code, 200)
        allocation = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{tenant.slug}/{claim}/points-allocation',
            {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 800}, format='json',
            HTTP_AUTHORIZATION='Bearer ' + finance.token)
        self.assertEqual(allocation.status_code, 200, allocation.data)

        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [owner.pk], 'reason': '主单退款', 'client_request_id': 'main-case'}, format='json'))
        proposal = self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/negotiation-proposal/', {
            'request_id': 'main-plan', 'expected_version': 0, 'refund_units': 9000 if deposit_case else 1000,
            'provider_terms': [{'account_id': owner.pk, 'retained_units': 0 if deposit_case else 7000}],
            'explanation': '主单部分退款'}, format='json'))
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/negotiation-execution/',
            {'proposal_id': proposal['id'], 'client_request_id': 'main-execute'}, format='json'))
        share = Order.objects.get(pk=order_id).refund_records.get().negotiation_execution.shares.get()
        self.assertEqual((share.pending_deduction, share.retained_income, share.wallet_deduction, share.deposit_deduction),
                         (8000, 0, 800, 200) if wallet_case else (8000, 0, 0, 1000) if deposit_case else (1000, 7000, 0, 0))
        main_release = OrderAddonFundingReleaseRun.objects.get(negotiation__refund__order_id=order_id)
        self.assertEqual((main_release.allocated_before, main_release.required_amount, main_release.released_amount),
                         (800, 0, 800) if deposit_case else (800, 600, 200))

        self.login(self.boss)
        generic_customer = self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'affected_provider_account_ids': [owner.pk], 'reason': '主单再次协商',
            'client_request_id': 'forbidden-customer-case'}, format='json')
        self.assertNotEqual(generic_customer.status_code, 200, generic_customer.data)
        self.login(self.admin)
        generic_case = self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [owner.pk], 'reason': '主单再次协商',
            'client_request_id': 'forbidden-unscoped-case'}, format='json')
        self.assertNotEqual(generic_case.status_code, 200, generic_case.data)
        wrong_payment = self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [owner.pk], 'reason': '错误加购付款',
            'client_request_id': 'wrong-addon-case', 'addon_payment_id': payment_id + 1000}, format='json')
        self.assertNotEqual(wrong_payment.status_code, 200, wrong_payment.data)
        if not second:
            from engagement.confirmation import aftersale_deadline
            with patch('django.utils.timezone.now', return_value=aftersale_deadline(Order.objects.get(pk=order_id))):
                expired = self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
                    'affected_provider_account_ids': [owner.pk], 'reason': '超期加购售后',
                    'client_request_id': 'expired-addon-case', 'addon_payment_id': payment_id}, format='json')
            self.assertNotEqual(expired.status_code, 200, expired.data)
        addon_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [owner.pk], 'reason': '加购退款', 'client_request_id': 'addon-case',
            'addon_payment_id': payment_id}, format='json'))
        self.assertEqual(addon_case['addon_payment'], payment_id)
        from orders.negotiation_execution import verify_execution
        from orders.negotiation_termination import verify_termination_evidence
        from stores.db import transaction
        with store_context(tenant), transaction.atomic():
            receipt = Order.objects.get(pk=order_id).refund_records.get().negotiation_execution
            self.assertEqual(verify_execution(receipt)[0].pending_deduction, 8000 if deposit_case else 1000)
            verify_termination_evidence(receipt)
        replay = self.client.post(f'/api/admin/aftersales/{case["id"]}/negotiation-execution/',
            {'proposal_id': proposal['id'], 'client_request_id': 'main-execute'}, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        altered = self.client.post(f'/api/admin/aftersales/{case["id"]}/negotiation-execution/',
            {'proposal_id': proposal['id'], 'client_request_id': 'changed-main-execute'}, format='json')
        self.assertNotEqual(altered.status_code, 200, altered.data)
        self.assertEqual(Order.objects.get(pk=order_id).refund_records.count(), 1)
        blocked = self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/negotiation-proposal/', {
            'request_id': 'forbidden-main', 'expected_version': 1, 'refund_units': 100,
            'provider_terms': [{'account_id': owner.pk, 'retained_units': 0}], 'explanation': '不允许主单追加'}, format='json')
        self.assertNotEqual(blocked.status_code, 200, blocked.data)
        blocked = self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/refund/',
            {'refund_amount': 100, 'resolution': '不允许主单退款', 'client_request_id': 'forbidden-main-refund'}, format='json')
        self.assertNotEqual(blocked.status_code, 200, blocked.data)
        url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
        if deposit_case:
            before_denied = get_wallet(user=self.boss, account=boss).balance
            denied = self.client.post(url, {'request_id': 'addon-plan', 'amount_raw': amount,
                'case_id': addon_case['id'], 'explanation': '加购协商退款'}, format='json')
            self.assertNotEqual(denied.status_code, 200, denied.data)
            self.assertEqual(denied.data['msg'], '陪玩待结算收益、钱包及押金不足，请先补足后退款')
            self.assertFalse(OrderAddonRefund.objects.filter(payment_id=payment_id).exists())
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, before_denied)
            next_deposit = 400 if wallet_case else 1200
            self.ok(self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': next_deposit, 'earnings_target': next_deposit + 500, 'expected_version': 1}, format='json'))
            self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 200, 'reason': '隔离押金补足', 'reference': 'stacked-deposit-2',
                'client_request_id': 'stacked-deposit-2'}, format='multipart'))
        item = self.ok(self.client.post(url, {'request_id': 'addon-plan', 'amount_raw': amount,
            'case_id': addon_case['id'], 'explanation': '加购协商退款'}, format='json'))
        pending_close = self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/resolve/',
            {'resolution': '未确认不应关闭'}, format='json')
        self.assertNotEqual(pending_close.status_code, 200, pending_close.data)
        decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{item["id"]}/'
        if second:
            self.login(second)
            self.assertNotEqual(self.client.post(decision, {'accepted': True}, format='json').status_code, 200)
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        self.login(self.admin)
        execute = url + f'{item["id"]}/execute/'
        before = get_wallet(user=self.boss, account=boss).balance
        target = ('orders.ordinary_addon_after_main_release.plan_release' if deposit_case else
                  'orders.ordinary_addon_after_main_release.OrderAddonFundingRelease.objects.bulk_create')
        with patch(target,
                   side_effect=RuntimeError('release write failed')):
            with self.assertRaisesMessage(RuntimeError, 'release write failed'):
                self.client.post(execute, {}, format='json')
        self.assertFalse(OrderAddonRefund.objects.filter(payment_id=payment_id).exists())
        self.assertEqual(get_wallet(user=self.boss, account=boss).balance, before)
        self.ok(self.client.post(execute, {}, format='json'))
        self.ok(self.client.post(execute, {}, format='json'))
        total_addon_refund = amount
        if amount < 1000:
            total_addon_refund += 100
            buyer_before = get_wallet(user=self.boss, account=boss).balance
            again = self.ok(self.client.post(url, {'request_id': 'second-addon-plan', 'amount_raw': 100,
                'case_id': addon_case['id'], 'explanation': '追加已开放加购退款'}, format='json'))
            again_decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{again["id"]}/'
            second_execute = url + f'{again["id"]}/execute/'
            self.assertNotEqual(self.client.post(second_execute, {}, format='json').status_code, 200)
            self.assertEqual(OrderAddonRefund.objects.filter(payment_id=payment_id).count(), 1)
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, buyer_before)
            for user in (self.boss, self.provider):
                self.login(user)
                self.ok(self.client.post(again_decision, {'accepted': True}, format='json'))
            self.login(self.admin)
            second_result = self.ok(self.client.post(second_execute, {}, format='json'))
            self.assertEqual(self.ok(self.client.post(second_execute, {}, format='json')), second_result)
            refunds = list(OrderAddonRefund.objects.filter(payment_id=payment_id).order_by('pk'))
            self.assertEqual(len(refunds), 2)
            self.assertEqual(sum(row.amount for row in refunds), total_addon_refund)
            self.assertEqual(get_wallet(user=self.boss, account=boss).balance, buyer_before + 100)
        refund = OrderAddonRefund.objects.filter(payment_id=payment_id).order_by('pk').first()
        self.assertEqual(OrderAddonRefund.objects.filter(payment_id=payment_id).count(),
                         2 if amount < 1000 else 1)
        from orders.models import OrderAddonFundingAllocation
        self.assertEqual(dict(OrderAddonFundingAllocation.objects.filter(payment_id=payment_id).values_list(
            'kind', 'refunded_amount')), {'GIFT': total_addon_refund})
        from django.db.models import Sum
        source_rows = Order.objects.get(pk=order_id).funding_allocations.values('kind').annotate(total=Sum('refunded_amount'))
        self.assertEqual({row['kind']: row['total'] for row in source_rows}, {'PREPAID': 9000 if deposit_case else 1000})
        self.assertEqual((refund.addon_pending_deduction, refund.main_pending_deduction,
                          refund.wallet_deduction, refund.deposit_deduction),
                         (800, 0, 0, 200) if deposit_case else (min(amount, 800), max(0, amount - 800), 0, 0))
        release = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        if deposit_case:
            from wallet.models import RefundDepositDeficit
            self.assertIsNotNone(release)
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount), (0, 0, 0))
            self.assertEqual(sorted(RefundDepositDeficit.objects.values_list('amount', flat=True)),
                             [200, 200] if wallet_case else [200, 1000])
        else:
            self.assertEqual((release.allocated_before, release.required_amount, release.released_amount),
                             (600, max(600 - amount, 0), min(amount, 600)))
        self.ok(self.client.post(f'/api/admin/aftersales/{addon_case["id"]}/resolve/', {'resolution': '退款完成'}, format='json'))
        if amount < 1000:
            staff = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/completion/'
            completion = self.ok(self.client.post(staff, {'request_id': 'remaining-done', 'completed_work': '剩余加购已完成'}, format='json'))
            decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/completion/{completion["id"]}/decision/'
            for user in (self.boss, self.provider):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        if second:
            self.login(second)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                {'completion_image': flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider_account=owner)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        if second:
            second_report = ProviderReport.objects.get(order_id=order_id, provider_account=self.accounts[second.pk])
            self.login(second)
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/images/',
                {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{second_report.pk}/approve/', {}, format='json'))
            second_report.refresh_from_db()
        report.refresh_from_db()
        submitted = max(report.submitted_at, second_report.submitted_at) if second else report.submitted_at
        with store_context(tenant), patch('django.utils.timezone.now', return_value=submitted + timedelta(hours=73)):
            process_order(order_id)
            process_order(order_id)
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        self.assertEqual(get_wallet(user=self.provider, account=owner).balance,
                         0 if deposit_case else 7800 - total_addon_refund)
        consumption = OrderAddonConsumption.objects.get(payment_id=payment_id)
        self.assertEqual((consumption.paid_units, consumption.refunded_units), (1000, total_addon_refund))
        if second:
            self.assertEqual(get_wallet(user=second, account=self.accounts[second.pk]).balance, 8000)
        self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance,
                         (2200 if deposit_case else 3800) if second else (200 if deposit_case else 1800))

    def test_partial(self):
        self.scenario(500)

    def test_double_same_affected_partial(self):
        self.scenario(500, double=True)

    def test_double_same_affected_partial_in_xinan(self):
        self.exercise_xinan(500, double=True)

    def test_double_same_affected_full_in_xinan(self):
        self.exercise_xinan(1000, double=True)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_double_same_affected_full_with_wallet_and_deposit(self):
        self.scenario(1000, double=True, deposit_case=True, wallet_case=True)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_full_uses_only_remaining_deposit(self):
        self.scenario(1000, deposit_case=True)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_full_does_not_reuse_wallet_spent_by_main_refund(self):
        self.scenario(1000, deposit_case=True, wallet_case=True)

    def test_full_in_xinan(self):
        self.exercise_xinan(1000)

    def exercise_xinan(self, amount, *, double=False):
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        from orders.models import Order
        from wallet.models import Transaction
        before = (Order.objects.using('default').count(), Transaction.objects.using('default').count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.scenario(amount, double=double)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)
