"""Exercise the historical account backfill with isolated order participants."""
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import OrderProvider
from orders.tests.factories import make_completed_order, make_provider, make_user


class IdentityBackfillEvaluationTest(TransactionTestCase):
    def test_existing_migration_backfills_order_and_assignment_from_same_map(self):
        boss = make_user()
        provider = make_provider()
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        order = make_completed_order(boss, provider)
        member = OrderProvider.objects.create(order=order, provider=provider)
        self.assertIsNone(order.provider_account_id)
        self.assertIsNone(member.provider_account_id)

        migration = import_module('orders.migrations.0022_evaluation_customer_account_and_more')
        with connection.schema_editor() as editor:
            migration.backfill_accounts(apps, editor)

        order.refresh_from_db()
        member.refresh_from_db()
        self.assertEqual(order.customer_account_id, boss_account.pk)
        self.assertEqual(order.provider_account_id, provider_account.pk)
        self.assertEqual(member.provider_account_id, provider_account.pk)
        from orders.evaluation_targets import evaluation_targets
        self.assertEqual(evaluation_targets(order)[0]['provider_account_id'], provider_account.pk)
