"""A real three-role order lifecycle feeds payroll and operating cost accounting.

This suite uses role JWT fixtures; live browser acceptance separately retains OTP.
No business payment, report, aftersale or payroll calculation is mocked.
"""
import calendar
import time
from datetime import timedelta
from importlib import import_module
from unittest import skipUnless

from django.apps import apps
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminRole, BusinessDictionaryItem, SalaryPlan
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport, RechargeRecord, Transaction, Wallet


def seed_payroll_fixture():
    """Restore canonical seed data inside this test's selected-store transaction.

    --keepdb preserves migration records, while TransactionTestCase flushes the
    rows created by RunPython. A repeatable workflow fixture therefore loads the
    real migration seeds explicitly when those fixture tables are empty.
    """
    from stores.db import connection as business_connection
    editor = business_connection.schema_editor(atomic=False)
    if not BusinessDictionaryItem.objects.filter(type='SALARY_POSITION').exists():
        import_module('console.migrations.0018_business_dictionaries').seed(apps, editor)
    if not SalaryPlan.objects.exists():
        import_module('console.migrations.0019_payroll').seed_salary_plans(apps, editor)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, REQUIRE_RECHARGE_PROOF=True,
                   GRAB_POOL_ENTRY_SNAPSHOT_ENABLED=True, GRAB_SINGLE_WINDOW_ENABLED=True,
                   GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                   REFUND_DEPOSIT_DEFICIT_ENFORCE_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
                   SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True,
                   SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
                   SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_ORDINARY_FUNDING_TESTING=True,
                   ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class OperatingWorkflowTest(APITransactionTestCase):
    def setUp(self):
        seed_payroll_fixture()
        from engagement.models import ClubOperationConfig
        ClubOperationConfig.objects.get_or_create(pk=1)
        self.manager = make_console_user([
            'salary:view', 'salary:edit', 'salary:confirm',
            'cost:view', 'cost:edit', 'roi:view',
        ])
        self.support = make_console_user(['order:view', 'order:dispatch'])
        self.aftersales = make_console_user(['wallet:recharge', 'order:view', 'order:settle', 'report:view', 'report:audit'])
        self.examiner = make_console_user([])
        self.boss = make_user()
        self.provider = make_provider()
        self.accounts = {}
        for user in (self.manager, self.support, self.aftersales, self.examiner, self.boss, self.provider):
            account = get_or_create_account_for_legacy_user(user)
            self.accounts[user.pk] = account
            if hasattr(user, 'admin_membership'):
                user.admin_membership.account = account
                user.admin_membership.save(update_fields=['account'])
        self.provider.escort_profile.account = self.accounts[self.provider.pk]
        self.provider.escort_profile.save(update_fields=['account'])
        self.service = make_service(price=10000, commission_rate=20)
        self.service.game_category = None
        self.service.save(update_fields=['game_category'])
        self.month = timezone.localdate().strftime('%Y-%m')

    def login(self, user):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[user.pk])[0])

    def ok(self, response):
        self.assertIn(response.status_code, (200, 201), ascii(response.data))
        self.assertEqual(response.data['code'], 0, response.data)
        return response.data.get('data')

    def post(self, path, data):
        return self.ok(self.client.post('/api/' + path, data, format='json'))

    def test_owner_permissions_preserve_salary_privacy_for_existing_roles(self):
        owner = AdminRole.objects.create(code='bootstrap_payroll_owner', name='核算初始化负责人',
            permissions=['admin:edit', 'role:edit', 'audit:view'])
        restricted = AdminRole.objects.create(code='bootstrap_payroll_limited', name='受限初始化成员',
            permissions=['audit:view'])
        support = AdminRole.objects.create(code='receipt_support', name='收款客服', permissions=['wallet:view'])
        support_before = list(support.permissions)
        migration = import_module('console.migrations.0020_operations_owner_permissions')
        # This RunPython only updates role rows and reads the editor's alias;
        # no schema operation is performed by this data-only migration.
        migration.grant_owner_permissions(apps, connection.schema_editor(atomic=False))
        owner.refresh_from_db()
        restricted.refresh_from_db()
        support.refresh_from_db()
        self.assertIn('salary:confirm', owner.permissions)
        self.assertIn('dictionary:edit', owner.permissions)
        self.assertNotIn('salary:view', restricted.permissions)
        self.assertEqual(support.permissions, support_before)

    @skipUnless(connection.vendor == 'mysql',
                '全旅程含单人抢单（须陪玩生命周期 MySQL 行锁）＋正式结算＋工资屏障，SQLite 无法承载（18-盘点工资屏障/订单售后资金组合待联调）')
    def test_receipt_dispatch_fulfilment_aftersale_payroll_and_roi(self):
        from console.grab_config import save_grab_window
        from console.capacity_config import save_capacity
        from orders.grab_single_flow import resolve
        from wallet.models import SystemConfig

        save_grab_window(1, 0, lambda *args: None)
        save_capacity(3, 0, lambda *args: None)
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_' + tier, defaults={'value': '0'})
        account = self.accounts[self.provider.pk]
        account.is_openid_bound, account.openid = True, 'operating-flow-wechat'
        account.save(update_fields=['is_openid_bound', 'openid'])
        profile = self.provider.escort_profile
        profile.wechat_avatar_verified_at = timezone.now()
        profile.pass_tier, profile.pass_expires_at = 'SILVER', timezone.now() + timedelta(hours=1)
        profile.deposit_mode = 'EARNINGS'
        profile.save(update_fields=['wechat_avatar_verified_at', 'pass_tier', 'pass_expires_at', 'deposit_mode'])
        profile.service_items.add(self.service)
        self.login(self.aftersales)
        self.ok(self.client.post('/api/admin/wallets/recharge/', {
            'account_id': self.accounts[self.boss.pk].pk, 'amount': 20000,
            'gift_amount': 1000, 'trade_no': 'operating-flow-receipt',
            'client_request_id': 'operating-flow-receipt', 'proof_image': screenshot(),
        }, format='multipart'))
        receipt = RechargeRecord.objects.get(trade_no='operating-flow-receipt')
        self.assertEqual(receipt.operator_account_id, self.accounts[self.aftersales.pk].pk)

        self.login(self.support)
        created = self.post('admin/orders/dispatch/', {
            'customer_id': self.accounts[self.boss.pk].pk, 'service_id': self.service.pk,
            'expected_amount': 10000, 'boss_confirmation': '老板确认使用本金支付服务',
            'boss_confirmation_method': '微信文字确认',
            'client_request_id': 'operating-flow-order',
        })
        order = Order.objects.get(pk=created['id'])
        self.assertEqual(order.status, 'PENDING')
        self.assertEqual(order.status_logs.get(action='CREATE').operator_account_id, self.accounts[self.support.pk].pk)
        self.assertEqual(self.client.get('/api/admin/payroll/preview/', {'month': self.month}).status_code, 403)

        time.sleep(0.01)
        self.login(self.provider)
        grab = self.post(f'orders/orders/{order.pk}/grab/', {'team_size': 1, 'client_request_id': 'operating-flow-grab'})
        self.assertEqual(grab['status'], 'PENDING')
        time.sleep(1.05)
        self.assertEqual(resolve(order.pk), 'ASSIGNED')
        order.refresh_from_db()
        self.assertEqual((order.status, order.provider_account_id), ('GRABBED', account.pk))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['payment_status'], 'PAID')
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order=order)
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        self.post(f'wallet/reports/{report.pk}/submit/', {})
        self.login(self.aftersales)
        self.post(f'admin/reports/{report.pk}/approve/', {})

        self.login(self.boss)
        case = self.post(f'orders/orders/{order.pk}/aftersale/', {'reason': '隔离验收：核对服务结果'})
        self.login(self.aftersales)
        self.post(f'admin/aftersales/{case["id"]}/resolve/', {'resolution': '隔离验收：已核对并与双方确认'})
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order.pk}/'))['aftersale_status'], 'RESOLVED')

        self.login(self.manager)
        manager_plan = SalaryPlan.objects.create(
            position=BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='STORE_MANAGER'),
            name='测试店长方案', base_fen=250000,
        )
        for person, position in (
            (self.support, 'CUSTOMER_SERVICE_FULLTIME'), (self.aftersales, 'AFTERSALE_MANAGER'),
            (self.manager, None), (self.examiner, 'EXAMINER_SHENGLANG'),
        ):
            plan = manager_plan if position is None else SalaryPlan.objects.get(position__code=position)
            self.post('admin/salary-assignments/', {
                'month': self.month, 'account': self.accounts[person.pk].pk, 'plan': plan.pk,
            })
        moment = timezone.localtime(order.status_logs.get(action='CREATE').created_at)
        config = self.ok(self.client.get('/api/admin/payroll/config/'))
        self.ok(self.client.put('/api/admin/payroll/config/', {
            'night_start': (moment - timedelta(minutes=1)).strftime('%H:%M'),
            'night_end': (moment + timedelta(minutes=2)).strftime('%H:%M'),
            'revision': config['revision'],
        }, format='json'))
        self.post('admin/salary-activities/', {
            'kind': 'DISPATCH', 'account': self.accounts[self.support.pk].pk,
            'source_id': order.pk, 'reason': '隔离验收：核对原派单客服',
        })
        self.post('admin/salary-activities/', {
            'kind': 'RECHARGE', 'account': self.accounts[self.support.pk].pk,
            'source_id': receipt.pk, 'reason': '隔离验收：售后代录，业绩属于客服',
        })
        cycles = self.ok(self.client.get('/api/admin/payroll/sources/', {
            'kind': 'AFTERSALE', 'source_id': case['id'],
        }))['list']
        self.assertEqual(len(cycles), 1)
        self.post('admin/salary-activities/', {
            'kind': 'AFTERSALE', 'account': self.accounts[self.aftersales.pk].pk,
            'source_id': case['id'], 'cycle_key': cycles[0]['cycle_key'], 'reason': '隔离验收：核实实际结案周期',
        })
        self.post('admin/salary-activities/', {
            'kind': 'EXAM', 'account': self.accounts[self.examiner.pk].pk,
            'performed_at': timezone.now().isoformat(), 'reason': '隔离验收：人工核实一次已完成考试',
            'client_request_id': 'operating-flow-exam',
        })
        preview = self.ok(self.client.get('/api/admin/payroll/preview/', {'month': self.month}))
        self.assertTrue(preview['can_confirm'], preview)
        lines = {row['account']: row for row in preview['lines']}
        support = lines[self.accounts[self.support.pk].pk]
        self.assertEqual((support['base_fen'], support['performance_fen']), (120000, 40))
        self.assertEqual((support['dispatch_day_amount_fen'], support['dispatch_night_amount_fen']), (0, 10000))
        self.assertEqual((support['dispatch_commission_fen'], support['recharge_commission_fen']), (150, 200))
        self.assertEqual(lines[self.accounts[self.aftersales.pk].pk]['aftersale_commission_fen'], 5000)
        self.assertEqual(lines[self.accounts[self.manager.pk].pk]['recharge_commission_fen'], 0)
        examiner = lines[self.accounts[self.examiner.pk].pk]
        self.assertEqual((examiner['exam_gross_fen'], examiner['exam_deduction_fen'], examiner['exam_commission_fen']), (5000, 1500, 3500))
        self.assertEqual(preview['totals']['total_fen'], 528890)

        accounts_before = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        transaction_count = Transaction.objects.count()
        confirmed = self.post('admin/payroll/confirm/', {'month': self.month, 'fingerprint': preview['fingerprint']})
        repeated = self.post('admin/payroll/confirm/', {'month': self.month, 'fingerprint': preview['fingerprint']})
        self.assertEqual(confirmed['run_id'], repeated['run_id'])
        self.assertEqual(accounts_before, list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')))
        self.assertEqual(transaction_count, Transaction.objects.count())

        today = timezone.localdate()
        ad = BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='AD_SPEND')
        other = BusinessDictionaryItem.objects.get(type='COST_CATEGORY', code='OTHER')
        channel = BusinessDictionaryItem.objects.get(type='AD_CHANNEL', code='UNKNOWN')
        last_day = calendar.monthrange(today.year, today.month)[1]
        for day in range(1, last_day + 1):
            self.post('admin/operating-costs/', {
                'day': today.replace(day=day).isoformat(), 'category': ad.pk, 'channel': channel.pk,
                'amount_fen': 5000 if day == today.day else 0,
                'note': '隔离验收：明确记录投流或当日未投流', 'client_request_id': f'operating-flow-ad-{day}',
            })
        self.post('admin/operating-costs/', {
            'day': today.isoformat(), 'category': other.pk, 'amount_fen': 2000,
            'note': '隔离验收：已核实运营支出', 'client_request_id': 'operating-flow-other',
        })
        roi = self.ok(self.client.get('/api/admin/operating-roi/', {
            'start_date': today.replace(day=1).isoformat(), 'end_date': today.replace(day=last_day).isoformat(),
        }))
        self.assertEqual((roi['roas']['net_consumption_fen'], roi['roas']['ad_spend_fen'], roi['roas']['value']), (10000, 5000, 2))
        operating = roi['operating']
        self.assertTrue(operating['complete'], roi)
        self.assertEqual((operating['net_consumption_fen'], operating['provider_cost_fen'], operating['salary_cost_fen']), (10000, 8000, 528890))
        self.assertEqual((operating['total_cost_fen'], operating['profit_fen']), (543890, -533890))
        self.assertAlmostEqual(operating['roi'], -533890 / 543890)
