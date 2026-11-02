"""Existing single reviews survive the migration to per-provider reviews."""
from unittest import skipIf

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class TeamEvaluationsMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_historical_review_survives_and_second_provider_can_be_reviewed(self):
        old = [('orders', '0077_team_workflow_log')]
        new = [('orders', '0078_alter_evaluation_order_and_more')]
        executor = MigrationExecutor(connection)
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        User = apps.get_model('users', 'CustomUser')
        Service = apps.get_model('orders', 'ServiceItem')
        Order = apps.get_model('orders', 'Order')
        Evaluation = apps.get_model('orders', 'Evaluation')
        boss = User.objects.create(username='historical-review-boss', boss_no='HIST-BOSS')
        first = User.objects.create(username='historical-review-first', boss_no='HIST-FIRST')
        second = User.objects.create(username='historical-review-second', boss_no='HIST-SECOND')
        service = Service.objects.create(name='历史服务', price=10000)
        order = Order.objects.create(customer_id=boss.pk, provider_id=first.pk, service_id=service.pk,
                                     order_no='historical-review-order', amount=10000, status='COMPLETED')
        original = Evaluation.objects.create(order_id=order.pk, customer_id=boss.pk, provider_id=first.pk,
                                             score=5, content='历史评价', reply_content='历史回复')
        executor = MigrationExecutor(connection)
        executor.migrate(new)
        Evaluation = executor.loader.project_state(new).apps.get_model('orders', 'Evaluation')
        retained = Evaluation.objects.get(pk=original.pk)
        self.assertEqual((retained.order_id, retained.provider_id, retained.content, retained.reply_content),
                         (order.pk, first.pk, '历史评价', '历史回复'))
        Evaluation.objects.create(order_id=order.pk, customer_id=boss.pk, provider_id=second.pk, score=4)
        self.assertEqual(Evaluation.objects.filter(order_id=order.pk).count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Evaluation.objects.create(order_id=order.pk, customer_id=boss.pk, provider_id=first.pk, score=3)
