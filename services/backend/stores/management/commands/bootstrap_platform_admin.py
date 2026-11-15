"""One-time interactive bootstrap; never accept a password in argv or logs."""
from getpass import getpass
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from stores.models import PlatformAccount, PlatformAuditEvent, PlatformRoleGrant


class Command(BaseCommand):
    help = '首次初始化平台超级管理员；密码只从交互式终端读取。'

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True)
        parser.add_argument('--phone', required=True)
        parser.add_argument('--nickname', default='')

    def handle(self, *args, **options):
        username = options['username'].strip()
        phone = options['phone'].strip()
        nickname = options['nickname'].strip()
        if not username or len(username) > 150 or '/' in username:
            raise CommandError('账号不能为空、不能包含斜线，且最多 150 个字符')
        if not phone.isascii() or not phone.isdigit() or not 5 <= len(phone) <= 20:
            raise CommandError('请输入有效手机号')
        if len(nickname) > 80:
            raise CommandError('昵称最多 80 个字符')
        if not sys.stdin.isatty():
            raise CommandError('必须在交互式终端安全输入密码')
        password = getpass('平台管理员密码：')
        confirmation = getpass('再次输入密码：')
        if not password or password != confirmation:
            raise CommandError('两次密码不一致或密码为空')
        account = PlatformAccount(username=username, phone=phone, nickname=nickname)
        try:
            validate_password(password, user=account)
        except ValidationError as error:
            raise CommandError('密码不符合安全要求：' + '；'.join(error.messages)) from None
        account.set_password(password)
        try:
            with transaction.atomic(using='default'):
                # The platform must be bootstrappable before any store exists.
                from stores.database_identity import lock_store_registration
                lock_store_registration()
                if PlatformRoleGrant.objects.filter(role=PlatformRoleGrant.Role.SUPER_ADMIN,
                        scope_slug='*', is_active=True, account__is_active=True).exists():
                    raise CommandError('平台超级管理员已存在，不允许再次初始化')
                if PlatformAccount.objects.filter(username=username).exists():
                    raise CommandError('平台账号已存在')
                account.save()
                PlatformRoleGrant.objects.create(account=account, role=PlatformRoleGrant.Role.SUPER_ADMIN, scope_slug='*')
                PlatformAuditEvent.objects.create(account=account, action='platform.bootstrap', target_type='platform_account', target_id=str(account.pk))
        except IntegrityError as error:
            raise CommandError('平台账号创建冲突，请核对后重试') from error
        self.stdout.write(self.style.SUCCESS('平台超级管理员已创建；请使用该手机号完成首次短信验证。'))
