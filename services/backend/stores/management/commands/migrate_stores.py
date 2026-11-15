from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from stores.context import store_context
from stores.models import Store


class Command(BaseCommand):
    help = '先升级平台目录，再逐门店执行完整业务迁移；不改变停用状态'

    def add_arguments(self, parser):
        parser.add_argument('--slug')

    def handle(self, *args, **options):
        with store_context(None):
            call_command('migrate', database='default', interactive=False, verbosity=options['verbosity'], stdout=self.stdout)
        stores = Store.objects.all()
        if options.get('slug'):
            stores = stores.filter(slug=options['slug'])
            if not stores.exists():
                raise CommandError('门店不存在')
        for store in stores:
            if store.database_alias == 'default':
                if getattr(settings, 'ISOLATED_CONTROL_PLANE', False):
                    raise CommandError(f'门店 {store.slug} 指向平台控制库，独立模式下不能继续迁移')
                continue
            if store.initialized_at is None and store.status == Store.Status.PROVISIONING:
                self.stdout.write(f'门店 {store.slug} 尚待初始化，请使用 provision_store；本次不开放或升级该门店')
                continue
            from stores.dynamic_database import discover_store_connection
            try:
                available = discover_store_connection(store)
            except ValueError as exc:
                raise CommandError(f'门店 {store.slug} 的数据库配置不可用，升级已停止') from exc
            if not available:
                raise CommandError(f'门店 {store.slug} 的数据库尚未配置，升级已停止')
            with store_context(store):
                call_command('migrate', database=store.database_alias, interactive=False,
                             verbosity=options['verbosity'], stdout=self.stdout)
            self.stdout.write(self.style.SUCCESS(f'门店 {store.slug} 迁移完成，状态保持 {store.status}'))
