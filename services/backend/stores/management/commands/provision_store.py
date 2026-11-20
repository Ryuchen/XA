from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from stores.models import Store
from stores.provisioning import create_database, initialize_store
from stores.serializers import configured_database_aliases


class Command(BaseCommand):
    help = '登记并初始化独立门店业务库；不会迁移原门店数据或覆盖管理员'

    def add_arguments(self, parser):
        parser.add_argument('--slug', required=True)
        parser.add_argument('--name', required=True)
        parser.add_argument('--database', required=True)
        parser.add_argument('--boss-url')
        parser.add_argument('--provider-url')
        parser.add_argument('--admin-url')
        parser.add_argument('--create-database', action='store_true')
        parser.add_argument('--admin-username')
        parser.add_argument('--password-env')
        parser.add_argument('--admin-phone')
        parser.add_argument('--admin-nickname')

    def handle(self, *args, **options):
        slug, alias = options['slug'], options['database']
        admin_username = options.get('admin_username')
        if admin_username is not None:
            admin_username = admin_username.strip()
            if not admin_username or len(admin_username) > 150:
                raise CommandError('管理员用户名必须为1到150位')
        phone = options.get('admin_phone') or ''
        if admin_username and (not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20):
            raise CommandError('新管理员需要可接收短信的手机号，请填写 --admin-phone')
        if not admin_username and (phone or options.get('admin_nickname')):
            raise CommandError('填写管理员手机号或昵称时还须指定 --admin-username')
        try:
            with transaction.atomic(using='default'):
                from stores.database_identity import assert_distinct_store_database, lock_store_registration
                lock_store_registration()
                store = Store.objects.filter(slug=slug).first()
                if alias not in configured_database_aliases() and not (store and store.database_alias == alias == 'default'):
                    raise CommandError('请先通过 XA_STORE_DATABASES 配置该门店独立数据库别名')
                if store and store.database_alias != alias:
                    raise CommandError('已登记门店的数据库不可变更')
                if Store.objects.exclude(slug=slug).filter(database_alias=alias).exists():
                    raise CommandError('该数据库已归属其他门店')
                if store is None or store.initialized_at is None:
                    assert_distinct_store_database(alias, exclude_store_id=store.pk if store else None)
                store = store or Store(slug=slug, database_alias=alias)
                store.name = options['name'].strip()
                for field in ('boss_url', 'provider_url', 'admin_url'):
                    if options.get(field) is not None:
                        setattr(store, field, options[field])
                store.full_clean()
                store.save()
                if options['create_database'] and store.initialized_at is None and admin_username:
                    from stores.provision_claim import reserve_claim
                    reserve_claim(store, admin_username=admin_username)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        except (ValidationError, IntegrityError) as exc:
            raise CommandError('门店名称、标识或入口配置无效/已使用') from exc
        if options['create_database']:
            create_database(alias, admin_username=admin_username)
        try:
            initialize_store(store, stdout=self.stdout, admin_username=admin_username,
                             password_env=options.get('password_env'), admin_phone=phone or None,
                             admin_nickname=options.get('admin_nickname'),
                             new_database_created=options['create_database'])
        except Exception:
            # A partially migrated/bootstrapped store must remain unavailable.
            from stores.views import _keep_uninitialized_store_closed
            _keep_uninitialized_store_closed(store)
            raise
        self.stdout.write(self.style.SUCCESS(f'门店 {store.name}（{store.slug}）数据库已初始化，当前状态为 {store.get_status_display()}。'))
