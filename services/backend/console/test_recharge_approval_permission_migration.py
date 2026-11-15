"""Only store owner roles inherit authority over recharge bonus approvals."""
from unittest import skipIf
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class RechargeApprovalPermissionMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_existing_owner_roles_get_approval_and_staff_do_not(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0022_chat_supervisor_permission')])
        Role = executor.loader.project_state([('console', '0022_chat_supervisor_permission')]).apps.get_model('console', 'AdminRole')
        owner, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': '管理员'})
        owner.permissions = ['recharge:view']; owner.save(update_fields=['permissions'])
        bootstrap = Role.objects.create(code='bootstrap_gift_owner', name='初始化赠送审批管理员',
            permissions=['role:edit', 'admin:edit', 'audit:view'])
        staff = Role.objects.create(code='gift_approval_support', name='赠送审批普通客服',
            permissions=['wallet:recharge', 'recharge:view'])
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0023_recharge_gift_approval_permission')])
        Role = executor.loader.project_state([('console', '0023_recharge_gift_approval_permission')]).apps.get_model('console', 'AdminRole')
        self.assertIn('recharge:gift_approve', Role.objects.get(pk=owner.pk).permissions)
        self.assertIn('recharge:gift_approve', Role.objects.get(pk=bootstrap.pk).permissions)
        self.assertNotIn('recharge:gift_approve', Role.objects.get(pk=staff.pk).permissions)
