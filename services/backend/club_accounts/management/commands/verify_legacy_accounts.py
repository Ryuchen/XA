from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from club_accounts.management.commands.migrate_legacy_accounts import ROLE_MAP
from club_accounts.models import LegacyAccountMap
from console.models import AdminMembership


class Command(BaseCommand):
    help = '逐项校验旧 CustomUser 与 ClubAccount 的账户级迁移结果'

    def add_arguments(self, parser):
        parser.add_argument('--source', default='default')
        parser.add_argument('--target', default='default')

    def handle(self, *args, **options):
        source = options['source']
        target = options['target']
        User = get_user_model()
        legacy_users = list(User.objects.using(source).order_by('id'))
        staff_user_ids = set(
            AdminMembership.objects.using(source).values_list('user_id', flat=True)
        )
        staff_user_ids.update(user.id for user in legacy_users if user.is_superuser)
        mappings = {
            item.legacy_user_id: item
            for item in LegacyAccountMap.objects.using(target).select_related('account')
        }

        errors = []
        if len(legacy_users) != len(mappings):
            errors.append(
                f'数量不一致：旧账号 {len(legacy_users)}，映射 {len(mappings)}'
            )

        for legacy_user in legacy_users:
            mapping = mappings.get(legacy_user.id)
            if mapping is None:
                errors.append(f'旧账号 {legacy_user.id} 缺少映射')
                continue
            account = mapping.account
            expected_account_type = (
                account.AccountType.STAFF
                if legacy_user.id in staff_user_ids
                else ROLE_MAP.get(legacy_user.role)
            )
            expected = {
                'username': legacy_user.username,
                'password': legacy_user.password,
                'account_type': expected_account_type,
                'openid': legacy_user.openid,
                'phone': legacy_user.phone,
                'nickname': legacy_user.nickname,
                'real_name': legacy_user.real_name,
                'account_no': legacy_user.boss_no,
                'can_login': legacy_user.can_login,
                'can_view': legacy_user.can_view,
                'is_active': legacy_user.is_active,
                'boss_type_id': legacy_user.boss_type_id,
            }
            for field, value in expected.items():
                if getattr(account, field) != value:
                    errors.append(
                        f'旧账号 {legacy_user.id} 字段 {field} 不一致'
                    )

            expected_inviter_id = None
            if legacy_user.inviter_id:
                inviter_mapping = mappings.get(legacy_user.inviter_id)
                expected_inviter_id = inviter_mapping.account_id if inviter_mapping else None
            if account.inviter_id != expected_inviter_id:
                errors.append(f'旧账号 {legacy_user.id} 推荐人映射不一致')

        if errors:
            preview = '\n'.join(errors[:20])
            raise CommandError(f'账户迁移校验失败，共 {len(errors)} 项：\n{preview}')

        self.stdout.write(self.style.SUCCESS(
            f'账户迁移校验通过：{len(legacy_users)} 个账号字段及关系一致'
        ))
