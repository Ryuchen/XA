from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from club_accounts.models import ClubAccount, LegacyAccountMap
from console.models import AdminMembership


ROLE_MAP = {
    'ADMIN': ClubAccount.AccountType.STAFF,
    'OPERATOR': ClubAccount.AccountType.STAFF,
    'CUSTOMER': ClubAccount.AccountType.BOSS,
    'PROVIDER': ClubAccount.AccountType.PROVIDER,
}


class Command(BaseCommand):
    help = '将旧 CustomUser 账户搬迁为独立 ClubAccount，并保存永久 ID 映射'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            default='default',
            help='旧 CustomUser 所在的数据库别名，默认 default',
        )
        parser.add_argument(
            '--target',
            default='default',
            help='ClubAccount 目标数据库别名，默认 default',
        )

    def handle(self, *args, **options):
        source = options['source']
        target = options['target']
        User = get_user_model()
        legacy_users = list(User.objects.using(source).order_by('id'))
        staff_user_ids = set(
            AdminMembership.objects.using(source).values_list('user_id', flat=True)
        )
        staff_user_ids.update(user.id for user in legacy_users if user.is_superuser)

        self._validate_source(legacy_users)
        created = 0

        with transaction.atomic(using=target):
            for legacy_user in legacy_users:
                account_type = (
                    ClubAccount.AccountType.STAFF
                    if legacy_user.id in staff_user_ids
                    else ROLE_MAP[legacy_user.role]
                )
                existing_mapping = LegacyAccountMap.objects.using(target).filter(
                    legacy_user_id=legacy_user.id,
                ).first()
                if existing_mapping:
                    ClubAccount.objects.using(target).filter(
                        pk=existing_mapping.account_id,
                    ).update(account_type=account_type)
                    continue

                account = ClubAccount(
                    username=legacy_user.username,
                    password=legacy_user.password,
                    last_login=legacy_user.last_login,
                    account_type=account_type,
                    openid=legacy_user.openid or None,
                    phone=legacy_user.phone or None,
                    avatar=legacy_user.avatar,
                    avatar_url=legacy_user.avatar_url,
                    nickname=legacy_user.nickname,
                    real_name=legacy_user.real_name,
                    is_phone_verified=legacy_user.is_phone_verified,
                    is_openid_bound=legacy_user.is_openid_bound,
                    game_region=legacy_user.game_region,
                    game_nickname=legacy_user.game_nickname,
                    game_uid=legacy_user.game_uid,
                    inviter_commission_rate=legacy_user.inviter_commission_rate,
                    boss_type_id=legacy_user.boss_type_id,
                    account_no=legacy_user.boss_no,
                    can_login=legacy_user.can_login,
                    can_view=legacy_user.can_view,
                    is_active=legacy_user.is_active,
                    last_active_at=legacy_user.last_active_at,
                )
                account.save(using=target)
                ClubAccount.objects.using(target).filter(pk=account.pk).update(
                    created_at=legacy_user.created_at,
                    updated_at=legacy_user.updated_at,
                )
                LegacyAccountMap.objects.using(target).create(
                    legacy_user_id=legacy_user.id,
                    account=account,
                    legacy_role=legacy_user.role,
                )
                created += 1

            for legacy_user in legacy_users:
                if not legacy_user.inviter_id:
                    continue
                account_id = LegacyAccountMap.objects.using(target).get(
                    legacy_user_id=legacy_user.id,
                ).account_id
                inviter_id = LegacyAccountMap.objects.using(target).get(
                    legacy_user_id=legacy_user.inviter_id,
                ).account_id
                ClubAccount.objects.using(target).filter(pk=account_id).update(
                    inviter_id=inviter_id,
                )

        total = LegacyAccountMap.objects.using(target).count()
        self.stdout.write(self.style.SUCCESS(
            f'业务账户迁移完成：本次新增 {created}，映射总数 {total}'
        ))

    @staticmethod
    def _validate_source(users):
        unknown_roles = sorted({user.role for user in users if user.role not in ROLE_MAP})
        if unknown_roles:
            raise CommandError(f'存在未知旧角色：{unknown_roles}')

        checks = {
            'username': [user.username for user in users],
            'openid': [user.openid for user in users if user.openid],
            'boss_no': [user.boss_no for user in users if user.boss_no],
        }
        for field, values in checks.items():
            duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
            if duplicates:
                raise CommandError(f'{field} 存在重复值，迁移已中止：{duplicates}')
