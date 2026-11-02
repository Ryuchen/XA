from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from unittest import skipIf


from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class OrderTimeoutMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_existing_order_deadline_is_untouched_and_snapshot_is_null(self):
        old = [('orders', '0056_order_dispatch_route_and_more')]
        new = [('orders', '0057_ordertimeoutconfig_ordertimeoutconfighistory_and_more')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            user = apps.get_model('users', 'CustomUser').objects.create(username='migration-timeout-user')
            service = apps.get_model('orders', 'ServiceItem').objects.create(name='migration-timeout', price=10000)
            deadline = timezone.now() + timedelta(minutes=19)
            order = apps.get_model('orders', 'Order').objects.create(customer_id=user.pk, service_id=service.pk,
                amount=10000, auto_cancel_at=deadline, order_no='migration-timeout-order')
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            apps = executor.loader.project_state(new).apps
            upgraded = apps.get_model('orders', 'Order').objects.get(pk=order.pk)
            self.assertEqual(upgraded.auto_cancel_at, deadline)
            self.assertIsNone(upgraded.pending_timeout_minutes)
            self.assertEqual(apps.get_model('orders', 'OrderTimeoutConfig').objects.get(pk=1).minutes, 30)
        finally:
            MigrationExecutor(connection).migrate(new)

    def test_only_owner_roles_receive_new_timeout_permission(self):
        old = [('console', '0011_staff_recharge_permissions')]
        new = [('console', '0012_order_timeout_owner_permission')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            roles = apps.get_model('console', 'AdminRole').objects
            owner, _ = roles.get_or_create(code='super_admin', defaults={'name': 'owner'})
            owner.permissions = ['order:view']
            owner.save(update_fields=['permissions'])
            roles.create(code='bootstrap_9001', name='full owner',
                         permissions=['order:view', 'role:edit', 'admin:edit', 'audit:view'])
            roles.create(code='bootstrap_9002', name='restricted owner', permissions=['order:view', 'role:edit'])
            roles.create(code='ordinary_timeout_test', name='staff', permissions=['order:view'])
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            roles = executor.loader.project_state(new).apps.get_model('console', 'AdminRole').objects
            self.assertIn('order:timeout', roles.get(code='super_admin').permissions)
            self.assertIn('order:timeout', roles.get(code='bootstrap_9001').permissions)
            self.assertNotIn('order:timeout', roles.get(code='bootstrap_9002').permissions)
            self.assertNotIn('order:timeout', roles.get(code='ordinary_timeout_test').permissions)
        finally:
            MigrationExecutor(connection).migrate(new)
