from django.core.management.base import BaseCommand, CommandError

from stores.context import store_context
from stores.models import StorePlatformGrant
from stores.permissions import is_platform_super_admin


class Command(BaseCommand):
    help = '显式授予/撤销原默认库后台人员的平台门店管理权限；不会隐式授权门店超管'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--revoke', action='store_true')

    def handle(self, *args, **options):
        from club_accounts.models import ClubAccount
        from console.permissions import is_console_account
        with store_context(None):
            account = ClubAccount.objects.filter(username=options['username'], account_type='STAFF', is_active=True).first()
            if account is None or not account.can_login or not is_console_account(account):
                raise CommandError('须指定默认库内可登录且拥有后台角色的业务管理员')
            if not options['revoke'] and not is_platform_super_admin(account):
                raise CommandError('平台门店管理仅可显式授权默认库内启用的超级管理员')
            StorePlatformGrant.objects.update_or_create(account_id=account.pk,
                                                       defaults={'is_active': not options['revoke']})
        verb = '已撤销' if options['revoke'] else '已授予'
        self.stdout.write(self.style.SUCCESS(f'{verb} {account.username} 的平台门店管理权限。'))
