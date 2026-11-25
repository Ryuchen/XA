from django.test import override_settings
from rest_framework.test import APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import GameCategory, OrderProvider, ServiceAddonConfiguration, ServiceAddonPriceOption
from orders.tests.factories import make_provider, make_service
from orders.tests import test_release_flow as flow
from wallet.models import ProviderReport


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True, ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True,
    ORDINARY_ADDON_FOLLOWUP_PREVIEW_INTERNAL_TESTING=True,
    LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class OrdinaryAddonNegotiatedReportTest(APITestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_unaffected_teammate_report_excludes_own_addon_refund_from_main_income(self):
        self.exercise_addon_followup(with_funding=False)

    def test_followup_releases_real_platform_funding(self):
        self.exercise_addon_followup(with_funding=True)

    def test_followup_settles_remaining_addon_income(self):
        self.exercise_addon_followup(with_funding=False, with_remaining_addon=True)

    def test_followup_before_first_person_is_paid(self):
        self.exercise_addon_followup(with_funding=False, prior_unpaid=True, with_remaining_addon=True)

    def test_zero_followup_can_release_second_person_first(self):
        self.exercise_addon_followup(with_funding=False, prior_unpaid=True, second_first=True, zero_followup=True)

    def test_unpaid_followup_rebalances_platform_funding(self):
        self.exercise_addon_followup(with_funding=True, prior_unpaid=True)

    def test_unfunded_followup_refunds_then_waits_for_store_funding(self):
        self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True)

    def test_unfunded_zero_followup_waits_for_store_funding(self):
        self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True, zero_followup=True)

    def test_followup_with_no_prior_allocation(self):
        self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True, zero_initial=True)

    def test_followup_zero_and_refund_in_xinan(self):
        from stores.tests_hosts import xinan_store
        from stores.context import resolve_store, store_context
        from orders.models import Order
        from wallet.models import Transaction
        first = resolve_store()
        with store_context(first):
            before = (Order.objects.count(), Transaction.objects.count())
        store = xinan_store()
        with store_context(store):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_addon_followup(with_funding=False)
            self.assertEqual(Order.objects.count(), 1)
        with store_context(first):
            self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)

    def test_unfunded_followup_remains_isolated_in_xinan(self):
        from stores.tests_hosts import xinan_store
        from stores.context import resolve_store, store_context
        from orders.models import Order
        from wallet.models import Transaction
        first = resolve_store()
        with store_context(first):
            before = (Order.objects.count(), Transaction.objects.count())
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True,
                                         zero_initial=True, simulate_funding_failure=True)
        with store_context(first):
            self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)

    def exercise_addon_followup(self, *, with_funding, with_remaining_addon=False, concurrency=False,
                                prior_unpaid=False, second_first=False, zero_followup=False, underfunded=False,
                                zero_initial=False, simulate_funding_failure=False):
        from orders.tests.factories import make_user
        inviter = get_or_create_account_for_legacy_user(make_user())
        boss_account = self.accounts[self.boss.pk]
        boss_account.inviter, boss_account.inviter_commission_rate = inviter, 20 if with_funding else 10
        boss_account.save(update_fields=['inviter', 'inviter_commission_rate'])
        second = make_provider(display_name='继续履约陪玩', deposit_paid=40000, wechat_avatar_legacy_exempt=True)
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        second.escort_profile.account = second_account
        second.escort_profile.deposit_business_cycle = second_account.business_cycle
        second.escort_profile.save(update_fields=['account', 'deposit_business_cycle'])
        game = GameCategory.objects.create(name='双陪退款报单测试')
        self.service.game_category = game
        self.service.billing_provider_count = 2
        self.service.fulfillment_provider_count = 2
        self.service.save(update_fields=['game_category', 'billing_provider_count', 'fulfillment_provider_count'])
        for player in (self.provider, second):
            player.escort_profile.service_items.add(self.service)
        addon = make_service(name='双陪加购', game_category=game, is_addon=True)
        config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
            condition_mode='NONE', conditions='', version=1)
        ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, second_account.pk],
            'gift_balance_amount': 1000 if with_funding else 0,
            'support_contact_id': self.card.pk, 'client_request_id': 'ordinary-report-double'}, format='json'))['id']
        for player in (self.provider, second):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        path = f'/api/orders/orders/{order_id}/addons/intents/'
        intent = self.ok(self.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
            'configuration_version': 1, 'provider_account_id': second_account.pk, 'request_id': 'report-addon',
            'expected_amount': 1000, 'gift_balance_amount': 0 if with_funding else 1000,
            'compensation_balance_amount': 0}, format='json'))
        payment = self.ok(self.client.post(path + str(intent['id']) + '/pay/', {}, format='json'))['payment_id']
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [second_account.pk], 'reason': '加购退款',
            'client_request_id': 'report-addon-case'}, format='json'))
        url = f'/api/admin/orders/{order_id}/addons/payments/{payment}/refund-proposals/'
        proposal = self.ok(self.client.post(url, {'request_id': 'report-addon-refund', 'amount_raw': 1000,
            'case_id': case['id'], 'explanation': '加购未履行'}, format='json'))
        decision = f"/api/orders/orders/{order_id}/addons/payments/{payment}/refund-proposals/{proposal['id']}/"
        for player in (self.boss, second):
            self.login(player)
            self.ok(self.client.post(decision, {'accepted': True}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(url + f"{proposal['id']}/execute/", {}, format='json'))
        self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/resolve/", {'resolution': '加购已退款'}, format='json'))
        if with_remaining_addon:
            self.login(self.boss)
            next_intent = self.ok(self.client.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                'configuration_version': 1, 'provider_account_id': second_account.pk, 'request_id': 'retained-addon',
                'expected_amount': 1000, 'gift_balance_amount': 1000, 'compensation_balance_amount': 0}, format='json'))
            remaining_addon_id = self.ok(self.client.post(path + str(next_intent['id']) + '/pay/', {}, format='json'))['payment_id']
            self.login(self.admin)
        affected = self.accounts[self.provider.pk]
        income_a = OrderProvider.objects.get(order_id=order_id, provider_account=affected).provider_income
        income_b = OrderProvider.objects.get(order_id=order_id, provider_account=second_account).provider_income
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id': order_id,
            'affected_provider_account_ids': [affected.pk], 'reason': '另一席位协商终止',
            'client_request_id': 'report-main-case'}, format='json'))
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id': 'report-main-refund', 'expected_version': 0, 'refund_units': 1000,
            'provider_terms': [{'account_id': affected.pk, 'retained_units': income_a - 1000}],
            'explanation': '退还主单部分金额'}, format='json'))
        for player in (self.boss, self.provider):
            self.login(player)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/negotiation-proposal/',
                                     {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        self.login(self.admin)
        response = self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-execution/", {
            'proposal_id': proposal['id'], 'client_request_id': 'report-main-execute'}, format='json')
        if response.status_code != 200:
            from orders.models import AfterSale, NegotiationProposal, Order
            from django.utils import timezone
            item, episode, agreement = Order.objects.get(pk=order_id), AfterSale.objects.get(pk=case['id']), NegotiationProposal.objects.get(pk=proposal['id'])
            self.fail(f'{response.data}; order={(item.status, item.in_service_at, item.settlement_mode, item.service_clock_version, item.service_aftersale_hours, item.service_freeze_hours, item.service_deposit_percent)} '
                      f'case={(episode.status, episode.created_at, episode.client_request_id)} '
                      f'proposal={(agreement.case_opened_at, agreement.case_request_id, agreement.accepted_at)} now={timezone.now()}')
        self.ok(response)
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order_id, provider_account=second_account)
        listing = self.ok(self.client.get('/api/wallet/reports/'))
        item = next(row for row in listing if row['id'] == report.pk)
        self.assertEqual(item['expected_income'], income_b - 200)
        self.assertIsNone(item['refund_summary'])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
        self.assertEqual(detail['expected_income'], income_b - 200)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report_a = ProviderReport.objects.get(order_id=order_id, provider_account=affected)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report_a.pk}/images/',
                                     {'kind': kind, 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report_a.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report_a.pk}/approve/', {}, format='json'))
        self.login(self.boss)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '继续处理另一位陪玩的售后', 'affected_provider_account_ids': [second_account.pk],
            'client_request_id': 'ordinary-addon-followup-case'}, format='json'))
        from orders.models import AfterSale
        followup_case = AfterSale.objects.get(order_id=order_id)
        if with_funding:
            from uuid import uuid4
            from stores.test_platform_pass_repayment import PlatformPassRepaymentTests
            from orders.ordinary_refunded_addon_funding import plan_locked
            from stores.context import current_store, resolve_store, store_context
            from stores.db import transaction
            finance = PlatformPassRepaymentTests(methodName='test_scoped_registration_and_cross_store_duplicate')
            tenant = current_store() or resolve_store()
            slug = tenant.slug
            if slug == 'xinan':
                from unittest.mock import patch
                with patch('stores.test_platform_pass_repayment.xinan_store', return_value=tenant):
                    finance.setUp()
            else:
                finance.setUp()
            with store_context(tenant), transaction.atomic():
                requirement = plan_locked(order_id).required
            self.assertGreater(requirement, 0)
            receipt = finance.post(slug, {**finance.data, 'amount': requirement + 1000})
            self.assertEqual(receipt.status_code, 200, receipt.data)
            claim = receipt.data['data']['claim_id']
            verified = finance.action(claim, 'verify', {'request_id': str(uuid4())}, slug=slug)
            self.assertEqual(verified.status_code, 200, verified.data)
            if not zero_initial:
                allocation = finance.client.post(f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation',
                    {'request_id': str(uuid4()), 'order_id': order_id,
                     'amount': max(1, requirement // 3) if underfunded else requirement}, format='json',
                    HTTP_AUTHORIZATION='Bearer ' + finance.token)
                self.assertEqual(allocation.status_code, 200, allocation.data)
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from engagement.confirmation import process_order
        from orders.models import OrderPointsSettlement
        from stores.context import current_store, resolve_store, store_context
        report_a.refresh_from_db()
        report.refresh_from_db()
        report.submitted_at = report_a.submitted_at + timedelta(hours=3)
        report.audited_at = report.submitted_at + timedelta(seconds=1)
        report.save(update_fields=['submitted_at', 'audited_at'])
        if second_first:
            report_a.submitted_at = report.submitted_at + timedelta(hours=3)
            report_a.audited_at = report_a.submitted_at + timedelta(seconds=1)
            report_a.save(update_fields=['submitted_at', 'audited_at'])
        from wallet.services import get_wallet
        if prior_unpaid:
            with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                    return_value=report_a.submitted_at + timedelta(hours=1)):
                self.assertFalse(OrderPointsSettlement.objects.filter(order_id=order_id).exists())
                self.assertEqual(get_wallet(account=affected).balance, 0)
                self.assertEqual(get_wallet(account=second_account).balance, 0)
                with override_settings(ORDINARY_ADDON_FOLLOWUP_ENDPOINT_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True,
                        ORDINARY_ADDON_FOLLOWUP_ZERO_INTERNAL_TESTING=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                        ORDINARY_ADDON_FOLLOWUP_UNFUNDED_INTERNAL_TESTING=underfunded,
                        REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True):
                    self.login(self.admin)
                    url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/'
                    info = self.ok(self.client.get(url))
                    self.assertTrue(info['execution_available'])
                    refund = 0 if zero_followup else 1000
                    agreement = self.ok(self.client.post(url, {'request_id': 'unpaid-followup-agreement',
                        'expected_version': info['version'], 'refund_units': refund,
                        'provider_terms': [{'account_id': second_account.pk, 'retained_units': income_b - 200 - refund}],
                        'explanation': '两位陪玩均未结算时的追加退款'}, format='json'))
                    decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                    for user in (self.boss, second):
                        self.login(user)
                        self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                    self.login(self.admin)
                    execution_url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-execution/'
                    request = {'proposal_id': agreement['id'], 'client_request_id': 'unpaid-followup-refund'}
                    buyer_before = get_wallet(account=boss_account).balance
                    result = self.ok(self.client.post(execution_url, request, format='json'))
                    self.assertEqual(result['refund_units'], refund)
                    self.assertEqual(get_wallet(account=boss_account).balance, buyer_before + refund)
                    self.assertTrue(OrderPointsSettlement.objects.filter(order_id=order_id).exists())
                    if with_funding and not underfunded:
                        from orders.models import OrderAddonFundingReleaseRun
                        release = OrderAddonFundingReleaseRun.objects.get(negotiation__proposal_id=agreement['id'])
                        self.assertGreater(release.released_amount, 0)
                        overview = finance.reconciliation(slug=slug)
                        self.assertEqual(overview.status_code, 200, overview.data)
                        self.assertEqual(overview.data['data']['claims'][0]['receipt']['unallocated_amount'],
                                         1000 + release.released_amount)
                    self.assertEqual(self.ok(self.client.post(execution_url, request, format='json')), result)
                    if underfunded:
                        from orders.points_funding_evidence import verify_order_funding
                        status = verify_order_funding(order_id)
                        self.assertGreater(status.remaining, 1)
                        status_url = f'/api/admin/platform/points-funding/{slug}/orders/{order_id}'
                        queue_url = f'/api/admin/platform/points-funding/{slug}/orders'
                        def funding_status():
                            return finance.client.get(status_url, HTTP_AUTHORIZATION='Bearer ' + finance.token)
                        def funding_queue():
                            return finance.client.get(queue_url, HTTP_AUTHORIZATION='Bearer ' + finance.token)
                        shown = funding_status()
                        self.assertEqual(shown.status_code, 200, shown.data)
                        self.assertEqual(shown.data['data'], {'order_id': order_id, 'store_slug': slug,
                            'required_units': status.required, 'allocated_units': status.allocated,
                            'remaining_units': status.remaining, 'fully_funded': False})
                        self.assertIn('no-store', shown['Cache-Control'])
                        queue = funding_queue()
                        self.assertEqual(queue.status_code, 200, queue.data)
                        queued = next(row for row in queue.data['data']['list'] if row['order_id'] == order_id)
                        self.assertEqual((queued['funding_status'], queued['remaining_units']), ('NEEDS_FUNDING', status.remaining))
                        self.assertIn('no-store', queue['Cache-Control'])
                        def revoke_during_scan(*args, **kwargs):
                            result = verify_order_funding(*args, **kwargs)
                            finance.grant.scope_slug = 'shenglang' if slug == 'xinan' else 'xinan'
                            finance.grant.save(update_fields=['scope_slug'])
                            return result
                        with patch('orders.points_funding_evidence.verify_order_funding', side_effect=revoke_during_scan):
                            self.assertEqual(funding_queue().status_code, 404)
                        finance.grant.scope_slug = '*'
                        finance.grant.save(update_fields=['scope_slug'])
                        if slug == 'xinan':
                            foreign = finance.client.get('/api/admin/platform/points-funding/shenglang/orders',
                                                         HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(foreign.status_code, 200, foreign.data)
                            self.assertNotIn(order_id, [row['order_id'] for row in foreign.data['data']['list']])
                        from stores.db import transaction
                        from orders.tests.factories import make_order
                        with transaction.atomic():
                            incomplete = make_order(self.boss, service=self.service, customer_account=boss_account,
                                status='GRABBED', payment_status='PAID', funding_terms={})
                            first_page = finance.client.get(queue_url, {'limit': 1}, HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(first_page.data['data']['list'][0]['order_id'], order_id)
                            self.assertTrue(first_page.data['data']['has_more'])
                            next_page = finance.client.get(queue_url, {'limit': 1,
                                'after_id': first_page.data['data']['next_after_id']}, HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(next_page.data['data']['list'][0]['order_id'], incomplete.pk)
                            self.assertEqual(next_page.data['data']['list'][0]['funding_status'], 'REVIEW')
                            self.assertIsNone(next_page.data['data']['list'][0]['remaining_units'])
                            self.assertFalse(next_page.data['data']['has_more'])
                            transaction.set_rollback(True)
                        self.assertEqual(finance.client.get(queue_url, {'limit': 0},
                            HTTP_AUTHORIZATION='Bearer ' + finance.token).status_code, 400)
                        from rest_framework.test import APIClient
                        self.assertNotEqual(APIClient(HTTP_HOST='admin.esports.club').get(status_url).status_code, 200)
                        self.assertNotEqual(APIClient(HTTP_HOST='admin.esports.club').get(queue_url).status_code, 200)
                        finance.grant.scope_slug = 'shenglang' if slug == 'xinan' else 'xinan'
                        finance.grant.save(update_fields=['scope_slug'])
                        self.assertEqual(funding_status().status_code, 404)
                        self.assertEqual(funding_queue().status_code, 404)
                        finance.grant.scope_slug = '*'
                        finance.grant.save(update_fields=['scope_slug'])
                        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                            self.assertFalse(process_order(order_id))
                            self.assertEqual(get_wallet(account=affected).balance, 0)
                            self.assertEqual(get_wallet(account=second_account).balance, 0)
                            self.login(second)
                            item = next(row for row in self.ok(self.client.get('/api/wallet/reports/'))
                                        if row['id'] == report.pk)
                            self.assertEqual(item['settlement_status'], 'WAITING')
                            self.assertIs(item['platform_funding_insufficient'], True)
                            amount = status.remaining // 2
                            fund_url = f'/api/admin/platform/pass-refund-repayments/{slug}/{claim}/points-allocation'
                            over = finance.client.post(fund_url, {'request_id': str(uuid4()),
                                'order_id': order_id, 'amount': status.remaining + 1}, format='json',
                                HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertNotEqual(over.status_code, 200)
                            self.assertEqual(verify_order_funding(order_id).remaining, status.remaining)
                            partial_request = {'request_id': str(uuid4()), 'order_id': order_id, 'amount': amount}
                            if simulate_funding_failure:
                                from django.db import DatabaseError
                                from stores.models import PlatformAuditEvent, PlatformPointsFundingOperation
                                from orders.models import OrderPointsFundingAllocation
                                from orders.points_funding_plan import PointsFundingPlanError
                                audit_create = PlatformAuditEvent.objects.create
                                def fail_completion(**kwargs):
                                    if kwargs.get('action') == 'points_funding.allocate_completed':
                                        raise DatabaseError('模拟平台记录失败')
                                    return audit_create(**kwargs)
                                with patch('stores.platform_points_funding.PlatformAuditEvent.objects.create',
                                           side_effect=fail_completion):
                                    uncertain = finance.client.post(fund_url, partial_request, format='json',
                                        HTTP_AUTHORIZATION='Bearer ' + finance.token)
                                self.assertEqual((uncertain.status_code, uncertain.data['errorCode']),
                                                 (503, 'POINTS_FUNDING_UNCERTAIN'))
                                operation = PlatformPointsFundingOperation.objects.get(request_id=partial_request['request_id'])
                                self.assertIsNone(operation.completed_at)
                                self.assertEqual(OrderPointsFundingAllocation.objects.filter(service_order_id=order_id).count(), 1)
                                with self.assertRaises(PointsFundingPlanError):
                                    verify_order_funding(order_id)
                                unknown = funding_status()
                                self.assertEqual((unknown.status_code, unknown.data['errorCode']),
                                                 (409, 'POINTS_FUNDING_REVIEW'))
                                queued = next(row for row in funding_queue().data['data']['list'] if row['order_id'] == order_id)
                                self.assertEqual((queued['funding_status'], queued['required_units'],
                                                  queued['allocated_units'], queued['remaining_units']),
                                                 ('REVIEW', None, None, None))
                                from orders.settlement import SettlementError
                                with self.assertRaises(SettlementError):
                                    process_order(order_id)
                                self.assertEqual(get_wallet(account=affected).balance, 0)
                                self.assertEqual(get_wallet(account=second_account).balance, 0)
                                recovery = finance.client.post(
                                    f'/api/admin/platform/points-funding/{slug}/{operation.pk}/recover', {}, format='json',
                                    HTTP_AUTHORIZATION='Bearer ' + finance.token)
                                self.assertEqual(recovery.status_code, 200, recovery.data)
                                self.assertEqual(recovery.data['data']['amount'], amount)
                            response = finance.client.post(fund_url, partial_request, format='json',
                                HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(response.status_code, 200, response.data)
                            repeated = finance.client.post(fund_url, partial_request, format='json',
                                HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(repeated.status_code, 200, repeated.data)
                            self.assertEqual(repeated.data, response.data)
                            self.assertEqual(verify_order_funding(order_id).remaining, status.remaining - amount)
                            self.assertEqual(funding_status().data['data']['remaining_units'], status.remaining - amount)
                            self.assertFalse(process_order(order_id))
                            self.assertEqual(get_wallet(account=affected).balance, 0)
                            response = finance.client.post(fund_url, {'request_id': str(uuid4()),
                                'order_id': order_id, 'amount': status.remaining - amount}, format='json',
                                HTTP_AUTHORIZATION='Bearer ' + finance.token)
                            self.assertEqual(response.status_code, 200, response.data)
                            self.assertTrue(verify_order_funding(order_id).fully_funded)
                            self.assertTrue(funding_status().data['data']['fully_funded'])
                            self.assertNotIn(order_id, [row['order_id'] for row in funding_queue().data['data']['list']])
                            self.assertTrue(process_order(order_id))
                            self.assertFalse(process_order(order_id))
                            self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                            self.assertEqual(get_wallet(account=second_account).balance, income_b - 200 - refund)
                            final_status = funding_status()
                            self.assertEqual(final_status.status_code, 200, final_status.data)
                            self.assertTrue(final_status.data['data']['fully_funded'])
                            item = next(row for row in self.ok(self.client.get('/api/wallet/reports/'))
                                        if row['id'] == report.pk)
                            self.assertEqual(item['settlement_status'], 'SETTLED')
                            self.assertIs(item['platform_funding_insufficient'], False)
                        return
                    first = report if second_first else report_a
                    last = report_a if second_first else report
                    with patch('django.utils.timezone.now', return_value=first.submitted_at + timedelta(hours=73)):
                        self.assertFalse(process_order(order_id))
                        self.assertEqual(get_wallet(account=affected).balance, 0 if second_first else income_a - 1000)
                        self.assertEqual(get_wallet(account=second_account).balance,
                                         income_b - 200 - refund if second_first else 0)
                    with patch('django.utils.timezone.now', return_value=last.submitted_at + timedelta(hours=73)):
                        self.assertTrue(process_order(order_id))
                        self.assertFalse(process_order(order_id))
                        self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                        self.assertEqual(get_wallet(account=second_account).balance,
                                         income_b - 200 - refund + (800 if with_remaining_addon else 0))
                        self.assertEqual(self.ok(self.client.get(execution_url, request)), result)
            return
        with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                return_value=report_a.submitted_at + timedelta(hours=73)):
            self.assertFalse(process_order(order_id))
            self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
            self.assertEqual(get_wallet(account=second_account).balance, 0)
            if concurrency:
                self.run_followup_race(order_id, followup_case, affected, second, second_account,
                                       income_a, income_b, report, boss_account)
                return
            from orders.ordinary_addon_followup_preview import preview_case_locked, preview_locked
            from orders.settlement import SettlementError
            from stores.db import transaction
            from wallet.models import Transaction
            from orders.models import Order
            order = Order.objects.get(pk=order_id)
            original = (Transaction.objects.count(), order.refund_records.count(),
                        get_wallet(account=affected).balance, get_wallet(account=second_account).balance)
            with transaction.atomic():
                preview = preview_locked(order_id, account_id=second_account.pk, refund_units=1000)
                self.assertEqual(preview_case_locked(followup_case.pk, refund_units=1000), preview)
                self.assertEqual((preview.prior_settled_account_id, preview.account_id, preview.refund_units,
                                  preview.pending_deduction, preview.retained_main_income, preview.insufficient),
                                 (affected.pk, second_account.pk, 1000, 1000, income_b - 1200, 0))
                self.assertEqual(preview.returned_principal + preview.returned_gift +
                                 preview.returned_compensation, 1000)
                self.assertEqual(preview.inviter_income,
                                 (order.amount - 2000) * boss_account.inviter_commission_rate // 100)
                followup_case.affected_provider_account_ids = [affected.pk]
                followup_case.save(update_fields=['affected_provider_account_ids'])
                with self.assertRaises(SettlementError):
                    preview_case_locked(followup_case.pk, refund_units=1000)
                followup_case.affected_provider_account_ids = [second_account.pk]
                followup_case.save(update_fields=['affected_provider_account_ids'])
                original_created_at = followup_case.created_at
                followup_case.created_at = order.completed_at + timedelta(hours=order.service_aftersale_hours)
                followup_case.save(update_fields=['created_at'])
                with self.assertRaises(SettlementError):
                    preview_case_locked(followup_case.pk, refund_units=1000)
                followup_case.created_at = original_created_at
                followup_case.save(update_fields=['created_at'])
                with self.assertRaises(SettlementError):
                    preview_locked(order_id, account_id=affected.pk, refund_units=1000)
                with self.assertRaises(SettlementError):
                    preview_locked(order_id, account_id=second_account.pk, refund_units=order.amount)
                player_wallet = get_wallet(account=second_account, user=second, for_update=True)
                player_wallet.balance = 100
                player_wallet.save(update_fields=['balance'])
                profile = second.escort_profile
                profile.deposit_paid = 200
                profile.save(update_fields=['deposit_paid'])
                unfunded = preview_locked(order_id, account_id=second_account.pk, refund_units=10000)
                excess = 10000 - (income_b - 200)
                self.assertGreater(excess, 300)
                self.assertEqual((unfunded.pending_deduction, unfunded.wallet_deduction,
                                  unfunded.deposit_deduction, unfunded.insufficient),
                                 (income_b - 200, 100, 200, excess - 300))
                transaction.set_rollback(True)
            self.assertEqual((Transaction.objects.count(), order.refund_records.count(),
                              get_wallet(account=affected).balance, get_wallet(account=second_account).balance), original)
            self.login(second)
            item = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
            self.assertEqual(item['settlement_status'], 'WAITING')
            self.login(self.admin)
            preview_api = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-preview/',
                                                  {'refund_units': 1000}))
            self.assertEqual(preview_api['provider_terms'], [{'account_id': second_account.pk,
                'original_income_units': income_b - 200, 'burden_units': 1000,
                'retained_units': income_b - 1200, 'additional_units': 0}])
            self.assertTrue(preview_api['read_only'])
            self.assertFalse(preview_api['agreement_available'])
            self.assertFalse(preview_api['execution_available'])
            funding_api = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-funding/',
                                                  {'refund_units': 1000}))
            self.assertEqual(funding_api['providers'][0]['pending_income_units'], 1000)
            self.assertFalse(funding_api['executable'])
            available = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/'))
            self.assertTrue(available['followup_preview_only'])
            self.assertFalse(available['execution_available'])
            with patch('orders.negotiation_followup.followup_enabled', return_value=True), patch(
                    'orders.negotiation_followup.completed_followup_execution_enabled', return_value=True):
                available = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/'))
                self.assertTrue(available['followup_preview_only'])
                self.assertFalse(available['execution_available'])
                execution = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-execution/'))
                self.assertFalse(execution['execution_enabled'])
            blocked = self.client.post(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/', {
                'request_id': 'cannot-agree-until-supported', 'expected_version': available['version'],
                'refund_units': 1000, 'provider_terms': [{'account_id': second_account.pk,
                    'retained_units': income_b - 1200}], 'explanation': '核对后续退款'}, format='json')
            self.assertNotEqual(blocked.data.get('code', 400), 0)
            blocked_execution = self.client.post(f'/api/admin/aftersales/{followup_case.pk}/negotiation-execution/', {
                'proposal_id': 999999, 'client_request_id': 'cannot-execute-before-support'}, format='json')
            self.assertNotEqual(blocked_execution.data.get('code', 400), 0)
            with transaction.atomic(), override_settings(ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True):
                agreement = self.ok(self.client.post(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/', {
                    'request_id': 'isolated-followup-agreement', 'expected_version': available['version'],
                    'refund_units': 1000, 'provider_terms': [{'account_id': second_account.pk,
                        'retained_units': income_b - 1200}], 'explanation': '双方确认后续退款'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                self.login(self.boss)
                self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                self.login(second)
                accepted = self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                self.assertEqual(accepted['status'], 'ACCEPTED')
                self.assertEqual((Transaction.objects.count(), order.refund_records.count(),
                                  get_wallet(account=affected).balance, get_wallet(account=second_account).balance), original)
                from orders.ordinary_addon_followup_posting import post_locked
                with override_settings(ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True):
                    buyer = get_wallet(account=boss_account, user=self.boss)
                    before_buyer = buyer.balance
                    from orders.ordinary_addon_followup_termination import execute_and_close
                    with override_settings(ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                                           ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True), patch(
                            'orders.ordinary_addon_followup_release.record_locked',
                            side_effect=RuntimeError('结案补资失败')):
                        with self.assertRaisesMessage(RuntimeError, '结案补资失败'):
                            execute_and_close(followup_case.pk, agreement['id'],
                                operator=self.accounts[self.admin.pk], legacy_operator=self.admin,
                                request_id='isolated-followup-refund')
                    buyer.refresh_from_db()
                    followup_case.refresh_from_db()
                    self.assertEqual((buyer.balance, followup_case.status, order.refund_records.count()),
                                     (before_buyer, 'OPEN', 1))
                    with patch('engagement.funding_wallet.refund_locked_order_funding',
                               side_effect=RuntimeError('退款来源写入失败')):
                        with self.assertRaisesMessage(RuntimeError, '退款来源写入失败'):
                            post_locked(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                        legacy_operator=self.admin, request_id='isolated-followup-refund')
                    buyer.refresh_from_db()
                    self.assertEqual(buyer.balance, before_buyer)
                    self.assertEqual(order.refund_records.count(), 1)
                    if with_funding:
                        from orders.ordinary_addon_followup_release import record_locked
                        with override_settings(ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True):
                            with self.assertRaisesMessage(RuntimeError, '补资释放失败'):
                                with transaction.atomic():
                                    temporary = post_locked(followup_case.pk, agreement['id'],
                                        operator=self.accounts[self.admin.pk], legacy_operator=self.admin,
                                        request_id='isolated-followup-refund')
                                    with patch('orders.ordinary_addon_followup_release.OrderAddonFundingRelease.objects.bulk_create',
                                               side_effect=RuntimeError('补资释放失败')):
                                        record_locked(temporary)
                            buyer.refresh_from_db()
                            self.assertEqual(buyer.balance, before_buyer)
                            self.assertEqual(order.refund_records.count(), 1)
                    link = post_locked(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                       legacy_operator=self.admin, request_id='isolated-followup-refund')
                    buyer.refresh_from_db()
                    self.assertEqual(buyer.balance, before_buyer + 1000)
                    self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                    self.assertEqual(get_wallet(account=second_account).balance, 0)
                    self.assertEqual(link.execution.shares.get().retained_income, income_b - 1200)
                    from orders.ordinary_addon_followup_release import record_locked
                    with override_settings(ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True):
                        release = record_locked(link)
                        if with_funding:
                            self.assertIsNotNone(release)
                            self.assertEqual(release.released_amount, preview.funding_to_release)
                            self.assertGreater(release.released_amount, 0)
                            self.assertEqual(record_locked(link).pk, release.pk)
                            overview = finance.reconciliation(slug=slug)
                            self.assertEqual(overview.status_code, 200, overview.data)
                            self.assertEqual(overview.data['data']['claims'][0]['receipt']['unallocated_amount'],
                                             1000 + release.released_amount)
                        else:
                            self.assertIsNone(release)
                            self.assertIsNone(record_locked(link))
                    again = post_locked(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                        legacy_operator=self.admin, request_id='isolated-followup-refund')
                    self.assertEqual(again.pk, link.pk)
                    buyer.refresh_from_db()
                    self.assertEqual(buyer.balance, before_buyer + 1000)
                    from rest_framework.exceptions import ValidationError
                    from orders.negotiation_execution import _allocation_hash
                    from orders.ordinary_addon_followup_posting import verify_posting_locked
                    with transaction.atomic():
                        link.projected_ledger['estimate']['store_income'] += 1
                        link.ledger_hash = _allocation_hash(link.projected_ledger)
                        link.save(update_fields=['projected_ledger', 'ledger_hash'])
                        with self.assertRaises(ValidationError):
                            verify_posting_locked(link)
                        transaction.set_rollback(True)
                    with override_settings(ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                                           ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True):
                        closed = execute_and_close(followup_case.pk, agreement['id'],
                            operator=self.accounts[self.admin.pk], legacy_operator=self.admin,
                            request_id='isolated-followup-refund')
                        followup_case.refresh_from_db()
                        self.assertEqual(followup_case.status, 'PARTIALLY_REFUNDED')
                        self.assertEqual(closed.pk, link.pk)
                        self.assertEqual(execute_and_close(followup_case.pk, agreement['id'],
                            operator=self.accounts[self.admin.pk], legacy_operator=self.admin,
                            request_id='isolated-followup-refund').pk, closed.pk)
                        self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                        with override_settings(ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True):
                            self.assertFalse(process_order(order_id))
                            self.assertEqual(get_wallet(account=second_account).balance, 0)
                            with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                                from orders.services import _credit_wallet
                                def fail_final_store(**kwargs):
                                    if kwargs['tx_type'] == 'SHOP_INCOME':
                                        raise RuntimeError('门店收益写入失败')
                                    return _credit_wallet(**kwargs)
                                with patch('orders.services._credit_wallet', side_effect=fail_final_store):
                                    with self.assertRaisesMessage(RuntimeError, '门店收益写入失败'):
                                        process_order(order_id)
                                self.assertEqual(get_wallet(account=second_account).balance, 0)
                                self.assertEqual(get_wallet(account=inviter).balance, 0)
                                self.assertTrue(process_order(order_id))
                                self.assertFalse(process_order(order_id))
                                self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                                self.assertEqual(get_wallet(account=second_account).balance,
                                                 income_b - 1200 + (800 if with_remaining_addon else 0))
                                self.assertEqual(get_wallet(account=inviter).balance,
                                    (order.amount - 2000) * boss_account.inviter_commission_rate // 100)
                                from engagement.models import ConsumptionConfirmation
                                confirmation = ConsumptionConfirmation.objects.get(order_id=order_id)
                                self.assertEqual((confirmation.amount, confirmation.refunded_amount), (order.amount, 2000))
                                if with_remaining_addon:
                                    from orders.models import OrderAddonConsumption, OrderAddonSettlement
                                    self.assertEqual(OrderAddonSettlement.objects.get(payment_id=remaining_addon_id).amount, 800)
                                    self.assertTrue(OrderAddonConsumption.objects.filter(payment_id=remaining_addon_id).exists())
                                self.login(second)
                                shown = next(row for row in self.ok(self.client.get('/api/wallet/reports/'))
                                             if row['id'] == report.pk)
                                self.assertEqual((shown['settlement_status'], shown['expected_income']),
                                                 ('SETTLED', income_b - 1200))
                                self.login(self.provider)
                                shown_a = next(row for row in self.ok(self.client.get('/api/wallet/reports/'))
                                               if row['id'] == report_a.pk)
                                self.assertEqual((shown_a['settlement_status'], shown_a['expected_income']),
                                                 ('SETTLED', income_a - 1000))
                                self.login(self.admin)
                                detail = self.ok(self.client.get(f'/api/admin/reports/{report.pk}/'))
                                self.assertFalse(detail['platform_funding_insufficient'])
                transaction.set_rollback(True)
            with transaction.atomic(), override_settings(
                    ORDINARY_ADDON_FOLLOWUP_ENDPOINT_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_ZERO_INTERNAL_TESTING=True, NEGOTIATION_ZERO_EXECUTION_ENABLED=True,
                    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True):
                self.login(self.admin)
                url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/'
                info = self.ok(self.client.get(url))
                with override_settings(ORDINARY_ADDON_FOLLOWUP_ZERO_INTERNAL_TESTING=False):
                    disabled_quote = self.ok(self.client.get(
                        f'/api/admin/aftersales/{followup_case.pk}/negotiation-preview/', {'refund_units': 0}))
                    self.assertFalse(disabled_quote['agreement_available'])
                    disabled = self.client.post(url, {'request_id': 'api-zero-disabled',
                        'expected_version': info['version'], 'refund_units': 0,
                        'provider_terms': [{'account_id': second_account.pk, 'retained_units': income_b - 200}],
                        'explanation': '零退款尚未开放'}, format='json')
                    self.assertNotEqual(disabled.data.get('code', 400), 0)
                quote = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-preview/',
                                                {'refund_units': 0}))
                self.assertTrue(quote['agreement_available'])
                agreement = self.ok(self.client.post(url, {'request_id': 'api-zero-followup-agreement',
                    'expected_version': info['version'], 'refund_units': 0,
                    'provider_terms': [{'account_id': second_account.pk, 'retained_units': income_b - 200}],
                    'explanation': '双方确认无退款结案'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                self.login(self.admin)
                execution_url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-execution/'
                execution_request = {'proposal_id': agreement['id'], 'client_request_id': 'api-zero-followup'}
                before = (get_wallet(account=boss_account).balance, order.refund_records.count(), Transaction.objects.count())
                with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('结案审计失败')):
                    with self.assertRaisesMessage(RuntimeError, '结案审计失败'):
                        self.client.post(execution_url, execution_request, format='json')
                followup_case.refresh_from_db()
                self.assertEqual(followup_case.status, 'OPEN')
                self.assertEqual((get_wallet(account=boss_account).balance, order.refund_records.count(), Transaction.objects.count()), before)
                result = self.ok(self.client.post(execution_url, execution_request, format='json'))
                self.assertEqual((result['refund_units'], result['refund_id']), (0, None))
                self.assertEqual((get_wallet(account=boss_account).balance, order.refund_records.count(), Transaction.objects.count()), before)
                self.assertEqual(self.ok(self.client.post(execution_url, execution_request, format='json')), result)
                self.assertEqual(self.ok(self.client.get(execution_url, execution_request)), result)
                followup_case.refresh_from_db()
                self.assertEqual(followup_case.status, 'RESOLVED')
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    self.assertTrue(process_order(order_id))
                    self.assertFalse(process_order(order_id))
                    self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                    self.assertEqual(get_wallet(account=second_account).balance,
                                     income_b - 200 + (800 if with_remaining_addon else 0))
                    from engagement.models import ConsumptionConfirmation
                    self.assertEqual(ConsumptionConfirmation.objects.get(order_id=order_id).refunded_amount, 1000)
                transaction.set_rollback(True)
            with transaction.atomic(), override_settings(ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True,
                                                         ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True):
                provider_wallet = get_wallet(account=second_account, user=second, for_update=True)
                wallet_before = provider_wallet.balance
                provider_wallet.balance += 1000
                provider_wallet.save(update_fields=['balance'])
                Transaction.objects.create(wallet=provider_wallet, tx_type='ADJUST', amount=1000,
                    balance_before=wallet_before, balance_after=provider_wallet.balance, remark='隔离测试既有余额')
                second_refund = income_b
                self.login(self.admin)
                agreement = self.ok(self.client.post(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/', {
                    'request_id': 'isolated-wallet-agreement', 'expected_version': available['version'],
                    'refund_units': second_refund, 'provider_terms': [{'account_id': second_account.pk,
                        'retained_units': 0}], 'explanation': '钱包承担不足部分'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                from orders.ordinary_addon_followup_posting import post_locked
                link = post_locked(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                   legacy_operator=self.admin, request_id='isolated-wallet-refund')
                share = link.execution.shares.get()
                provider_wallet.refresh_from_db()
                self.assertEqual((share.pending_deduction, share.wallet_deduction, share.deposit_deduction,
                                  share.retained_income, provider_wallet.balance), (income_b - 200, 200, 0, 0, 800))
                self.assertEqual(share.wallet_transaction.amount, -200)
                self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                from orders.ordinary_addon_followup_termination import execute_and_close
                with override_settings(ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                                       ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True,
                                       ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True):
                    execute_and_close(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                      legacy_operator=self.admin, request_id='isolated-wallet-refund')
                    with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                        self.assertTrue(process_order(order_id))
                        provider_wallet.refresh_from_db()
                        self.assertEqual(provider_wallet.balance, 800 + (800 if with_remaining_addon else 0))
                        self.assertFalse(process_order(order_id))
                transaction.set_rollback(True)
            with transaction.atomic(), override_settings(
                    ORDINARY_ADDON_FOLLOWUP_ENDPOINT_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
                    ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True,
                    REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True):
                self.login(self.admin)
                url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/'
                info = self.ok(self.client.get(url))
                self.assertFalse(info['followup_preview_only'])
                self.assertTrue(info['execution_available'])
                case_detail = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/'))
                self.assertTrue(case_detail['completed_followup_supported'])
                quote = self.ok(self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-preview/',
                                                {'refund_units': 1000}))
                self.assertTrue(quote['agreement_available'])
                agreement = self.ok(self.client.post(url, {'request_id': 'api-followup-agreement',
                    'expected_version': info['version'], 'refund_units': 1000,
                    'provider_terms': [{'account_id': second_account.pk, 'retained_units': income_b - 1200}],
                    'explanation': '双方同意后的追加退款'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                self.login(self.provider)
                denied = self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json')
                self.assertNotEqual(denied.data.get('code', 400), 0)
                self.login(self.boss)
                self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                execution_url = f'/api/admin/aftersales/{followup_case.pk}/negotiation-execution/'
                execution_request = {'proposal_id': agreement['id'], 'client_request_id': 'api-followup-refund'}
                self.login(self.admin)
                unconfirmed = self.client.post(execution_url, execution_request, format='json')
                self.assertNotEqual(unconfirmed.data.get('code', 400), 0)
                self.login(second)
                self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                self.login(self.admin)
                result = self.ok(self.client.post(execution_url, execution_request, format='json'))
                self.assertEqual(result['refund_units'], 1000)
                self.assertEqual(self.ok(self.client.post(execution_url, execution_request, format='json')), result)
                self.assertEqual(self.ok(self.client.get(execution_url, execution_request)), result)
                with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                    self.assertTrue(process_order(order_id))
                    self.assertFalse(process_order(order_id))
                    self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                    self.assertEqual(get_wallet(account=second_account).balance,
                                     income_b - 1200 + (800 if with_remaining_addon else 0))
                    self.assertEqual(self.ok(self.client.get(execution_url, execution_request)), result)
                    prior_request = {'proposal_id': proposal['id'], 'client_request_id': 'report-main-execute'}
                    prior_readback = self.ok(self.client.get(execution_url, prior_request))
                    self.assertEqual(prior_readback['refund_units'], 1000)
                    self.assertEqual(self.ok(self.client.post(execution_url, prior_request, format='json')),
                                     prior_readback)
                transaction.set_rollback(True)
            with transaction.atomic(), override_settings(ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True,
                                                         ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True):
                profile = second.escort_profile
                profile.deposit_paid = 0
                profile.save(update_fields=['deposit_paid'])
                self.login(self.admin)
                agreement = self.ok(self.client.post(f'/api/admin/aftersales/{followup_case.pk}/negotiation-proposal/', {
                    'request_id': 'isolated-shortfall-agreement', 'expected_version': available['version'],
                    'refund_units': income_b, 'provider_terms': [{'account_id': second_account.pk,
                        'retained_units': 0}], 'explanation': '资金不足暂缓退款'}, format='json'))
                decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
                for user in (self.boss, second):
                    self.login(user)
                    self.ok(self.client.post(decision_url, {'proposal_id': agreement['id'], 'accepted': True}, format='json'))
                from rest_framework.exceptions import ValidationError
                from orders.ordinary_addon_followup_posting import post_locked
                with self.assertRaises(ValidationError):
                    post_locked(followup_case.pk, agreement['id'], operator=self.accounts[self.admin.pk],
                                legacy_operator=self.admin, request_id='isolated-shortfall-refund')
                self.assertEqual(order.refund_records.count(), 1)
                self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
                transaction.set_rollback(True)
            self.login(self.boss)
            forbidden = self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-funding/',
                                        {'refund_units': 1000})
            self.assertNotEqual(forbidden.status_code, 200)
            self.login(second)
            forbidden = self.client.get(f'/api/admin/aftersales/{followup_case.pk}/negotiation-preview/',
                                        {'refund_units': 1000})
            self.assertNotEqual(forbidden.status_code, 200)
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/aftersales/{followup_case.pk}/resolve/',
                                     {'resolution': '结束本次测试售后'}, format='json'))
        with store_context(current_store() or resolve_store()), patch('django.utils.timezone.now',
                return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
            self.assertFalse(process_order(order_id))
            self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
            self.assertEqual(get_wallet(account=second_account).balance,
                             income_b - 200 + (800 if with_remaining_addon else 0))
            self.login(second)
            item = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
            self.assertEqual(item['settlement_status'], 'SETTLED')
        evidence = OrderPointsSettlement.objects.get(order_id=order_id).ordinary_refund_addon_evidence
        self.assertEqual({row['account_id']: row['main_income'] for row in evidence.facts['people']},
                         {affected.pk: income_a - 1000, second_account.pk: income_b - 200})
        self.assertEqual([row['income'] for row in evidence.facts['addons']], [800] if with_remaining_addon else [])

    def run_followup_race(self, order_id, case, affected, second, second_account, income_a, income_b, report, boss_account):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timedelta
        from threading import Barrier
        from unittest.mock import patch
        from django.db import connections
        from rest_framework.test import APIClient
        from club_accounts.tokens import issue_account_tokens
        from engagement.confirmation import process_order
        from orders.models import NegotiationFollowupLink, Order
        from stores.context import current_store, resolve_store, store_context
        from wallet.services import get_wallet

        self.login(self.admin)
        proposal_url = f'/api/admin/aftersales/{case.pk}/negotiation-proposal/'
        info = self.ok(self.client.get(proposal_url))
        self.assertTrue(info['execution_available'])
        proposal = self.ok(self.client.post(proposal_url, {'request_id': 'mysql-followup-agreement',
            'expected_version': info['version'], 'refund_units': 1000,
            'provider_terms': [{'account_id': second_account.pk, 'retained_units': income_b - 1200}],
            'explanation': '并发测试双方确认'}, format='json'))
        decision_url = f'/api/orders/orders/{order_id}/negotiation-proposal/'
        for user in (self.boss, second):
            self.login(user)
            self.ok(self.client.post(decision_url, {'proposal_id': proposal['id'], 'accepted': True}, format='json'))
        token = issue_account_tokens(self.accounts[self.admin.pk])[0]
        store = current_store() or resolve_store()
        buyer_before = get_wallet(account=boss_account).balance
        execution_url = f'/api/admin/aftersales/{case.pk}/negotiation-execution/'
        request = {'proposal_id': proposal['id'], 'client_request_id': 'mysql-followup-refund'}
        barrier = Barrier(2)

        def execute():
            try:
                with store_context(store):
                    client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
                    barrier.wait(timeout=15)
                    response = client.post(execution_url, request, format='json')
                    return response.status_code, response.data
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(), range(2)))
        self.assertEqual([item[0] for item in results], [200, 200], results)
        self.assertEqual(results[0][1], results[1][1])
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.refund_records.count(), 2)
        self.assertEqual(NegotiationFollowupLink.objects.filter(order=order).count(), 1)
        self.assertEqual(get_wallet(account=boss_account).balance, buyer_before + 1000)
        self.assertEqual(get_wallet(account=affected).balance, income_a - 1000)
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertTrue(process_order(order_id))
            self.assertFalse(process_order(order_id))
        self.assertEqual(get_wallet(account=second_account).balance, income_b - 1200)


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
    ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
    ORDINARY_REFUNDED_ADDON_FUNDING_INTERNAL_TESTING=True, ORDINARY_REFUNDED_ADDON_SETTLEMENT_INTERNAL_TESTING=True,
    ORDINARY_REFUNDED_ADDON_PERSON_POSTING_INTERNAL_TESTING=True, ORDINARY_REFUNDED_ADDON_FINALIZE_INTERNAL_TESTING=True,
    ORDINARY_ADDON_FOLLOWUP_PREVIEW_INTERNAL_TESTING=True, ORDINARY_ADDON_FOLLOWUP_ENDPOINT_INTERNAL_TESTING=True,
    ORDINARY_ADDON_FOLLOWUP_AGREEMENT_INTERNAL_TESTING=True, ORDINARY_ADDON_FOLLOWUP_POSTING_INTERNAL_TESTING=True,
    ORDINARY_ADDON_FOLLOWUP_RELEASE_INTERNAL_TESTING=True, ORDINARY_ADDON_FOLLOWUP_TERMINATION_INTERNAL_TESTING=True,
    ORDINARY_ADDON_FOLLOWUP_SETTLEMENT_INTERNAL_TESTING=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
    REFUND_DEPOSIT_RECOVERY_ENFORCE_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class OrdinaryAddonFollowupMySQLRaceTest(APITransactionTestCase):
    databases = {'default', 'store_b'}
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    exercise_addon_followup = OrdinaryAddonNegotiatedReportTest.exercise_addon_followup
    run_followup_race = OrdinaryAddonNegotiatedReportTest.run_followup_race

    def test_payroll_barrier_precedes_real_followup_refund_locks(self):
        import os
        from django.db import connection
        if (connection.vendor != 'mysql'
                or os.environ.get('DJANGO_SETTINGS_MODULE') != 'config.ordinary_followup_mysql_test_settings'):
            self.skipTest('requires dedicated isolated two-store MySQL')
        from concurrent.futures import ThreadPoolExecutor
        from time import monotonic, sleep
        from django.db import connections
        from orders.models import Order, OrderRefund
        from orders.points_checkout_test import require_isolated_checkout
        from console.payroll_event_barrier import lock_payroll_events
        from stores.context import current_store, resolve_store, store_context
        from stores.db import transaction

        original = self.run_followup_race
        def gated_race(*args):
            order_id = args[0]
            store = current_store() or resolve_store()
            with store_context(store):
                require_isolated_checkout()
            def run():
                try:
                    with store_context(store):
                        return original(*args)
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with store_context(store), transaction.atomic():
                    lock_payroll_events()
                    pending = pool.submit(run)
                    deadline, found = monotonic() + 20, False
                    while monotonic() < deadline:
                        db = connections[store.database_alias]
                        with db.cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            found = any(row[3] == db.settings_dict['NAME'] and row[4] == 'Query'
                                and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                                and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                        if found:
                            break
                        if pending.done():
                            self.fail(f'Followup returned before barrier: {pending.result()}')
                        sleep(0.05)
                    self.assertTrue(found, 'Followup refund did not wait for payroll barrier')
                    Order.objects.select_for_update(nowait=True).get(pk=order_id)
                    self.assertEqual(OrderRefund.objects.filter(order_id=order_id).count(), 1)
                pending.result(timeout=45)
        self.run_followup_race = gated_race
        try:
            with override_settings(PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True):
                self.exercise_addon_followup(with_funding=False, concurrency=True)
        finally:
            del self.run_followup_race

    def test_concurrent_same_refund_is_posted_once(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('requires disposable MySQL with real row locks')
        self.exercise_addon_followup(with_funding=False, concurrency=True)

    def test_unpaid_first_provider_after_second_provider_settles(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('requires disposable MySQL with real row locks')
        self.exercise_addon_followup(with_funding=False, prior_unpaid=True, second_first=True)

    def test_refund_then_partial_funding_and_settlement(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('requires disposable MySQL with real row locks')
        self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True, zero_initial=True)

    def test_xinan_funding_recovers_after_control_commit_failure(self):
        from django.db import connection
        if connection.vendor != 'mysql':
            self.skipTest('requires disposable MySQL with separate tenant database')
        from stores.context import store_context
        from stores.tests_hosts import xinan_store
        with store_context(xinan_store()):
            flow.ReleaseFlowTest.setUp(self)
            login = self.login
            def bound_login(user):
                login(user)
                self.client.defaults['HTTP_HOST'] = 'admin.esports.club' if user.pk == self.admin.pk else 'xa.esports.club'
            self.login = bound_login
            self.exercise_addon_followup(with_funding=True, prior_unpaid=True, underfunded=True,
                                         zero_initial=True, simulate_funding_failure=True)
