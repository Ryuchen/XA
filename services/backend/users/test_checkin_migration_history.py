"""Existing signup history survives the account-only compatibility migration."""
import os
from datetime import date
from unittest import skipUnless
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


@skipUnless(os.environ.get('XA_CHECKIN_MIGRATION_TEST') == '1', 'Requires a disposable database with real migrations')
class CheckinMigrationHistoryTest(TransactionTestCase):
    def test_legacy_rows_survive_and_account_only_rows_can_coexist(self):
        old = ('users', '0033_escortprofile_dispatch_notification_preferences')
        new = ('users', '0034_checkin_legacy_user_optional')
        executor = MigrationExecutor(connection)
        executor.migrate([old])
        old_apps = executor.loader.project_state([old]).apps
        user = old_apps.get_model('users', 'CustomUser').objects.create(username='checkin-migration-legacy')
        old_apps.get_model('users', 'CheckinMonthProgress').objects.create(user_id=user.pk, year=2026, month=10, makeup_cards=2)
        old_apps.get_model('users', 'CheckinRecord').objects.create(user_id=user.pk, checkin_date=date(2026, 10, 1), seq_in_month=1, reward_amount=10)
        executor = MigrationExecutor(connection)
        executor.migrate([new])
        apps = executor.loader.project_state([new]).apps
        Progress, Record = apps.get_model('users', 'CheckinMonthProgress'), apps.get_model('users', 'CheckinRecord')
        self.assertEqual(Progress.objects.get(user_id=user.pk).makeup_cards, 2)
        self.assertEqual(Record.objects.get(user_id=user.pk).reward_amount, 10)
        Account = apps.get_model('club_accounts', 'ClubAccount')
        first = Account.objects.create(username='checkin-migration-first', account_no='CHECKIN-MIGRATION-1', account_type='BOSS')
        second = Account.objects.create(username='checkin-migration-second', account_no='CHECKIN-MIGRATION-2', account_type='BOSS')
        for account in (first, second):
            Progress.objects.create(account_id=account.pk, year=2026, month=10)
            Record.objects.create(account_id=account.pk, checkin_date=date(2026, 10, 1), seq_in_month=1)
        self.assertEqual(Progress.objects.filter(user__isnull=True).count(), 2)
        self.assertEqual(Record.objects.filter(user__isnull=True).count(), 2)
