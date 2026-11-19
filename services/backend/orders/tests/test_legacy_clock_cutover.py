"""Store-local legacy clock cutover with real order and report records."""
import os
import json
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from orders.models import LegacyClockCutover, Order
from orders.tests import test_negotiation_termination as fixture
from wallet.club_rules import DEPOSIT_PERCENT_KEY
from wallet.models import IncomeHold, ProviderReport, SystemConfig, Transaction, Wallet
from wallet.settlement_clocks import CONFIG_KEY


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False)
class LegacyClockCutoverTests(APITestCase):
    account = fixture.NegotiationTerminationTests.account
    client_for = fixture.NegotiationTerminationTests.client_for

    def setUp(self):
        fixture.NegotiationTerminationTests.setUp(self)
        from orders.tests.factories import make_console_user
        operator_user = make_console_user(['order:view', 'deposit_rule:edit'])
        self.cutter = self.account(operator_user)
        self.client = self.client_for(self.cutter)
        self.url = f'/api/admin/orders/{self.order.pk}/legacy-clock-cutover/'
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=None, settlement_mode='COMPLETION',
            service_clock_version=None, service_aftersale_hours=None, service_freeze_hours=None,
            service_deposit_percent=None, created_at=timezone.now()-timedelta(hours=60),
            in_service_at=timezone.now()-timedelta(hours=55))
        self.order.refresh_from_db()
        SystemConfig.objects.update_or_create(key=CONFIG_KEY,
            defaults={'value': json.dumps({'aftersale_hours':48, 'income_freeze_hours':48, 'version':7})})
        SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value':'60'})

    def preview(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def submit(self, preview, key='legacy-cutover'):
        return self.client.post(self.url, {'plan_fingerprint':preview['plan_fingerprint'],
                                          'client_request_id':key}, format='json')

    def test_cutover_snapshots_rules_and_effective_reports_without_changing_funds(self):
        when = timezone.now()-timedelta(hours=50)
        for index, (account, provider) in enumerate(((self.a_account, self.a), (self.b_account, self.b))):
            report = ProviderReport.objects.create(order=self.order, provider=provider, provider_account=account,
                game_name='历史履约', amount=5000, commission_rate=20,
                status='APPROVED' if index == 0 else 'PENDING', submitted_at=when,
                audited_at=when+timedelta(minutes=1) if index == 0 else None,
                auditor_account=self.staff if index == 0 else None, payout_amount=4000 if index == 0 else 0)
            ProviderReport.objects.filter(pk=report.pk).update(created_at=when-timedelta(hours=1))
        before_money = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        before_transactions = Transaction.objects.count()
        preview = self.preview()
        self.assertEqual((preview['state'], preview['facts']['problems']), ('READY', []))
        self.assertFalse(LegacyClockCutover.objects.exists())
        self.assertEqual(Transaction.objects.count(), before_transactions)
        response = self.submit(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual((self.order.settlement_mode, self.order.service_aftersale_hours,
            self.order.service_freeze_hours, self.order.service_clock_version, self.order.service_deposit_percent),
            ('REPORT_APPROVAL', 48, 48, 7, 60))
        self.assertEqual(response.data['data']['rules']['deposit_source'], 'STORE_AT_CUTOVER')
        self.assertEqual(self.order.report_ready_at, when)
        self.assertEqual(self.order.consumption_ready_at, when+timedelta(hours=48))
        self.assertLess(self.order.consumption_ready_at, timezone.now())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_money)
        self.assertEqual(Transaction.objects.count(), before_transactions)
        self.assertFalse(self.order.settled_at)
        self.assertEqual(LegacyClockCutover.objects.get(order=self.order).previous['settlement_mode'], 'COMPLETION')
        SystemConfig.objects.filter(key=CONFIG_KEY).update(value=json.dumps(
            {'aftersale_hours':36, 'income_freeze_hours':36, 'version':8}))
        SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).update(value='40')
        self.assertEqual(self.submit(preview).status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual((self.order.service_aftersale_hours, self.order.service_deposit_percent), (48, 60))
        finished_at = timezone.now()
        completed = self.client_for(self.staff).post(f'/api/admin/orders/{self.order.pk}/legacy-room-completion/', {
            'customer_account_id': self.boss_account.pk, 'provider_account_ids': [self.a_account.pk, self.b_account.pk],
            'confirmed_at': finished_at.isoformat(), 'evidence': '已核验老板本人在旧站确认结单',
            'client_request_id': 'cutover-old-room'}, format='json')
        self.assertEqual(completed.status_code, 200, completed.data)
        self.order.refresh_from_db()
        from engagement.confirmation import aftersale_deadline
        self.assertEqual(aftersale_deadline(self.order), finished_at+timedelta(hours=48))
        self.assertFalse(self.order.settled_at)
        self.assertEqual(Transaction.objects.count(), before_transactions)
        from console.models import AdminAuditLog
        self.assertEqual(AdminAuditLog.objects.filter(resource='legacy-clock-cutover', object_id=str(self.order.pk)).count(), 1)

    def test_existing_payment_deposit_snapshot_is_preserved(self):
        Order.objects.filter(pk=self.order.pk).update(service_deposit_percent=35)
        SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).delete()
        preview = self.preview()
        self.assertEqual((preview['state'], preview['facts']['deposit_percent'], preview['facts']['deposit_source']),
                         ('READY', 35, 'PAYMENT_SNAPSHOT'))
        response = self.submit(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.service_deposit_percent, 35)

    def test_completed_unsettled_order_preserves_old_aftersale_deadline(self):
        from engagement.confirmation import aftersale_deadline
        completed = timezone.now() - timedelta(hours=40)
        Order.objects.filter(pk=self.order.pk).update(status='COMPLETED', completed_at=completed)
        before_transactions = list(Transaction.objects.filter(order=self.order).values())
        before_wallets = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        preview = self.preview()
        self.assertEqual(preview['state'], 'READY', preview['facts']['problems'])
        self.assertEqual(preview['facts']['aftersale_source'], 'LEGACY_DEADLINE')
        self.assertEqual(preview['facts']['aftersale_hours'], 72)
        self.assertEqual(preview['facts']['clock_config']['aftersale_hours'], 48)
        Order.objects.filter(pk=self.order.pk).update(completed_at=completed+timedelta(seconds=1))
        self.assertEqual(self.submit(preview).status_code, 409)
        self.assertFalse(LegacyClockCutover.objects.exists())
        Order.objects.filter(pk=self.order.pk).update(completed_at=completed)
        response = self.submit(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(aftersale_deadline(self.order), completed + timedelta(hours=72))
        self.assertEqual((self.order.service_aftersale_hours, self.order.service_freeze_hours), (72, 48))
        self.assertEqual(response.data['data']['rules']['aftersale_source'], 'LEGACY_DEADLINE')
        self.assertEqual(list(Transaction.objects.filter(order=self.order).values()), before_transactions)
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_wallets)
        self.assertFalse(self.order.settled_at)
        self.assertEqual(self.submit(preview).status_code, 200)

    def test_completed_order_with_unreliable_completion_time_needs_review(self):
        for completed in (None, timezone.now()+timedelta(hours=1), self.order.created_at-timedelta(seconds=1)):
            Order.objects.filter(pk=self.order.pk).update(status='COMPLETED', completed_at=completed)
            preview = self.preview()
            self.assertEqual(preview['state'], 'REVIEW')
            self.assertEqual(self.submit(preview).status_code, 409)
        self.assertFalse(LegacyClockCutover.objects.exists())

    def test_changed_config_or_report_invalidates_the_preview(self):
        preview = self.preview()
        SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).update(value='40', updated_at=timezone.now())
        self.assertEqual(self.submit(preview).status_code, 409)
        self.assertFalse(LegacyClockCutover.objects.exists())
        preview = self.preview()
        ProviderReport.objects.create(order=self.order, provider=self.a, provider_account=self.a_account,
            game_name='历史报单', amount=5000, commission_rate=20, status='DRAFT')
        self.assertEqual(self.submit(preview).status_code, 409)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.service_clock_version)

    def test_payment_and_original_income_evidence_must_reconcile(self):
        from stores.db import transaction
        payment = Transaction.objects.get(order=self.order, tx_type='PAY')
        with transaction.atomic():
            Transaction.objects.filter(pk=payment.pk).update(amount=payment.amount+1)
            self.assertIn('历史付款流水不完整或存在冲正，须人工核对', self.preview()['facts']['problems'])
            transaction.set_rollback(True)
        with transaction.atomic():
            Order.objects.filter(pk=self.order.pk).update(provider_income=self.order.provider_income-1)
            self.assertIn('原订单参与份额或分账快照不一致，须人工核对', self.preview()['facts']['problems'])
            transaction.set_rollback(True)

    def test_existing_hold_incomplete_reports_and_missing_config_are_not_overwritten(self):
        SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).delete()
        self.assertEqual(self.preview()['state'], 'REVIEW')
        SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY, value='60')
        report = ProviderReport.objects.create(order=self.order, provider=self.a, provider_account=self.a_account,
            game_name='历史报单', amount=5000, commission_rate=20, status='PENDING', submitted_at=None)
        self.assertEqual(self.preview()['state'], 'REVIEW')
        report.status='APPROVED'; report.submitted_at=timezone.now()-timedelta(hours=2)
        report.save(update_fields=['status', 'submitted_at'])
        self.assertEqual(self.preview()['state'], 'REVIEW')
        report.delete()
        wallet = Wallet.objects.get(account=self.a_account)
        from stores.db import transaction
        with transaction.atomic():
            unlinked = Transaction.objects.create(wallet=wallet, tx_type='INCOME', amount=100,
                balance_before=0, balance_after=100, status='SUCCESS')
            IncomeHold.objects.create(wallet=wallet, income_transaction=unlinked, amount=100,
                                      release_at=timezone.now()+timedelta(hours=9))
            self.assertIn('陪玩存在无法归属订单的历史冻结，须人工核对', self.preview()['facts']['problems'])
            transaction.set_rollback(True)
        income = Transaction.objects.create(wallet=wallet, order=self.order, tx_type='INCOME', amount=100,
            balance_before=0, balance_after=100, status='SUCCESS')
        hold = IncomeHold.objects.create(wallet=wallet, income_transaction=income, amount=100,
                                         release_at=timezone.now()+timedelta(hours=9))
        old_deadline = hold.release_at
        preview = self.preview()
        self.assertEqual(preview['state'], 'REVIEW')
        self.assertEqual(preview['facts']['income_holds'][0]['release_at'], old_deadline.isoformat())
        self.assertEqual(self.submit(preview).status_code, 409)
        hold.refresh_from_db()
        self.assertEqual(hold.release_at, old_deadline)
        self.assertFalse(LegacyClockCutover.objects.exists())

    def test_permissions_audit_failure_and_changed_snapshot_fail_closed(self):
        self.assertEqual(self.client_for(self.staff).get(self.url).status_code, 403)
        preview = self.preview()
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('切换审计失败')):
            with self.assertRaisesMessage(RuntimeError, '切换审计失败'):
                self.submit(preview)
        self.order.refresh_from_db()
        self.assertEqual((self.order.settlement_mode, self.order.service_clock_version), ('COMPLETION', None))
        self.assertFalse(LegacyClockCutover.objects.exists())
        self.assertEqual(self.submit(preview).status_code, 200)
        Order.objects.filter(pk=self.order.pk).update(service_freeze_hours=1)
        self.assertEqual(self.preview()['state'], 'REVIEW')
        self.assertEqual(self.submit(preview).status_code, 409)


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacyClockCutoverMoneyFlowTest(APITestCase):
    from orders.tests.test_release_flow import ReleaseFlowTest as _flow
    setUp = _flow.setUp
    login = _flow.login
    ok = _flow.ok
    recharge = _flow.recharge
    new_order = _flow.new_order

    def test_completed_cutover_then_report_settles_after_both_clocks(self):
        from engagement.confirmation import process_order
        from orders.tests.test_release_flow import screenshot
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:edit', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        SystemConfig.objects.update_or_create(key=CONFIG_KEY,
            defaults={'value':json.dumps({'aftersale_hours':48, 'income_freeze_hours':48, 'version':2})})
        SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value':'60'})
        order = self.new_order()
        report = self._flow.finish(self, order)
        now = timezone.now()
        Order.objects.filter(pk=order.pk).update(room_required_snapshot=None, settlement_mode='COMPLETION',
            service_clock_version=None, service_aftersale_hours=None, service_freeze_hours=None,
            service_deposit_percent=None, created_at=now-timedelta(hours=60),
            in_service_at=now-timedelta(hours=55), completed_at=now-timedelta(hours=40))
        self.login(self.admin)
        path=f'/api/admin/orders/{order.pk}/legacy-clock-cutover/'
        preview=self.ok(self.client.get(path))
        self.assertEqual(preview['state'], 'READY', preview['facts']['problems'])
        self.ok(self.client.post(path, {'plan_fingerprint':preview['plan_fingerprint'],
                                       'client_request_id':'completed-cutover-money'}, format='json'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind':'RESULT', 'image':screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        report.refresh_from_db()
        from orders.models import AfterSale
        case=AfterSale.objects.create(order=order, account=self.accounts[self.boss.pk],
                                      reason='旧期限内已登记的售后', status='OPEN')
        with patch('django.utils.timezone.now', return_value=report.submitted_at+timedelta(hours=33)):
            self.assertFalse(process_order(order.pk))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        with patch('django.utils.timezone.now', return_value=report.submitted_at+timedelta(hours=49)):
            self.assertFalse(process_order(order.pk))
            self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
            case.status='RESOLVED'; case.save(update_fields=['status'])
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)

    def test_cutover_then_legacy_completion_waits_before_releasing_income(self):
        from engagement.confirmation import process_order
        from orders.tests.test_release_flow import screenshot
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:edit', 'deposit_rule:edit']
        role.save(update_fields=['permissions'])
        SystemConfig.objects.update_or_create(key=CONFIG_KEY,
            defaults={'value':json.dumps({'aftersale_hours':48, 'income_freeze_hours':48, 'version':2})})
        SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value':'60'})
        order = self.new_order()
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':screenshot()}, format='multipart'))
        Order.objects.filter(pk=order.pk).update(room_required_snapshot=None, settlement_mode='COMPLETION',
            service_clock_version=None, service_aftersale_hours=None, service_freeze_hours=None,
            service_deposit_percent=None)
        self.login(self.admin)
        path=f'/api/admin/orders/{order.pk}/legacy-clock-cutover/'
        preview=self.ok(self.client.get(path))
        self.assertEqual(preview['state'], 'READY')
        self.ok(self.client.post(path, {'plan_fingerprint':preview['plan_fingerprint'],
                                       'client_request_id':'legacy-clock-money'}, format='json'))
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/legacy-room-completion/', {
            'customer_account_id':self.accounts[self.boss.pk].pk,
            'provider_account_ids':[self.accounts[self.provider.pk].pk],
            'confirmed_at':timezone.now().isoformat(), 'evidence':'已核对老板本人旧站结单',
            'client_request_id':'legacy-clock-room'}, format='json'))
        report=ProviderReport.objects.get(order=order, provider=self.provider)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind':kind, 'image':screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        report.refresh_from_db(); order.refresh_from_db()
        with patch('django.utils.timezone.now', return_value=report.submitted_at+timedelta(hours=47)):
            self.assertFalse(process_order(order.pk))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        with patch('django.utils.timezone.now', return_value=report.submitted_at+timedelta(hours=49)):
            self.assertTrue(process_order(order.pk))
            self.assertFalse(process_order(order.pk))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)
        self.assertEqual(Transaction.objects.filter(order=order, tx_type='INCOME').count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacyClockCutoverStoresTest(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'KOOK派单卡片原价核验/旧时钟切换冻结口径/旧团队报单/旧报单归属/邀请前端真实HTTP属正式契约深水区（18-盘点 KOOK派单/旧旅程切换/试音待联调）；最终联调启用')
    def test_matching_order_ids_use_only_the_authenticated_store_configuration(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderProvider
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        rows = []
        for index, store in enumerate((resolve_store(), xinan_store())):
            with store_context(store):
                boss, player = make_user(), make_provider(deposit_mode='EARNINGS')
                customer, provider = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
                wallet=get_wallet(user=boss, account=customer)
                player.escort_profile.account=provider; player.escort_profile.save(update_fields=['account'])
                order = make_order(boss, service=make_service(), provider=player, status='IN_SERVICE',
                    customer_account=customer, provider_account=provider, payment_status='PAID',
                    settlement_mode='COMPLETION', in_service_at=timezone.now()-timedelta(hours=1))
                Order.objects.filter(pk=order.pk).update(room_required_snapshot=None)
                Order.objects.filter(pk=order.pk).update(provider_income=order.amount)
                OrderProvider.objects.create(order=order, provider=player, provider_account=provider,
                                             settlement_base=order.amount, provider_income=order.amount)
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-order.amount,
                    balance_before=order.amount, balance_after=0, status='SUCCESS')
                SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value':json.dumps(
                    {'aftersale_hours':24 if index else 72, 'income_freeze_hours':48, 'version':1})})
                SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value':'40' if index else '60'})
                user = make_console_user(['order:view', 'deposit_rule:edit'])
                staff = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account=staff; user.admin_membership.save(update_fields=['account'])
                client=APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                rows.append((store, order.pk, client))
        self.assertEqual(rows[0][1], rows[1][1])
        url=f'/api/admin/orders/{rows[1][1]}/legacy-clock-cutover/'
        preview=rows[1][2].get(url).data['data']
        self.assertEqual((preview['facts']['clock_config']['aftersale_hours'], preview['facts']['deposit_percent']), (24, 40))
        response=rows[1][2].post(url, {'plan_fingerprint':preview['plan_fingerprint'],
                                     'client_request_id':'store-cutover'}, format='json', HTTP_X_STORE_SLUG='shenglang')
        if response.status_code != 200:
            response=rows[1][2].post(url, {'plan_fingerprint':preview['plan_fingerprint'],
                                         'client_request_id':'store-cutover'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for index, (store, pk, _) in enumerate(rows):
            with store_context(store):
                order=Order.objects.get(pk=pk)
                self.assertEqual(order.service_aftersale_hours, 24 if index else None)
                self.assertEqual(LegacyClockCutover.objects.filter(order=order).count(), index)

    def test_completed_cutover_keeps_original_deadline_in_only_the_selected_store(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderProvider
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        rows = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss, player = make_user(), make_provider(deposit_mode='CASH')
                customer, provider = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
                wallet=get_wallet(user=boss, account=customer)
                player.escort_profile.account=provider; player.escort_profile.save(update_fields=['account'])
                order=make_order(boss, service=make_service(), provider=player, status='COMPLETED',
                    customer_account=customer, provider_account=provider, payment_status='PAID',
                    settlement_mode='COMPLETION', provider_income=8000, shop_income=2000)
                now=timezone.now()
                Order.objects.filter(pk=order.pk).update(room_required_snapshot=None,
                    service_clock_version=None, service_aftersale_hours=None, service_freeze_hours=None,
                    service_deposit_percent=None, created_at=now-timedelta(hours=60),
                    in_service_at=now-timedelta(hours=55), completed_at=now-timedelta(hours=40))
                OrderProvider.objects.create(order=order, provider=player, provider_account=provider,
                                             settlement_base=order.amount, provider_income=8000)
                Transaction.objects.create(wallet=wallet, order=order, tx_type='PAY', amount=-order.amount,
                    balance_before=order.amount, balance_after=0, status='SUCCESS')
                SystemConfig.objects.update_or_create(key=CONFIG_KEY, defaults={'value':json.dumps(
                    {'aftersale_hours':24, 'income_freeze_hours':48, 'version':1})})
                SystemConfig.objects.update_or_create(key=DEPOSIT_PERCENT_KEY, defaults={'value':'40'})
                user=make_console_user(['order:view', 'deposit_rule:edit'])
                staff=get_or_create_account_for_legacy_user(user)
                user.admin_membership.account=staff; user.admin_membership.save(update_fields=['account'])
                client=APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                rows.append((store, order.pk, client))
        self.assertEqual(rows[0][1], rows[1][1])
        path=f'/api/admin/orders/{rows[1][1]}/legacy-clock-cutover/'
        preview=rows[1][2].get(path).data['data']
        self.assertEqual((preview['state'], preview['facts']['aftersale_hours']), ('READY', 72))
        response=rows[1][2].post(path, {'plan_fingerprint':preview['plan_fingerprint'],
                                      'client_request_id':'completed-store-cutover'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for index, (store, pk, _) in enumerate(rows):
            with store_context(store):
                order=Order.objects.get(pk=pk)
                self.assertEqual(order.service_aftersale_hours, 72 if index else None)
                self.assertEqual(LegacyClockCutover.objects.filter(order=order).count(), index)
