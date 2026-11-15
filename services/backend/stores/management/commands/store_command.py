import argparse

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from stores.context import resolve_store, store_context


# These project maintenance commands use routed ORM/transactions. Built-in
# commands often explicitly bind --database=default, bypassing the ORM router;
# data reset/import tools also have their own environment safety boundaries.
# Add a command only after checking its database, file and external side effects.
STORE_COMMANDS = frozenset({
    'lift_expired_bans', 'check_mysql_readiness',
    'verify_business_account_relations', 'check_order_timeout',
    'expire_reservations', 'audit_pending_payment_sources',
    'audit_escort_ratings', 'normalize_provider_qualifications',
    'correct_companion_catalog', 'backfill_deposit_platform_income',
    'audit_voice_files',
})


class Command(BaseCommand):
    help = '在指定门店运行已适配的业务维护命令，不切换/覆盖平台数据库配置'

    def add_arguments(self, parser):
        parser.add_argument('--store', required=True)
        parser.add_argument('command')
        parser.add_argument('command_args', nargs=argparse.REMAINDER)

    def handle(self, *args, **options):
        name = options['command']
        if name not in STORE_COMMANDS:
            raise CommandError('该命令未适配门店维护入口。数据库升级使用 migrate_stores，初始化使用 provision_store；'
                               '允许命令：' + '、'.join(sorted(STORE_COMMANDS)))
        command_args = options['command_args']
        if any(value.split('=', 1)[0] in ('--database', '--source-db', '--target-db', '--store')
               for value in command_args):
            raise CommandError('门店维护命令不允许覆盖数据库或门店；请仅通过外层 --store 指定')
        try:
            store = resolve_store(options['store'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'正在门店 {store.name}（{store.slug}）执行业务维护')
        with store_context(store):
            return call_command(name, *command_args, stdout=self.stdout, stderr=self.stderr)
