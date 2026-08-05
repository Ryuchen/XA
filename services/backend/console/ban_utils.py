"""封禁/解封业务逻辑。

封禁不再是无语义地 PATCH ClubAccount 的布尔开关，而是通过 AccountBan
记录原因、期限与操作人，并统一翻转开关；到期或手动解封时按快照回滚。

三条不变量（改这个文件前先读）：

1. **锁序固定为「先账户行、后封禁行」**。任何路径都不许反过来，
   两条路径以相反顺序拿锁就是教科书级死锁。
2. **封禁只锁 is_active / can_login**，不动 can_view。can_view 是展示开关，
   没有任何鉴权路径消费它，混进封禁语义会让解封无法区分「运营主动隐藏」。
3. **解封按 pre_ban_state 快照回滚**，不无脑置 True。否则「本来就被停用的
   账户」被封一次再解封，会被凭空恢复成正常账户——这是一条越权提权路径。
"""
from django.db import transaction
from django.utils import timezone

from club_accounts.models import ClubAccount
from .models import AccountBan

# 封禁真正锁死的账户开关。故意不含 can_view，见模块 docstring 第 2 条。
BAN_LOCKED_FIELDS = ('is_active', 'can_login')


def active_ban_for(account, *, for_update=False):
    """返回该账户当前生效的封禁记录（status=ACTIVE），无则 None。

    Args:
        account: ``ClubAccount`` 实例。
        for_update: 是否对封禁行加行锁。仅可在事务内且**已持有账户行锁之后**
            传 True，以保证全局锁序一致。

    Returns:
        AccountBan | None
    """
    queryset = AccountBan.objects.filter(
        account=account, status=AccountBan.Status.ACTIVE,
    )
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.first()


def snapshot_account_state(account):
    """采集账户当前的封禁相关开关，作为解封回滚依据。

    Returns:
        dict[str, bool]: 形如 ``{'is_active': True, 'can_login': True}``。
    """
    return {field: bool(getattr(account, field)) for field in BAN_LOCKED_FIELDS}


def apply_ban(account, reason, operator=None, operator_account=None,
              expires_at=None, scope=AccountBan.Scope.FULL):
    """对账户执行封禁。

    - 先对 ClubAccount 行加锁，再处理封禁记录（锁序不可颠倒）；
    - 若已存在 ACTIVE 封禁，先标记为 LIFTED（被新封禁取代），保持单账户至多一个
      生效封禁；新记录**继承**旧记录的 pre_ban_state，避免把「已被封死的状态」
      当成封禁前的原始状态快照下来，导致解封后永远解不开；
    - 创建 AccountBan 记录，承载原因/期限/操作人/封禁前快照等审计信息。

    Args:
        account: 被封禁的 ``ClubAccount``。
        reason: 封禁原因，必填。
        operator: 操作人（legacy ``CustomUser``），可空。
        operator_account: 操作人业务账户（``ClubAccount``），可空。
        expires_at: 自动解封时间；None 表示永久封禁。
        scope: 封禁范围，目前仅 FULL。

    Returns:
        AccountBan: 新建的生效封禁记录。

    Raises:
        ValueError: 原因为空。
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('封禁原因不能为空')

    with transaction.atomic():
        # 锁序第一步：账户行。后续所有读写都基于这把锁，杜绝「读到旧状态再覆盖」。
        locked_account = ClubAccount.objects.select_for_update().get(pk=account.pk)

        # 锁序第二步：该账户的生效封禁行。
        existing = active_ban_for(locked_account, for_update=True)
        if existing is not None:
            # 账户此刻已处于被封状态，现场快照没有意义，必须继承旧记录的。
            pre_ban_state = dict(existing.pre_ban_state or {})
            existing.status = AccountBan.Status.LIFTED
            existing.lift_reason = '被新的封禁取代'
            existing.lifted_at = timezone.now()
            existing.lifted_by = operator
            existing.lifted_by_account = operator_account
            existing.save(update_fields=[
                'status', 'lift_reason', 'lifted_at',
                'lifted_by', 'lifted_by_account',
            ])
        else:
            pre_ban_state = snapshot_account_state(locked_account)

        ban = AccountBan.objects.create(
            account=locked_account,
            reason=reason,
            scope=scope,
            operator=operator,
            operator_account=operator_account,
            status=AccountBan.Status.ACTIVE,
            expires_at=expires_at,
            pre_ban_state=pre_ban_state,
        )

        for field in BAN_LOCKED_FIELDS:
            setattr(locked_account, field, False)
        locked_account.save(update_fields=list(BAN_LOCKED_FIELDS))

    # 让调用方手上的实例与库内一致，避免其后续用陈旧字段做判断。
    for field in BAN_LOCKED_FIELDS:
        setattr(account, field, False)
    return ban


def lift_ban(ban, lifted_by=None, lifted_by_account=None, reason='',
             status=AccountBan.Status.LIFTED):
    """解除一条封禁记录并按快照恢复账户开关。

    仅对 ACTIVE 的封禁生效；已解封/过期的记录直接返回，幂等。

    Args:
        ban: 待解除的 ``AccountBan``。
        lifted_by: 解封人（legacy ``CustomUser``）。
        lifted_by_account: 解封人业务账户。
        reason: 解封原因。
        status: 终态，手动解封为 LIFTED，到期自动解封传 EXPIRED。

    Returns:
        AccountBan: 传入的实例（字段已同步为库内最新值）。
    """
    if ban.status != AccountBan.Status.ACTIVE:
        return ban

    with transaction.atomic():
        # 锁序第一步：账户行（与 apply_ban 保持一致）。
        account = ClubAccount.objects.select_for_update().get(pk=ban.account_id)
        # 锁序第二步：封禁行。并发下两个解封请求都读到 ACTIVE 时，
        # 只有拿到锁后复查状态才能保证仅一次生效。
        locked_ban = AccountBan.objects.select_for_update().get(pk=ban.pk)
        if locked_ban.status != AccountBan.Status.ACTIVE:
            ban.status = locked_ban.status
            return ban

        locked_ban.status = status
        locked_ban.lift_reason = (reason or '').strip()
        locked_ban.lifted_at = timezone.now()
        locked_ban.lifted_by = lifted_by
        locked_ban.lifted_by_account = lifted_by_account
        locked_ban.save(update_fields=[
            'status', 'lift_reason', 'lifted_at',
            'lifted_by', 'lifted_by_account',
        ])

        # 空快照（历史数据/迁移前记录）回落为 True，保持旧行为不炸。
        restored = locked_ban.pre_ban_state or {}
        for field in BAN_LOCKED_FIELDS:
            setattr(account, field, bool(restored.get(field, True)))
        account.save(update_fields=list(BAN_LOCKED_FIELDS))

        # 同步回调用方实例。
        ban.status = locked_ban.status
        ban.lift_reason = locked_ban.lift_reason
        ban.lifted_at = locked_ban.lifted_at
        ban.lifted_by = lifted_by
        ban.lifted_by_account = lifted_by_account

    return ban


def lift_expired_bans():
    """扫描到期且仍 ACTIVE 的封禁并自动解封。

    供 Celery beat 定时任务与 management command 调用。

    与手动解封的区别：终态落 ``EXPIRED`` 而非 ``LIFTED``，否则审计上无法区分
    「运营主动解封」和「刑满释放」，追责时是两回事。

    Returns:
        int: 本次自动解封的数量。
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
        lift_ban(
            ban,
            reason='到期自动解封',
            status=AccountBan.Status.EXPIRED,
        )
        count += 1
    return count
