"""The new permission is granted to established store owners, not reviewers."""
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class WithdrawConfigPermissionMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_only_existing_owner_roles_receive_config_permission(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0020_operations_owner_permissions')])
        old = executor.loader.project_state([('console', '0020_operations_owner_permissions')]).apps
        Role = old.get_model('console', 'AdminRole')
        owner, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': '管理员'})
        owner.permissions = ['withdraw:view', 'withdraw:audit']
        owner.save(update_fields=['permissions'])
        bootstrap = Role.objects.create(code='bootstrap_withdraw_owner', name='初始化管理员',
            permissions=['role:edit', 'admin:edit', 'audit:view'])
        staff = Role.objects.create(code='withdraw_config_review_only', name='初审客服',
            permissions=['withdraw:view', 'withdraw:audit'])
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0021_withdraw_config_permission')])
        Role = executor.loader.project_state([('console', '0021_withdraw_config_permission')]).apps.get_model('console', 'AdminRole')
        self.assertIn('withdraw:config', Role.objects.get(pk=owner.pk).permissions)
        self.assertIn('withdraw:config', Role.objects.get(pk=bootstrap.pk).permissions)
        self.assertNotIn('withdraw:config', Role.objects.get(pk=staff.pk).permissions)
