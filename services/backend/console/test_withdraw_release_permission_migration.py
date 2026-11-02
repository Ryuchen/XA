"""Existing administrators retain access after release permission is introduced."""
from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class WithdrawReleasePermissionMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_existing_owner_receives_permission_without_granting_finance(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0015_audition_provider_binding_permission')])
        old = executor.loader.project_state([('console', '0015_audition_provider_binding_permission')]).apps
        Role = old.get_model('console', 'AdminRole')
        super_role, _ = Role.objects.get_or_create(code='super_admin', defaults={'name': '超级管理员'})
        super_role.permissions = ['withdraw:view', 'withdraw:audit']
        super_role.save(update_fields=['permissions'])
        owner = Role.objects.create(code='bootstrap_test_owner', name='初始化管理员',
                                    permissions=['admin:edit', 'role:edit', 'audit:view', 'withdraw:view'])
        finance = Role.objects.create(code='test_finance_no_release', name='审核客服',
                                      permissions=['withdraw:view', 'withdraw:audit'])
        executor = MigrationExecutor(connection)
        executor.migrate([('console', '0016_withdraw_release_permission')])
        Role = executor.loader.project_state([('console', '0016_withdraw_release_permission')]).apps.get_model('console', 'AdminRole')
        self.assertIn('withdraw:release', Role.objects.get(pk=super_role.pk).permissions)
        self.assertIn('withdraw:release', Role.objects.get(pk=owner.pk).permissions)
        self.assertNotIn('withdraw:release', Role.objects.get(pk=finance.pk).permissions)
