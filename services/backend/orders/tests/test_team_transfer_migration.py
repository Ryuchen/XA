from unittest import skipIf

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


from .migration_isolation import IsolatedMigrationDatabaseMixin


@skipIf(bool(settings.MIGRATION_MODULES), 'Requires real migrations')
class TeamTransferMigrationTest(IsolatedMigrationDatabaseMixin, TransactionTestCase):
    def test_inflight_transferred_team_refreshes_only_existing_completion_evidence(self):
        old = [('orders', '0074_direct_refund_inbox_done')]
        new = [('orders', '0075_orderprovider_team_completion_pending')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            User = apps.get_model('users', 'CustomUser')
            Account = apps.get_model('club_accounts', 'ClubAccount')
            Service = apps.get_model('orders', 'ServiceItem')
            Order = apps.get_model('orders', 'Order')
            Member = apps.get_model('orders', 'OrderProvider')
            Report = apps.get_model('wallet', 'ProviderReport')
            Log = apps.get_model('orders', 'OrderStatusLog')
            boss = User.objects.create(username='migration-team-boss', boss_no='MIGTEAM-BOSS')
            service = Service.objects.create(name='migration-team-service', price=10000)
            members = []
            for i, (status, transferred, image) in enumerate((
                ('IN_SERVICE', True, 'old-completion.png'),
                ('IN_SERVICE', True, ''),
                ('IN_SERVICE', False, 'current-completion.png'),
                ('COMPLETED', True, 'finished-completion.png'),
            )):
                user = User.objects.create(username=f'migration-team-provider-{i}', boss_no=f'MIGTEAM-USER-{i}')
                account = Account.objects.create(username=f'migration-team-account-{i}',
                    account_no=f'MIGTEAM{i}', account_type='PROVIDER')
                order = Order.objects.create(customer_id=boss.pk, service_id=service.pk,
                    order_no=f'migration-team-order-{i}', amount=10000, status=status,
                    escort_mode='DOUBLE', required_provider_count=2, requires_boss_selection=True)
                member = Member.objects.create(order_id=order.pk, provider_id=user.pk, provider_account_id=account.pk)
                Report.objects.create(order_id=order.pk, provider_id=user.pk, provider_account_id=account.pk,
                    game_name='migration', amount=5000, status='DRAFT', completion_image=image)
                if transferred:
                    Log.objects.create(order_id=order.pk, action='TRANSFER')
                members.append(member.pk)
            executor = MigrationExecutor(connection)
            executor.migrate(new)
            Member = executor.loader.project_state(new).apps.get_model('orders', 'OrderProvider')
            self.assertEqual([Member.objects.get(pk=pk).team_completion_pending for pk in members],
                             [True, False, False, False])
        finally:
            MigrationExecutor(connection).migrate(new)
