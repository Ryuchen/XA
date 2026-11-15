from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.dictionary_models import BusinessDictionaryItem
from console.models import AdminAuditLog
from console.payroll_models import PayrollConfig, SalaryAssignment, SalaryPlan
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context
from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant, Store
from stores.platform_auth import issue_tokens
from stores.tests_hosts import xinan_store


@override_settings(PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class PlatformStorePayrollTests(TransactionTestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        self.first = resolve_store()
        Store.objects.filter(pk=self.first.pk).update(initialized_at=timezone.now())
        self.first.refresh_from_db()
        self.second = xinan_store()
        self.finance = PlatformAccount(username='store-payroll-finance', phone='13800001111', is_phone_verified=True)
        self.finance.set_password('Synthetic-Payroll-Password-2026!')
        self.finance.save()
        self.grant = PlatformRoleGrant.objects.create(account=self.finance, role='FINANCE', scope_slug='xinan')
        token, _ = issue_tokens(self.finance, 'SMS', self.finance.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        for store, amount in ((self.first, 1000), (self.second, 2000)):
            with store_context(store):
                position = BusinessDictionaryItem.objects.create(type='SALARY_POSITION', code='PAY_TEST', name='隔离岗位')
                SalaryPlan.objects.create(position=position, name='隔离薪资方案', base_fen=amount)

    def test_scoped_read_configure_preview_and_audit(self):
        base = '/api/admin/platform/store-payroll'
        self.assertEqual(self.client.get(f'{base}/shenglang/plans').status_code, 404)
        response = self.client.get(f'{base}/xinan/plans')
        self.assertEqual(response.status_code, 200, response.data)
        item = next(row for row in response.data['data']['list'] if row['name'] == '隔离薪资方案')
        self.assertEqual(item['base_fen'], 2000)
        result = self.client.put(f'{base}/xinan/plans/{item["id"]}', {'base_fen': 2500, 'revision': item['revision']}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual((result.data['data']['base_fen'], result.data['data']['revision']), (2500, 2))
        self.assertEqual(self.client.put(f'{base}/xinan/plans/{item["id"]}',
            {'base_fen': 3000, 'revision': 1}, format='json').status_code, 409)
        result = self.client.put(f'{base}/xinan/config', {'night_start': '22:00', 'night_end': '08:00', 'revision': 0}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(self.client.get(f'{base}/xinan/config').data['data']['revision'], 1)
        preview = self.client.get(f'{base}/xinan/preview', {'month': '2026-11'})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertFalse(preview.data['data']['can_confirm'])
        self.assertEqual(self.client.get(f'{base}/xinan/assignments', {'month': '2026-11'}).data['data']['list'], [])
        self.assertEqual(self.client.post(f'{base}/xinan/confirm', {'month': '2026-11'}, format='json').status_code, 503)
        with store_context(self.first):
            self.assertEqual(SalaryPlan.objects.get(name='隔离薪资方案').base_fen, 1000)
            self.assertFalse(PayrollConfig.objects.exists())
        with store_context(self.second):
            self.assertEqual(SalaryPlan.objects.get(name='隔离薪资方案').base_fen, 2500)
            events = list(AdminAuditLog.objects.filter(resource='platform-store-payroll').order_by('pk'))
            self.assertEqual(len(events), 2)
            self.assertTrue(all(event.operator_account_id is None and event.operator_id is None for event in events))
            self.assertTrue(all(event.request_body['platform_account_id'] == str(self.finance.pk) for event in events))
        completed = PlatformAuditEvent.objects.filter(account=self.finance, action='store_payroll.configure_completed', target_store_slug='xinan')
        self.assertEqual(completed.count(), 2)
        self.grant.is_active = False
        self.grant.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(f'{base}/xinan/plans').status_code, 401)

    def test_platform_creates_plan_and_employee_assignment_with_idempotent_actor(self):
        with store_context(self.second):
            user = make_console_user([])
            employee = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = employee
            user.admin_membership.save(update_fields=['account'])
            position = BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='PAY_TEST')
        base = '/api/admin/platform/store-payroll/xinan'
        plan_key, assignment_key = str(uuid4()), str(uuid4())
        payload = {'request_id': plan_key, 'position': position.pk, 'name': '平台创建方案', 'base_fen': 3100}
        original_create = PlatformAuditEvent.objects.create
        def fail_completion(**values):
            if values.get('action') == 'store_payroll.create_completed':
                raise DatabaseError('simulated platform audit failure')
            return original_create(**values)
        with patch.object(PlatformAuditEvent.objects, 'create', side_effect=fail_completion):
            uncertain = self.client.post(f'{base}/plans', payload, format='json')
        self.assertEqual(uncertain.status_code, 503, uncertain.data)
        plan_result = self.client.post(f'{base}/plans', payload, format='json')
        self.assertEqual(plan_result.status_code, 200, plan_result.data)
        plan_id = plan_result.data['data']['id']
        self.assertEqual(self.client.post(f'{base}/plans', {**payload, 'base_fen': 3200}, format='json').status_code, 409)
        self.assertEqual(self.client.post('/api/admin/platform/store-payroll/shenglang/plans', payload, format='json').status_code, 404)
        assignment = {'request_id': assignment_key, 'month': '2026-11', 'account': employee.pk,
                      'plan': plan_id, 'expected_plan_revision': 1}
        response = self.client.post(f'{base}/assignments', assignment, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        repeated = self.client.post(f'{base}/assignments', assignment, format='json')
        self.assertEqual(repeated.data['data']['id'], response.data['data']['id'])
        self.assertEqual(self.client.post(f'{base}/assignments', {**assignment, 'request_id': str(uuid4())}, format='json').status_code, 409)
        self.assertEqual(self.client.post(f'{base}/assignments', {**assignment, 'request_id': str(uuid4()), 'created_by': employee.pk}, format='json').status_code, 400)
        with store_context(self.second):
            plan = SalaryPlan.objects.get(pk=plan_id)
            row = SalaryAssignment.objects.get(pk=response.data['data']['id'])
            self.assertEqual((plan.created_by_id, plan.created_by_platform_id, plan.created_by_system),
                             (None, self.finance.pk, False))
            self.assertEqual((row.created_by_id, row.created_by_platform_id, row.account_id),
                             (None, self.finance.pk, employee.pk))
            self.assertEqual(row.plan_snapshot['base_fen'], 3100)
            self.assertEqual(SalaryPlan.objects.filter(platform_request_id=plan_key).count(), 1)
            self.assertEqual(SalaryAssignment.objects.filter(platform_request_id=assignment_key).count(), 1)
            self.assertEqual(AdminAuditLog.objects.filter(resource='platform-store-payroll-create').count(), 2)
        preview = self.client.get(f'{base}/preview', {'month': '2026-11'})
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data['data']['lines'][0]['account'], employee.pk)
        changed = self.client.put(f'{base}/plans/{plan_id}', {'base_fen': 4200, 'revision': 1}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        replay = self.client.post(f'{base}/plans', payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual((replay.data['data']['base_fen'], replay.data['data']['revision']), (3100, 1))
        with store_context(self.second):
            self.assertEqual(SalaryPlan.objects.get(pk=plan_id).base_fen, 4200)
            self.assertEqual(SalaryAssignment.objects.get(pk=response.data['data']['id']).plan_snapshot['base_fen'], 3100)
        with store_context(self.first):
            self.assertFalse(SalaryPlan.objects.filter(name='平台创建方案').exists())
            self.assertFalse(SalaryAssignment.objects.filter(platform_request_id=assignment_key).exists())

    def test_super_admin_both_stores_and_uncertain_cross_database_result(self):
        actor = PlatformAccount(username='store-payroll-super', phone='13900001111', is_phone_verified=True)
        actor.set_password('Synthetic-Payroll-Password-2026!')
        actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(actor, 'SMS', actor.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        base = '/api/admin/platform/store-payroll'
        for slug in ('shenglang', 'xinan'):
            self.assertEqual(client.get(f'{base}/{slug}/plans').status_code, 200)
        original_create = PlatformAuditEvent.objects.create
        def fail_completion(**values):
            if values.get('action') == 'store_payroll.configure_completed':
                raise DatabaseError('simulated platform audit failure')
            return original_create(**values)
        with patch.object(PlatformAuditEvent.objects, 'create', side_effect=fail_completion):
            response = client.put(f'{base}/xinan/config',
                {'night_start': '22:00', 'night_end': '08:00', 'revision': 0}, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data['errorCode'], 'PAYROLL_RESULT_UNCERTAIN')
        with store_context(self.second):
            self.assertEqual(PayrollConfig.objects.get().revision, 1)
            evidence = AdminAuditLog.objects.get(resource='platform-store-payroll')
            self.assertEqual(evidence.request_body['platform_account_id'], str(actor.pk))
        self.assertTrue(PlatformAuditEvent.objects.filter(account=actor,
            action='store_payroll.configure_requested', target_store_slug='xinan').exists())
        self.assertFalse(PlatformAuditEvent.objects.filter(account=actor,
            action='store_payroll.configure_completed', target_store_slug='xinan').exists())
        self.assertEqual(client.get(f'{base}/xinan/config').data['data']['revision'], 1)

    def test_platform_confirm_closed_without_barrier_and_refund_accounting(self):
        body = {'month': '2026-11', 'fingerprint': 'a' * 64, 'request_id': str(uuid4())}
        response = self.client.post('/api/admin/platform/store-payroll/xinan/confirm', body, format='json')
        self.assertEqual(response.status_code, 503, response.data)
        with store_context(self.second):
            from console.payroll_models import PayrollRun
            self.assertFalse(PayrollRun.objects.filter(month='2026-11-01').exists())

    def test_store_admin_creation_keeps_real_store_actor(self):
        with store_context(self.first):
            user = make_console_user(['salary:view', 'salary:edit'])
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            token, _ = issue_account_tokens(account)
            position = BusinessDictionaryItem.objects.get(type='SALARY_POSITION', code='PAY_TEST')
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        plan = client.post('/api/admin/salary-plans/', {'position': position.pk, 'name': '本店创建方案',
            'base_fen': 3100}, format='json')
        self.assertEqual(plan.status_code, 201, plan.data)
        assignment = client.post('/api/admin/salary-assignments/', {'month': '2026-11', 'account': account.pk,
            'plan': plan.data['data']['id']}, format='json')
        self.assertEqual(assignment.status_code, 201, assignment.data)
        with store_context(self.first):
            row = SalaryPlan.objects.get(pk=plan.data['data']['id'])
            assigned = SalaryAssignment.objects.get(pk=assignment.data['data']['id'])
            self.assertEqual((row.created_by_id, row.created_by_platform_id, row.created_by_system), (account.pk, None, False))
            self.assertEqual((assigned.created_by_id, assigned.created_by_platform_id), (account.pk, None))

    def test_store_identity_is_not_platform_identity(self):
        with store_context(self.first):
            user = make_console_user(['salary:view', 'salary:edit', 'salary:confirm'])
            account = get_or_create_account_for_legacy_user(user)
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
            token, _ = issue_account_tokens(account)
        local = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(local.get('/api/admin/platform/store-payroll/shenglang/plans').status_code, 401)
