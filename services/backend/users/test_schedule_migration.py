"""Existing fixed-slot records survive the overnight constraint migration."""
from unittest import skipIf

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from orders.tests.migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'requires real migrations')
class ScheduleMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_old_slots_are_preserved_and_new_cross_midnight_slots_are_valid(self):
        old = [('users', '0045_passrefundrepaymentaction')]
        new = [('users', '0046_escortschedule_overnight')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        user = apps.get_model('users', 'CustomUser').objects.create(username='schedule-migration-user')
        row = apps.get_model('users', 'EscortSchedule').objects.create(
            provider_id=user.pk, weekday=1, start_minute=360, end_minute=720)
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        schedule = executor.loader.project_state(new).apps.get_model('users', 'EscortSchedule')
        old_row = schedule.objects.get(pk=row.pk)
        self.assertEqual((old_row.provider_id, old_row.weekday, old_row.start_minute, old_row.end_minute),
                         (user.pk, 1, 360, 720))
        schedule.objects.create(provider_id=user.pk, weekday=1, start_minute=1110, end_minute=120)
        with self.assertRaises(IntegrityError), transaction.atomic():
            schedule.objects.create(provider_id=user.pk, weekday=1, start_minute=120, end_minute=120)
