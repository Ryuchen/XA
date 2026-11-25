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
    ADDON_HANDOVER_INTERNAL_TESTING=False, ADDON_COMPLETION_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=False,
    ORDINARY_REFUNDED_ADDON_RELEASE_INTERNAL_TESTING=False, ORDINARY_REFUNDED_ADDON_COMPLETION_INTERNAL_TESTING=False)
class SingleAddonFirstStackedTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def scenario(self, *, allocated, late_funding=False, wallet_case=False, addon_amount=1000, mixed_source=False, double=False, different=False):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from engagement.confirmation import process_order
        from orders.models import (GameCategory, Order, OrderAddonFundingReleaseRun, OrderAddonRefund,
            ServiceAddonConfiguration, ServiceAddonPriceOption)
        from orders.tests.factories import make_service, make_user
        from stores.context import current_store, resolve_store, store_context
        from stores.db import transaction
        from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
        from wallet.models import ProviderReport
        from wallet.services import get_wallet

        owner, boss = self.accounts[self.provider.pk], self.accounts[self.boss.pk]
        inviter_user = make_user()
        inviter = get_or_create_account_for_legacy_user(inviter_user)
        boss.inviter, boss.inviter_commission_rate = inviter, 20 if allocated or late_funding else 0
        boss.save(update_fields=['inviter', 'inviter_commission_rate'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit', 'escort:deposit_topup', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        if wallet_case:
            profile = self.provider.escort_profile
            profile.deposit_mode, profile.deposit_paid = 'CASH', 0
            profile.save(update_fields=['deposit_mode', 'deposit_paid'])
            self.login(self.admin)
            self.ok(self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': 200, 'earnings_target': 700, 'expected_version': 0}, format='json'))
            self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 200, 'reason': '隔离押金', 'reference': 'first-addon-deposit',
                'client_request_id': 'first-addon-deposit'}, format='multipart'))
        game = GameCategory.objects.create(name='先退加购再退主单')
        self.service.game_category = game
        if double:
            from orders.tests.factories import make_provider
            second = make_provider(display_name='未受影响陪玩', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
            second_account = get_or_create_account_for_legacy_user(second)
            self.accounts[second.pk] = second_account
            second.escort_profile.account = second_account
            second.escort_profile.deposit_business_cycle = second_account.business_cycle
            second.escort_profile.save(update_fields=['account', 'deposit_business_cycle'])
            self.service.billing_provider_count = 2
            self.service.fulfillment_provider_count = 2
            second.escort_profile.service_items.add(self.service)
        if not allocated and not late_funding:
            self.service.commission_rate = 100 if wallet_case else 0
        self.service.save(update_fields=['game_category', 'commission_rate', 'billing_provider_count',
                                         'fulfillment_provider_count'])
        self.provider.escort_profile.service_items.add(self.service)
        addon = make_service(name='隔离加购', game_category=game, is_addon=True,
                             commission_rate=20 if allocated or late_funding else 0)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        self.ok(self.recharge())
        if wallet_case:
            from orders.models import ServiceCategory
            gift_category = ServiceCategory.objects.create(name='真实钱包收入', is_gift=True)
            gift = make_service(name='先退加购钱包来源', price=800, service_category=gift_category)
            self.login(self.boss)
            self.ok(self.client.post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
                'provider_id': owner.pk, 'expected_amount': 800, 'client_request_id': 'prior-wallet-gift'}, format='json'))
            from wallet.income import release_due_income
            release_due_income(now=timezone.now() + timedelta(days=10))
            self.assertEqual(get_wallet(user=self.provider, account=owner).balance, 800)
        if late_funding:
            self.ok(self.recharge(trade_no='late-extra', request_id='late-extra'))
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            **({'provider_ids': [owner.pk, second_account.pk]} if double else {'provider_id': owner.pk}), 'support_contact_id': self.card.pk,
            'gift_balance_amount': 1000 if late_funding else 0, 'client_request_id': 'addon-first'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        if double:
            self.login(second)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        path = f'/api/orders/orders/{order_id}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': owner.pk, 'request_id': 'addon-first-item',
            'expected_amount': 1000, 'gift_balance_amount': 500 if mixed_source else 1000,
            'compensation_balance_amount': 0}, format='json'))
        payment_id = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
        tenant = current_store() or resolve_store()
        if allocated:
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
            result = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{tenant.slug}/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 300 if mixed_source else 800}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(result.status_code, 200, result.data)

        self.login(self.admin)
        case_payload = {'order_id': order_id, 'affected_provider_account_ids': [owner.pk],
                        'reason': '加购退款', 'client_request_id': 'addon-case'}
        case_url = '/api/admin/aftersales/open-negotiation/'
        if double:
            from orders.models import AfterSale
            from django.db.models import DateTimeField
            with patch('orders.aftersale.append_history', side_effect=RuntimeError('history failed')):
                with self.assertRaisesMessage(RuntimeError, 'history failed'):
                    self.client.post(case_url, case_payload, format='json')
            self.assertFalse(AfterSale.objects.filter(order_id=order_id).exists())
            original_pre_save = DateTimeField.pre_save
            def displaced_pre_save(field, instance, add):
                value = original_pre_save(field, instance, add)
                if isinstance(instance, AfterSale) and field.name == 'created_at' and add:
                    value -= timedelta(seconds=1)
                    instance.created_at = value
                return value
            with patch.object(DateTimeField, 'pre_save', displaced_pre_save):
                case = self.ok(self.client.post(case_url, case_payload, format='json'))
        else:
            case = self.ok(self.client.post(case_url, case_payload, format='json'))
        url = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/'
        addon_proposal = self.ok(self.client.post(url, {'request_id': 'addon-plan', 'amount_raw': addon_amount,
            'case_id': case['id'], 'explanation': '加购协商退款'}, format='json'))
        decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/refund-proposals/{addon_proposal["id"]}/'
        for user in (self.boss, self.provider):
            self.login(user)
            self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(url + f'{addon_proposal["id"]}/execute/', {}, format='json'))
        addon_refund = OrderAddonRefund.objects.get(payment_id=payment_id)
        if double and not different:
            from types import SimpleNamespace
            from orders.ordinary_addon_production import eligible
            item = Order.objects.get(pk=order_id)
            self.assertFalse(eligible(item, allow_open=True, proposal=SimpleNamespace(
                affected_accounts=[owner.pk, second_account.pk], refund_units=1000)))
        from orders.models import AfterSale
        opened = [row for row in AfterSale.objects.get(pk=case['id']).history
                  if row.get('client_request_id') == 'addon-case' and row.get('status') == 'OPEN']
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]['created_at'], addon_refund.proposal.case_opened_at.isoformat())
        if mixed_source:
            from orders.models import OrderAddonFundingAllocation
            self.assertEqual(dict(OrderAddonFundingAllocation.objects.filter(payment_id=payment_id).values_list(
                'kind', 'refunded_amount')), {'PREPAID': 500, 'GIFT': 200})
        self.assertEqual((addon_refund.addon_pending_deduction, addon_refund.main_pending_deduction),
                         (0, 0) if wallet_case else (min(addon_amount, 800), max(0, addon_amount - 800))
                         if allocated or late_funding else (addon_amount, 0))
        if wallet_case:
            self.assertEqual((addon_refund.wallet_deduction, addon_refund.deposit_deduction),
                             (500, 0) if addon_amount == 500 else (800, 200))
        premature = self.client.post(f'/api/admin/aftersales/{case["id"]}/negotiation-proposal/', {
            'request_id': 'same-episode-main', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': owner.pk, 'retained_units': 6800}],
            'explanation': '不同业务须另行协商'}, format='json')
        self.assertNotEqual(premature.status_code, 200, premature.data)
        self.ok(self.client.post(f'/api/admin/aftersales/{case["id"]}/resolve/', {'resolution': '加购退款完成'}, format='json'))
        if wallet_case:
            next_target = 700 if addon_amount == 500 else 1000
            self.ok(self.client.put('/api/admin/config/deposit-targets', {
                'cash_target': next_target, 'earnings_target': next_target + 500, 'expected_version': 1}, format='json'))
            self.ok(self.client.post(f'/api/admin/escorts/{profile.pk}/deposit-topups/', {
                'amount': 500 if addon_amount == 500 else 1000, 'reason': '主单退款补足押金', 'reference': 'main-refund-deposit',
                'client_request_id': 'main-refund-deposit'}, format='multipart'))

        main_owner = second_account if different else owner
        main_user = second if different else self.provider
        main_case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [main_owner.pk], 'reason': '主单退款', 'client_request_id': 'main-case'}, format='json'))
        reopened = AfterSale.objects.get(pk=main_case['id'])
        current_open = [row for row in reopened.history if row.get('client_request_id') == 'main-case' and row.get('status') == 'OPEN']
        self.assertEqual(len(current_open), 1)
        self.assertEqual(current_open[0]['created_at'], reopened.created_at.isoformat())
        income = (Order.objects.get(pk=order_id).providers.get(provider_account=main_owner).provider_income - (0 if different else max(0, addon_amount - 800)) if double else 0 if wallet_case else 8000 - max(0, addon_amount - 800)
                  if allocated or late_funding else 10000)
        main = self.ok(self.client.post(f'/api/admin/aftersales/{main_case["id"]}/negotiation-proposal/', {
            'request_id': 'main-plan', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': main_owner.pk, 'retained_units': max(0, income - 1000)}], 'explanation': '主单部分退款'}, format='json'))
        if different:
            from orders.models import NegotiationProposal
            decisions = NegotiationProposal.objects.get(pk=main['id']).decisions
            self.assertEqual(set(decisions.values_list('account_id', flat=True)), {boss.pk, main_owner.pk})
            from types import SimpleNamespace
            from orders.ordinary_addon_production import eligible
            self.assertFalse(eligible(Order.objects.get(pk=order_id), allow_open=True, proposal=SimpleNamespace(
                affected_accounts=[main_owner.pk], provider_terms=[{'additional_units': 1}], refund_units=1000)))
        for user in (self.boss, main_user):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                {'proposal_id': main['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        execute = f'/api/admin/aftersales/{main_case["id"]}/negotiation-execution/'
        payload = {'proposal_id': main['id'], 'client_request_id': 'main-execute'}
        before = get_wallet(user=self.boss, account=boss).balance
        if different:
            provider_before = {account.pk: (get_wallet(user=user, account=account).balance,
                user.escort_profile.deposit_paid) for user, account in ((self.provider, owner), (second, second_account))}
        # A missing decision must block even when no main earnings or funding were used.
        with transaction.atomic():
            addon_proposal_obj = addon_refund.proposal
            addon_proposal_obj.decisions.filter(account=owner).update(accepted=False)
            self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
            transaction.set_rollback(True)
        with transaction.atomic():
            addon_proposal_obj = addon_refund.proposal
            addon_proposal_obj.source_plan = []
            addon_proposal_obj.save(update_fields=['source_plan'])
            self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
            transaction.set_rollback(True)
        with transaction.atomic():
            from console.models import AdminAuditLog
            AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(addon_refund.pk)).delete()
            self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
            transaction.set_rollback(True)
        if wallet_case and addon_refund.deposit_deduction:
            with transaction.atomic():
                from wallet.models import RefundDepositDeficit, Transaction
                deficit = RefundDepositDeficit.objects.get(addon_refund=addon_refund)
                Transaction.objects.filter(pk=deficit.source_store_transaction_id).update(amount=-190)
                self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
                transaction.set_rollback(True)
        with transaction.atomic():
            from orders.models import AfterSale
            previous_case = AfterSale.objects.get(pk=case['id'])
            history = list(previous_case.history)
            original_entry = next(row for row in history if row.get('client_request_id') == 'addon-case' and row.get('status') == 'OPEN')
            original_entry['affected_provider_account_ids'] = []
            previous_case.history = history
            previous_case.save(update_fields=['history'])
            self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
            transaction.set_rollback(True)
        if allocated:
            with transaction.atomic():
                run = OrderAddonFundingReleaseRun.objects.get(refund=addon_refund)
                run.required_amount, run.released_amount = run.required_amount + 100, run.released_amount - 100
                run.save(update_fields=['required_amount', 'released_amount'])
                run.releases.update(amount=run.released_amount)
                from orders.addon_funding_release import verify_run
                from rest_framework.exceptions import ValidationError
                with self.assertRaises(ValidationError):
                    verify_run(run)  # The historical requirement itself must catch this self-consistent alteration.
                self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
                transaction.set_rollback(True)
            with patch('orders.ordinary_refunded_addon_release.OrderAddonFundingRelease.objects.bulk_create',
                       side_effect=RuntimeError('main release failed')):
                with self.assertRaisesMessage(RuntimeError, 'main release failed'):
                    response = self.client.post(execute, payload, format='json')
                    self.fail(str(response.data))
        self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        self.assertEqual(get_wallet(user=self.boss, account=boss).balance, before)
        from orders.ordinary_addon_prior_refund import verify_locked
        with transaction.atomic():
            verify_locked(Order.objects.get(pk=order_id))
        self.ok(self.client.post(execute, payload, format='json'))
        if different:
            for user, account in ((self.provider, owner), (second, second_account)):
                user.escort_profile.refresh_from_db()
                self.assertEqual((get_wallet(user=user, account=account).balance, user.escort_profile.deposit_paid),
                                 provider_before[account.pk])
        self.ok(self.client.post(execute, payload, format='json'))
        if double:
            from orders.negotiation_execution import _lock_reverse_addon_people
            with patch('orders.negotiation_execution._lock_reverse_addon_people', wraps=_lock_reverse_addon_people) as ordered:
                self.ok(self.client.get(execute, payload))
                self.assertTrue(ordered.called)
            with override_settings(ORDINARY_SERVICE_FUNDING_ENABLED=False):
                from orders.ordinary_addon_production import eligible
                self.assertFalse(eligible(Order.objects.get(pk=order_id), allow_open=True))
                self.ok(self.client.get(execute, payload))
            with transaction.atomic():
                addon_refund.proposal.decisions.filter(account=owner).update(accepted=False)
                self.assertNotEqual(self.client.get(execute, payload).status_code, 200)
                self.assertNotEqual(self.client.post(execute, payload, format='json').status_code, 200)
                transaction.set_rollback(True)
        self.ok(self.client.post(url + f'{addon_proposal["id"]}/execute/', {}, format='json'))
        self.assertEqual(OrderAddonRefund.objects.filter(payment_id=payment_id).count(), 1)
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.refund_records.count(), 1)
        from django.db.models import Sum
        source_rows = order.funding_allocations.values('kind').annotate(total=Sum('refunded_amount'))
        self.assertEqual({row['kind']: row['total'] for row in source_rows},
                         {'PREPAID': 1000, 'GIFT': 0} if late_funding else {'PREPAID': 1000})
        share = order.refund_records.get().negotiation_execution.shares.get()
        self.assertEqual((share.original_income, share.pending_deduction, share.retained_income),
                         (0, 0, 0) if wallet_case else (income, 1000, income - 1000))
        if wallet_case:
            self.assertEqual((share.wallet_deduction, share.deposit_deduction),
                             (300, 700) if addon_amount == 500 else (0, 1000))
        if addon_amount < 1000:
            from orders.points_funding_evidence import verify_order_funding
            from orders.points_funding_plan import PointsFundingPlanError
            with store_context(tenant), self.assertRaises(PointsFundingPlanError):
                verify_order_funding(order_id)
            self.login(self.admin)
            staff = f'/api/admin/orders/{order_id}/addons/payments/{payment_id}/completion/'
            completion = self.ok(self.client.post(staff, {'request_id': 'remaining-addon-done',
                'completed_work': '其余加购已完成'}, format='json'))
            decision = f'/api/orders/orders/{order_id}/addons/payments/{payment_id}/completion/{completion["id"]}/decision/'
            for user in (self.boss, self.provider):
                self.login(user)
                self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        if late_funding:
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            finance.setUp()
            receipt = finance.post(tenant.slug, {**finance.data, 'amount': 2000})
            self.assertEqual(receipt.status_code, 200, receipt.data)
            claim = receipt.data['data']['claim_id']
            self.assertEqual(finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=tenant.slug).status_code, 200)
            result = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{tenant.slug}/{claim}/points-allocation',
                {'request_id': str(uuid4()), 'order_id': order_id, 'amount': 1600 - addon_amount}, format='json',
                HTTP_AUTHORIZATION='Bearer ' + finance.token)
            self.assertEqual(result.status_code, 200, result.data)
        if double:
            continuing_user = self.provider if different else second
            continuing_account = owner if different else second_account
            self.login(continuing_user)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
            second_report = ProviderReport.objects.get(order=order, provider_account=continuing_account)
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/images/', {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{second_report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{second_report.pk}/approve/', {}, format='json'))
        report = ProviderReport.objects.get(order=order, provider_account=main_owner)
        self.login(main_user)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        with store_context(tenant), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            from orders.settlement import SettlementError
            from orders.points_funding_plan import PointsFundingPlanError
            from rest_framework.exceptions import ValidationError
            with transaction.atomic():
                addon_refund.proposal.decisions.filter(account=owner).update(accepted=False)
                with self.assertRaises((SettlementError, PointsFundingPlanError, ValidationError)):
                    process_order(order_id)
                self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
                transaction.set_rollback(True)
            process_order(order_id)
            process_order(order_id)
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        addon_income = 0 if wallet_case else 800 if allocated or late_funding else 1000
        self.assertEqual(get_wallet(user=main_user, account=main_owner).balance,
                         max(0, income - 1000) + (0 if different else max(0, addon_income - addon_amount)))
        self.assertEqual(get_wallet(user=inviter_user, account=inviter).balance, 3800 if double else 1800 if allocated or late_funding else 0)
        if double:
            self.assertEqual(get_wallet(user=continuing_user, account=continuing_account).balance,
                             order.providers.get(provider_account=continuing_account).provider_income
                             - (max(0, addon_amount - 800) if different else 0))

    def test_double_different_people(self):
        self.scenario(allocated=True, double=True, different=True)

    def test_double_different_people_in_xinan(self):
        self.exercise_xinan(addon_amount=1000, double=True, different=True)

    def test_double_partial_remaining_addon_income(self):
        self.scenario(allocated=True, addon_amount=500, double=True)

    def test_double_partial_remaining_addon_income_in_xinan(self):
        self.exercise_xinan(addon_amount=500, double=True)

    def test_double_partial_allocated_same_person(self):
        self.scenario(allocated=True, addon_amount=900, double=True)

    def test_double_partial_allocated_in_xinan(self):
        self.exercise_xinan(addon_amount=900, double=True)

    def test_double_allocated_same_person(self):
        self.scenario(allocated=True, double=True)

    def test_no_allocation_no_main_income_deduction(self):
        self.scenario(allocated=False)

    def test_no_prior_allocation_can_fund_after_main_refund(self):
        self.scenario(allocated=False, late_funding=True)

    def test_partial_without_prior_allocation_can_fund_after_confirmation(self):
        self.scenario(allocated=False, late_funding=True, addon_amount=500)

    def test_partial_refund_consumes_main_income_before_main_refund(self):
        self.scenario(allocated=True, addon_amount=900)

    def test_partial_mixed_payment_sources_and_funding(self):
        self.scenario(allocated=True, addon_amount=700, mixed_source=True)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_first_addon_wallet_deposit_then_main_uses_new_deposit(self):
        self.scenario(allocated=False, wallet_case=True)

    @override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True)
    def test_partial_addon_wallet_then_main_uses_remaining_wallet_and_deposit(self):
        self.scenario(allocated=False, wallet_case=True, addon_amount=500)

    def test_double_allocated_in_xinan(self):
        self.exercise_xinan(addon_amount=1000, double=True)

    def test_allocated_in_xinan(self):
        self.exercise_xinan(addon_amount=1000)

    def test_partial_allocated_in_xinan(self):
        self.exercise_xinan(addon_amount=500)

    def exercise_xinan(self, *, addon_amount, double=False, different=False):
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
            self.scenario(allocated=True, addon_amount=addon_amount, double=double, different=different)
        self.assertEqual((Order.objects.using('default').count(), Transaction.objects.using('default').count()), before)

