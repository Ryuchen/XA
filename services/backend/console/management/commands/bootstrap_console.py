from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from console.models import AdminMembership, AdminRole
from console.permissions import ALL_PERMISSION_SET


class Command(BaseCommand):
    help = '首次部署创建可登录运营后台的业务管理员；不会覆盖已有账号或密码'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)

    def handle(self, *args, **options):
        username = options['username'].strip()
        User = get_user_model()
        if not username or len(username) > 150:
            raise CommandError('用户名必须为1到150位')
        if User.objects.filter(username=username).exists() or ClubAccount.objects.filter(username=username).exists():
            raise CommandError('账号已存在；请使用已有管理员管理权限，不会覆盖该账号')
        password = getpass('管理员密码（不回显）: ')
        if password != getpass('再次输入密码: '):
            raise CommandError('两次密码不一致')
        user = User(username=username, role=User.Role.ADMIN)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError('；'.join(exc.messages)) from exc
        with transaction.atomic():
            user.set_password(password)
            user.save()
            account = get_or_create_account_for_legacy_user(user)
            # 使用独立角色，避免静默修改已有业务角色。
            role = AdminRole.objects.create(
                name=f'初始化管理员 {account.account_no}', code=f'bootstrap_{account.pk}',
                permissions=sorted(ALL_PERMISSION_SET),
            )
            membership = AdminMembership.objects.create(user=user, account=account)
            membership.roles.add(role)
        self.stdout.write(self.style.SUCCESS(f'运营管理员 {username} 已创建，可登录管理端。'))
