"""封禁/解封业务逻辑。

封禁不再是无语义地 PATCH ClubAccount 的三个布尔开关，而是通过 AccountBan
记录原因、期限与操作人，并统一翻转开关；到期或手动解封时回滚。
"""
from django.db import transaction
from django.utils import timezone

from club_accounts.models import ClubAccount
from .models import AccountBan


def active_ban_for(account):
    """返回该账户当前生效的封禁记录（status=ACTIVE），无则 None。"""
    return account.bans.filter(status=AccountBan.Status.ACTIVE).first()


def apply_ban(account, reason, operator=None, operator_account=None,
              expires_at=None, scope=AccountBan.Scope.FULL):
    """对账户执行封禁。

    - 翻转 ClubAccount 的 is_active/can_login/can_view 为 False（登录与数据可见性均锁死）；
    - 若已存在 ACTIVE 封禁，先将其标记为 LIFTED（被新封禁取代），保持单账户至多一个生效封禁；
    - 创建 AccountBan 记录，承载原因/期限/操作人等审计信息。
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('封禁原因不能为空')

    with transaction.atomic():
        # 先收口已有生效封禁，避免多重封禁叠加导致语义不清。
        existing = active_ban_for(account)
        if existing is not None:
            existing.status = AccountBan.Status.LIFTED
            existing.lift_reason = '被新的封禁取代'
            existing.lifted_at = timezone.now()
            existing.lifted_by = operator
            existing.lifted_by_account = operator_account
            existing.save(update_fields=[
                'status', 'lift_reason', 'lifted_at',
                'lifted_by', 'lifted_by_account',
            ])

        ban = AccountBan.objects.create(
            account=account,
            reason=reason,
            scope=scope,
            operator=operator,
            operator_account=operator_account,
            status=AccountBan.Status.ACTIVE,
            expires_at=expires_at,
        )

        locked_account = ClubAccount.objects.select_for_update().get(pk=account.pk)
        locked_account.is_active = False
        locked_account.can_login = False
        locked_account.can_view = False
        locked_account.save(update_fields=['is_active', 'can_login', 'can_view'])

    return ban


def lift_ban(ban, lifted_by=None, lifted_by_account=None, reason=''):
    """解除一条封禁记录并恢复账户开关。

    仅对 ACTIVE 的封禁生效；已解封/过期的记录直接返回，幂等。
    """
    if ban.status != AccountBan.Status.ACTIVE:
        return ban

    with transaction.atomic():
        ban.status = AccountBan.Status.LIFTED
        ban.lift_reason = (reason or '').strip()
        ban.lifted_at = timezone.now()
        ban.lifted_by = lifted_by
        ban.lifted_by_account = lifted_by_account
        ban.save(update_fields=[
            'status', 'lift_reason', 'lifted_at',
            'lifted_by', 'lifted_by_account',
        ])

        account = ClubAccount.objects.select_for_update().get(pk=ban.account_id)
        account.is_active = True
        account.can_login = True
        account.can_view = True
        account.save(update_fields=['is_active', 'can_login', 'can_view'])

    return ban


def lift_expired_bans():
    """扫描到期且仍 ACTIVE 的封禁并自动解封。

    供 Celery beat 定时任务与 management command 调用。返回自动解封的数量。
    """
    now = timezone.now()
    expired = list(
        AccountBan.objects.filter(
            status=AccountBan.Status.ACTIVE,
            expires_at__isnull=False,
            expires_at__lte=now,
        ).select_related('account')
    )
    count = 0
    for ban in expired:
        lift_ban(ban, reason='到期自动解封')
        count += 1
    return count
