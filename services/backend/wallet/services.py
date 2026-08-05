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

import hashlib

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import IdempotencyKey, Wallet

#: 幂等键的最大长度，与 ``IdempotencyKey.request_id`` 字段保持一致。
MAX_REQUEST_ID_LENGTH = 64

#: 旧客户端未带 client_request_id 时，服务端派生键的去重窗口（秒）。
#: 窗口内的同参数重复提交视为误触；超出窗口视为用户真实的第二次操作。
DEFAULT_FALLBACK_WINDOW_SECONDS = 60

#: 服务端派生键的前缀，便于排障时与客户端真实键区分。
DERIVED_KEY_PREFIX = 'auto:'


class WalletAddressingError(Exception):
    """钱包寻址异常：脏数据或入参不足，必须由调用方显式处理。"""


class DuplicateRequestError(Exception):
    """重复请求：幂等键已被占用，业务不得重复执行。"""


def normalize_request_id(raw):
    """把客户端传入的幂等键归一化为可入库的字符串。

    Args:
        raw: 原始入参，通常来自 ``request.data['client_request_id']``。

    Returns:
        去除首尾空白并截断到 ``MAX_REQUEST_ID_LENGTH`` 的字符串；
        入参为空/None 时返回空串，由调用方决定是拒绝还是放行。
    """
    if raw is None:
        return ''
    return str(raw).strip()[:MAX_REQUEST_ID_LENGTH]


def claim_idempotency_key(*, account, request_id, scope=''):
    """抢占一个幂等键；返回 True 表示首次占用，调用方可继续执行业务。

    必须在业务写入所处的同一个 ``transaction.atomic`` 块内调用，
    这样业务失败回滚时幂等键一并释放，用户可以正常重试。

    实现选型：这里用 ``get_or_create`` 而不是
    ``bulk_create(ignore_conflicts=True)``。后者虽然直译成
    ``INSERT ... ON CONFLICT DO NOTHING``，但在 MySQL / SQLite 上**不会**
    回填主键，调用方无从判断自己是不是抢到键的那一方 —— 而这正是幂等
    判定的全部意义。``get_or_create`` 内部同样是「INSERT，撞唯一约束就认输」，
    并且 Django 会为这次 INSERT 开一个 savepoint，失败不会污染外层事务。

    Args:
        account: ``ClubAccount`` 实例；为 None 时无法去重，直接抛错。
        request_id: 已归一化的客户端幂等键，空串视为非法。
        scope: 业务标注（如 ``withdraw``），仅用于排障。

    Returns:
        bool: True = 首次占用；False = 键已存在，属重复请求。

    Raises:
        WalletAddressingError: 缺少 account 或 request_id，无法建立幂等保护。
    """
    if account is None:
        raise WalletAddressingError('缺少业务账户，无法建立幂等保护')
    request_id = normalize_request_id(request_id)
    if not request_id:
        raise WalletAddressingError('缺少 client_request_id，无法建立幂等保护')

    try:
        _, created = IdempotencyKey.objects.get_or_create(
            account=account,
            request_id=request_id,
            defaults={'scope': scope[:32]},
        )
    except IntegrityError:
        # MySQL REPEATABLE READ 下，并发方先插入但未提交时，本事务会先阻塞、
        # 再拿到 duplicate key，而快照里又读不到那一行，导致 get_or_create
        # 内部的兜底 get() 也扑空并重新抛出。这种情况同样是重复请求。
        return False
    return created


def require_idempotency_key(*, account, request_id, scope=''):
    """``claim_idempotency_key`` 的严格版：重复请求直接抛 ``DuplicateRequestError``。

    适合视图里 ``try/except`` 统一转 400 的写法，省掉每处的 if 判断。
    """
    if not claim_idempotency_key(
        account=account, request_id=request_id, scope=scope
    ):
        raise DuplicateRequestError('请求重复提交，请勿重复操作')


def _derive_request_id(*, scope, fingerprint):
    """为未携带幂等键的旧客户端派生一个稳定的键。

    同一账户 + 同一业务 + 同一组关键参数 => 同一个键，因此短时间内的
    重复提交会落到同一行上而被拦下。
    """
    digest = hashlib.sha256(
        f'{scope}|{fingerprint}'.encode('utf-8')
    ).hexdigest()[:40]
    return f'{DERIVED_KEY_PREFIX}{digest}'


def peek_fund_write(*, account, request_id):
    """只读探测：本次请求是否是一次**已成功**请求的重放。不占键、无副作用。

    为什么单独需要这一步
    --------------------
    幂等键必须等「校验全过、即将动钱」时才占用 —— 否则校验失败（余额不足）
    也会把键永久占掉，用户补足余额后拿同一个键重试会被误判成重复提交。

    但只在末尾占键会漏掉反向的一种情形：**第一次其实已经成功了**，重放时被
    某个「正因为第一次成功才会失败」的状态校验提前拦下。例如押金接口：
    首次缴 3000 成功后应缴差额只剩 2000，重放同样的 3000 会先撞上
    「缴纳金额超过应缴差额」返回 400 —— 客户端据此以为是自己参数错了，
    真实原因却是上一笔已经到账。这种错误提示会直接引来「钱扣了却说我填错」的客诉。

    因此在状态相关校验**之前**先做一次只读探测：键已存在就明确回 409。

    仅对客户端显式携带的 ``client_request_id`` 生效 —— 派生键依赖校验后才
    确定的参数指纹，此刻算不出来，那条降级路径仍由 ``claim_fund_write`` 兜底。

    Args:
        account: ``ClubAccount`` 实例。
        request_id: 客户端原始幂等键，可为空。

    Returns:
        tuple[bool, str]: ``(是否为重放, 提示原因)``；非重放时原因为空串。
    """
    if account is None:
        return False, ''
    normalized = normalize_request_id(request_id)
    # 空键走降级通道；派生键前缀由服务端保留，客户端传来即为伪造，
    # 两种都留给 claim_fund_write 判定，这里一律不拦。
    if not normalized or normalized.startswith(DERIVED_KEY_PREFIX):
        return False, ''
    if IdempotencyKey.objects.filter(
        account=account, request_id=normalized,
    ).exists():
        return True, '请求重复提交，请勿重复操作'
    return False, ''


def claim_fund_write(
    *,
    account,
    request_id,
    scope,
    fingerprint='',
    window_seconds=DEFAULT_FALLBACK_WINDOW_SECONDS,
):
    """资金写接口的统一幂等闸门。**必须在业务事务内调用。**

    两种模式：

    * **客户端携带 ``client_request_id``（推荐）**：走 ``IdempotencyKey``
      的唯一约束做永久去重，键一旦用过就永不放行，语义最强。
    * **旧客户端未携带**：由 ``scope`` + ``fingerprint`` 派生一个稳定键，
      在 ``window_seconds`` 窗口内去重。这是**降级**保护 —— 挡得住弱网重试
      和用户连点，挡不住跨窗口的重复提交。把
      ``REQUIRE_CLIENT_REQUEST_ID=True`` 打开即可关闭该降级通道，届时未带键
      的请求一律拒绝（等各端小程序都发版带上键之后再开）。

    Args:
        account: ``ClubAccount`` 实例。
        request_id: 客户端原始幂等键，可为空。
        scope: 业务标注，如 ``withdraw`` / ``order.create``。
        fingerprint: 参与派生键的关键参数串（金额、订单号等），仅降级模式使用。
        window_seconds: 降级模式的去重窗口。

    Returns:
        tuple[bool, str]: ``(是否放行, 拒绝原因)``；放行时原因为空串。
    """
    if account is None:
        return False, '缺少业务账户，无法受理资金操作'

    normalized = normalize_request_id(request_id)
    if normalized:
        if normalized.startswith(DERIVED_KEY_PREFIX):
            # 防止客户端伪造服务端派生键，绕过降级窗口的去重。
            return False, 'client_request_id 非法'
        if claim_idempotency_key(
            account=account, request_id=normalized, scope=scope
        ):
            return True, ''
        return False, '请求重复提交，请勿重复操作'

    if getattr(settings, 'REQUIRE_CLIENT_REQUEST_ID', False):
        return False, '缺少 client_request_id'

    derived = _derive_request_id(scope=scope, fingerprint=fingerprint)
    now = timezone.now()
    # 加行锁：并发的两次重复提交必须串行地看到彼此，否则双方都以为自己是第一个。
    existing = (
        IdempotencyKey.objects.select_for_update()
        .filter(account=account, request_id=derived)
        .first()
    )
    if existing is not None:
        if (now - existing.created_at).total_seconds() < window_seconds:
            return False, '操作过于频繁，请勿重复提交'
        # 超出窗口：视为一次全新操作，顺延窗口继续放行。
        IdempotencyKey.objects.filter(pk=existing.pk).update(created_at=now)
        return True, ''

    if claim_idempotency_key(
        account=account, request_id=derived, scope=scope
    ):
        return True, ''
    return False, '操作过于频繁，请勿重复提交'


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
