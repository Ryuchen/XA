from datetime import timedelta
import json
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from engagement.tasks import confirm_due_consumption
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order, OrderProvider
from orders.refunds import can_request_aftersale
from engagement.confirmation import refund_within_aftersale_window
from orders.tests.factories import make_provider
from orders.tests import test_release_flow as flow
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Wallet
from wallet.settlement_display import report_settlement_status


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ServiceSettlementClockTests(APITestCase):
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def setUp(self):
        flow.ReleaseFlowTest.setUp(self)
        profile = self.provider.escort_profile
        profile.deposit_mode = 'EARNINGS'
        profile.save(update_fields=['deposit_mode'])

    def test_service_waits_for_both_clocks_without_a_second_freeze(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((order.service_aftersale_hours, order.service_freeze_hours), (72, 72))
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=71)):
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        with patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 1)
            self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())

    def test_aftersale_deadline_rejects_new_case_but_keeps_timely_open_case(self):
        order = self.new_order()
        self.finish(order)
        order.refresh_from_db()
        self.login(self.boss)
        deadline = order.completed_at + timedelta(hours=order.service_aftersale_hours)
        with patch('django.utils.timezone.now', return_value=deadline):
            self.assertFalse(can_request_aftersale(order))
            self.assertFalse(refund_within_aftersale_window(order))
            denied = self.client.post(f'/api/orders/orders/{order.pk}/aftersale/',
                                      {'reason': '核实服务'}, format='json')
            self.assertNotEqual(denied.data.get('code'), 0)
        with patch('django.utils.timezone.now', return_value=deadline - timedelta(minutes=1)):
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/',
                                     {'reason': '核实服务'}, format='json'))
        with patch('django.utils.timezone.now', return_value=deadline + timedelta(hours=1)):
            self.assertFalse(can_request_aftersale(order))
            self.assertTrue(refund_within_aftersale_window(order))

    def test_zero_freeze_still_uses_order_deposit_snapshot(self):
        profile = self.provider.escort_profile
        profile.deposit_paid = 0
        profile.save(update_fields=['deposit_paid'])
        SystemConfig.objects.update_or_create(key='settlement_clock_rules', defaults={'value': json.dumps({
            'aftersale_hours': 0, 'income_freeze_hours': 0, 'version': 1})})
        SystemConfig.objects.update_or_create(key='provider_deposit_income_percent', defaults={'value': '60'})
        order = self.new_order()
        SystemConfig.objects.filter(key='provider_deposit_income_percent').update(value='20')
        SystemConfig.objects.filter(key='settlement_clock_rules').update(value=json.dumps({
            'aftersale_hours': 72, 'income_freeze_hours': 72, 'version': 2}))
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        order.refresh_from_db()
        self.provider.escort_profile.refresh_from_db()
        self.assertEqual((order.service_aftersale_hours, order.service_freeze_hours,
                          order.service_deposit_percent), (0, 0, 60))
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(self.provider.escort_profile.deposit_paid, 4800)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 3200)
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())

    def test_rejected_report_restarts_freeze_from_resubmission(self):
        order = self.new_order()
        report = self.finish(order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        report.refresh_from_db()
        first_time = report.submitted_at
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/reject/', {'audit_remark': '请补充说明'}, format='json'))
        with patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=24)):
            self.login(self.provider)
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.login(self.admin)
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        report.refresh_from_db()
        self.assertEqual(report.submitted_at, first_time + timedelta(hours=24))
        with patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        with patch('django.utils.timezone.now', return_value=first_time + timedelta(hours=97)):
            self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)

    def test_double_order_releases_each_approved_share_once(self):
        self.service.price = 5000
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['price', 'billing_provider_count'])
        second = make_provider(deposit_mode='EARNINGS')
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        second.escort_profile.account = second_account
        second.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'double-clock-test', 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk},
                          {'provider_id': second_account.pk}],
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': flow.screenshot()}, format='multipart'))
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': flow.screenshot()}, format='multipart'))
        self.login(self.provider)
        report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
        self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/", {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
        first = ProviderReport.objects.get(pk=report['id'])
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{first.pk}/approve/'))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 0)
            order.refresh_from_db()
            self.assertIsNone(order.settled_at)
            self.assertIsNotNone(OrderProvider.objects.get(order=order, provider=self.provider).settled_at)
            first.refresh_from_db()
            self.assertEqual(report_settlement_status(first), 'SETTLED')
            self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
            self.login(second)
            second_report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
            self.ok(self.client.post(f"/api/wallet/reports/{second_report['id']}/images/",
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{second_report['id']}/submit/"))
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/reports/{second_report['id']}/approve/"))
            self.assertIsNone(OrderProvider.objects.get(order=order, provider=second).settled_at)
        with patch('django.utils.timezone.now', return_value=first.submitted_at + timedelta(hours=147)):
            self.assertEqual(confirm_due_consumption(), 1)
            self.assertEqual(confirm_due_consumption(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=second).count(), 1)

    def test_targeted_double_aftersale_only_blocks_and_notifies_affected_provider(self):
        self.service.price = 5000
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['price', 'billing_provider_count'])
        second = make_provider(deposit_mode='EARNINGS')
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        second.escort_profile.account = second_account
        second.escort_profile.save(update_fields=['account'])
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'scoped-case-order', 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk},
                          {'provider_id': second_account.pk}],
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        for action, image in [('start', 'entry_image'), ('complete', 'completion_image')]:
            for user in (self.provider, second):
                self.login(user)
                self.ok(self.client.post(f'/api/orders/orders/{order.pk}/{action}/',
                                         {image: flow.screenshot()}, format='multipart'))
        reports = {}
        for user in (self.provider, second):
            self.login(user)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order.pk}))
            reports[user.pk] = report['id']
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/reports/{report['id']}/approve/"))
        self.login(self.boss)
        payload = {'reason': '仅核实第一位陪玩', 'client_request_id': 'case-scope',
                   'affected_provider_account_ids': [self.accounts[self.provider.pk].pk]}
        invalid = {**payload, 'affected_provider_account_ids': [self.accounts[self.admin.pk].pk]}
        self.assertNotEqual(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', invalid, format='json').data.get('code', 400), 0)
        self.assertFalse(order.aftersales.exists())
        with self.captureOnCommitCallbacks(execute=True):
            case = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', payload, format='json'))
        self.assertEqual(case['affected_provider_names'], [self.provider.escort_profile.display_name])
        self.assertEqual(self.ok(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', payload, format='json'))['id'], case['id'])
        from site_messages.models import Message
        notices = Message.objects.filter(related_order_id=order.pk, title='订单售后处理中')
        self.assertTrue(notices.filter(recipient_account=self.accounts[self.provider.pk]).exists())
        self.assertFalse(notices.filter(recipient_account=second_account).exists())
        conflict = {**payload, 'affected_provider_account_ids': [second_account.pk]}
        self.assertEqual(self.client.post(f'/api/orders/orders/{order.pk}/aftersale/', conflict, format='json').status_code, 409)
        for user, expected in [(self.provider, 'OPEN'), (second, '')]:
            self.login(user)
            detail = self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))
            self.assertEqual(detail['aftersale_status'], expected)
            self.assertFalse(detail['can_aftersale'])
            report_rows = self.ok(self.client.get('/api/wallet/reports/'))
            self.assertEqual(next(row for row in report_rows if row['id'] == reports[user.pk])['aftersale_status'], expected)
        self.assertEqual(self.client.get(f'/api/orders/orders/{order.pk}/aftersale/').status_code, 404)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertIsNone(OrderProvider.objects.get(order=order, provider=self.provider).settled_at)
            self.assertIsNotNone(OrderProvider.objects.get(order=order, provider=second).settled_at)
            self.login(self.admin)
            rejected = self.client.get(f'/api/admin/orders/{order.pk}/refund-preview/')
            self.assertEqual(rejected.data.get('errorCode'), 'REFUND_SCOPED_ALLOCATION_REQUIRED')
            self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/resolve/",
                                     {'resolution': '问题已核实'}, format='json'))
            self.assertEqual(confirm_due_consumption(), 1)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).count(), 1)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=second).count(), 1)

    @override_settings(NEGOTIATION_PROPOSAL_ENABLED=True)
    def test_in_service_negotiation_survives_completion_and_other_provider_settlement(self):
        self.service.price = 5000; self.service.billing_provider_count = 2
        self.service.save(update_fields=['price', 'billing_provider_count'])
        second = make_provider(deposit_mode='EARNINGS')
        second_account = get_or_create_account_for_legacy_user(second)
        self.accounts[second.pk] = second_account
        second.escort_profile.account = second_account; second.escort_profile.save(update_fields=['account'])
        role = self.admin.admin_membership.roles.first()
        role.permissions = list(set([*role.permissions, 'support:edit'])); role.save(update_fields=['permissions'])
        self.ok(self.recharge())
        data = self.ok(self.client.post('/api/admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'client_request_id': 'negotiation-completion-order', 'escort_mode': 'DOUBLE',
            'providers': [{'provider_id': self.accounts[self.provider.pk].pk}, {'provider_id': second_account.pk}],
        }, format='json'))
        order = Order.objects.get(pk=data['id'])
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/',
                                     {'entry_image':flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        case = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order.pk,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk],
            'reason':'仅协商第一位陪玩', 'client_request_id':'negotiation-before-completion'}, format='json'))
        preview = self.ok(self.client.get(f"/api/admin/aftersales/{case['id']}/negotiation-preview/", {'refund_units':1000}))
        terms = [{'account_id':item['account_id'], 'retained_units':item['retained_units']} for item in preview['provider_terms']]
        proposal = self.ok(self.client.post(f"/api/admin/aftersales/{case['id']}/negotiation-proposal/", {
            'request_id':'negotiation-before-completion-proposal', 'expected_version':0,
            'refund_units':1000, 'provider_terms':terms, 'explanation':'协商退款'}, format='json'))
        for user in (self.provider, second):
            self.login(user)
            self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/',
                                     {'completion_image':flow.screenshot()}, format='multipart'))
        order.refresh_from_db(); self.assertEqual(order.status, 'COMPLETED')
        for user in (self.provider, second):
            self.login(user)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id':order.pk}))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                     {'kind':'RESULT', 'image':flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
            self.login(self.admin)
            self.ok(self.client.post(f"/api/admin/reports/{report['id']}/approve/"))
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(hours=73)):
            self.assertEqual(confirm_due_consumption(), 0)
            self.assertIsNotNone(OrderProvider.objects.get(order=order, provider=second).settled_at)
            self.assertIsNone(OrderProvider.objects.get(order=order, provider=self.provider).settled_at)
            for user in (self.boss, self.provider):
                self.login(user)
                result = self.ok(self.client.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                    {'proposal_id':proposal['id'], 'accepted':True}, format='json'))
            self.assertEqual(result['status'], 'ACCEPTED')
            self.login(self.admin)
            refused = self.client.post(f"/api/admin/aftersales/{case['id']}/resolve/", {'resolution':'直接结案'}, format='json')
            self.assertNotEqual(refused.data.get('code', 400), 0, refused.data)
            self.assertEqual(confirm_due_consumption(), 0)
        self.assertEqual(order.transactions.filter(tx_type='INCOME', wallet__user=second).count(), 1)
        self.assertFalse(order.transactions.filter(tx_type='INCOME', wallet__user=self.provider).exists())
        self.assertFalse(order.refund_records.exists())
