"""钱包寻址统一入口（全站唯一允许查询/创建钱包的地方）。

历史包袱：``Wallet`` 同时挂了两个 OneToOne —— ``user``（旧 CustomUser）与
``account``（ClubAccount）。业务代码里两套维度混用：

  * 下单、钱包首页走 ``get_or_create(account=...)``
  * 结算、退款、奖惩、充值、报单入账走 ``get_or_create(user_id=...)``

于是出现死锁式故障：某用户先被 user 维度建了钱包（``account`` 为 NULL），
之后任何 account 维度的访问都查不到它，``get_or_create`` 走 create 分支，
撞上 ``user`` 的唯一约束 → IntegrityError 500 —— 该用户从此再也无法
查看钱包、下单、提现，且无法自愈。

本模块把两个维度收口成一条路径：

  1. 入参无论给的是 account 还是 user，先通过 ``LegacyAccountMap`` 互推，补全成一对
  2. 先按 account 查，未命中再按 user 查（两条腿，命中即止）
  3. 命中后自动回填缺失的那一维度（存量脏数据自愈）
  4. 两个维度命中了不同的钱包行 → 显式抛错，绝不静默合并资金
  5. 都没命中才创建，且一次性写全两个维度

约定：业务代码不得再直接调用 ``Wallet.objects.get_or_create``。
"""

from django.db import IntegrityError, transaction

from .models import Wallet


class WalletAddressingError(Exception):
    """钱包寻址异常：脏数据或入参不足，必须由调用方显式处理。"""


def resolve_identity(*, account=None, account_id=None, user=None, user_id=None):
    """把任意一种入参补全成 ``(account_id, user_id)``，能推导的通过映射表推导。

    平台系统账户等没有 ClubAccount 的用户，返回的 account_id 为 None，属正常情况。
    """
    from club_accounts.models import LegacyAccountMap

    if account is not None:
        account_id = account.pk
    if user is not None:
        user_id = user.pk

    # 请求里常是字符串（如 request.data['user_id']），与钱包里的整型主键比较会
    # 因 3 != "3" 误判冲突；统一归一为 int 再做后续比对与查询。
    if account_id is not None:
        account_id = int(account_id)
    if user_id is not None:
        user_id = int(user_id)

    if account_id and not user_id:
        mapping = LegacyAccountMap.objects.filter(account_id=account_id).only(
            'legacy_user_id'
        ).first()
        if mapping is not None:
            user_id = mapping.legacy_user_id
    elif user_id and not account_id:
        mapping = LegacyAccountMap.objects.filter(legacy_user_id=user_id).only(
            'account_id'
        ).first()
        if mapping is not None:
            account_id = mapping.account_id

    return account_id, user_id


def get_wallet(
    *,
    account=None,
    account_id=None,
    user=None,
    user_id=None,
    for_update=False,
    create=True,
):
    """返回目标主体的钱包，必要时创建。

    Args:
        account / account_id: ClubAccount 维度入参（二选一）
        user / user_id: 旧 CustomUser 维度入参（二选一）
        for_update: 加行锁，必须在 ``transaction.atomic`` 内调用
        create: 未命中时是否创建；False 时返回 None

    Raises:
        WalletAddressingError: 入参不足、两维度指向不同钱包、或缺少 user 无法建号
    """
    account_id, user_id = resolve_identity(
        account=account, account_id=account_id, user=user, user_id=user_id
    )
    if not account_id and not user_id:
        raise WalletAddressingError('钱包寻址至少需要 account 或 user 之一')

    def _qs():
        qs = Wallet.objects.all()
        return qs.select_for_update() if for_update else qs

    by_account = _qs().filter(account_id=account_id).first() if account_id else None
    by_user = _qs().filter(user_id=user_id).first() if user_id else None

    # 脏数据护栏：同一主体在两个维度上挂了不同的钱包行，资金分散在两处。
    # 静默选一个会造成余额凭空消失/重复，必须暴露给人工处理。
    if by_account is not None and by_user is not None and by_account.pk != by_user.pk:
        raise WalletAddressingError(
            f'钱包数据冲突：account={account_id} 指向钱包#{by_account.pk}，'
            f'user={user_id} 指向钱包#{by_user.pk}，请先人工合并'
        )

    wallet = by_account or by_user
    if wallet is not None:
        return _backfill(wallet, account_id=account_id, user_id=user_id)

    if not create:
        return None

    # user 是 non-null 外键，没有 legacy user 就建不出钱包
    if not user_id:
        raise WalletAddressingError(
            f'account={account_id} 缺少旧账户映射，无法创建钱包'
        )

    try:
        with transaction.atomic():
            wallet = Wallet.objects.create(user_id=user_id, account_id=account_id)
    except IntegrityError:
        # 并发下被其它事务抢先创建，重查一次即可
        wallet = _qs().filter(user_id=user_id).first()
        if wallet is None and account_id:
            wallet = _qs().filter(account_id=account_id).first()
        if wallet is None:
            raise
        wallet = _backfill(wallet, account_id=account_id, user_id=user_id)

    return wallet


def _backfill(wallet, *, account_id, user_id):
    """回填钱包缺失的维度；已有值且不一致时抛错，不覆盖。"""
    if account_id and wallet.account_id != account_id:
        if wallet.account_id is not None:
            raise WalletAddressingError(
                f'钱包#{wallet.pk} 已绑定 account={wallet.account_id}，'
                f'与目标 account={account_id} 冲突'
            )
        wallet.account_id = account_id
        wallet.save(update_fields=['account', 'updated_at'])
    if user_id and wallet.user_id != user_id:
        raise WalletAddressingError(
            f'钱包#{wallet.pk} 已绑定 user={wallet.user_id}，'
            f'与目标 user={user_id} 冲突'
        )
    return wallet
