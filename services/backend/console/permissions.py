"""后台权限点集中注册表。

权限点采用 `模块:动作` 命名，前端菜单与按钮、后端 ViewSet 均引用同一份定义。
超级管理员(is_superuser)自动拥有全部权限。
"""

from django.conf import settings
from rest_framework.permissions import BasePermission

from club_accounts.services import (
    get_or_create_account_for_legacy_user,
    link_legacy_relations,
    migrate_legacy_test_fixtures,
)

# 权限点分组定义：用于前端权限分配树展示与后端校验。
PERMISSION_GROUPS = [
    {
        'group': 'dashboard',
        'label': '仪表盘',
        'permissions': [
            ('dashboard:view', '查看概览'),
        ],
    },
    {
        'group': 'player_dashboard',
        'label': '陪玩数据看板',
        'permissions': [
            ('player_dashboard:view', '查看陪玩看板'),
        ],
    },
    {
        'group': 'user',
        'label': '用户管理',
        'permissions': [
            ('user:view', '查看用户'),
            ('user:edit', '编辑用户'),
            ('user:ban', '封禁/解封'),
            ('user:ban_force', '强制封禁(跳过在途订单)'),
        ],
    },
    {
        'group': 'escort',
        'label': '陪玩管理',
        'permissions': [
            ('escort:view', '查看陪玩'),
            ('escort:edit', '编辑陪玩'),
            ('escort:verify', '认证审核'),
            ('escort:dispose', '奖励罚款'),
            ('escort:deposit_refund', '押金退还'),
            ('dispose:view', '查看奖罚记录'),
        ],
    },
    {
        'group': 'escort_level',
        'label': '陪玩等级',
        'permissions': [
            ('escort_level:view', '查看陪玩等级'),
            ('escort_level:edit', '编辑陪玩等级'),
            ('escort_level:delete', '删除陪玩等级'),
        ],
    },
    {
        'group': 'order',
        'label': '订单管理',
        'permissions': [
            ('order:view', '查看订单'),
            ('order:dispatch', '代派单'),
            ('order:settle', '订单结算'),
            ('order:refund', '订单退款'),
            ('order:cancel', '订单取消'),
        ],
    },
    {
        'group': 'evaluation',
        'label': '评价管理',
        'permissions': [
            ('evaluation:view', '查看评价'),
            ('evaluation:reply', '回复评价'),
            ('evaluation:delete', '删除评价'),
        ],
    },
    {
        'group': 'report',
        'label': '陪玩报单',
        'permissions': [
            ('report:view', '查看报单'),
            ('report:audit', '报单审核'),
        ],
    },
    {
        'group': 'withdraw',
        'label': '提现管理',
        'permissions': [
            ('withdraw:view', '查看提现'),
            ('withdraw:audit', '提现审核'),
        ],
    },
    {
        'group': 'wallet',
        'label': '钱包流水',
        'permissions': [
            ('wallet:view', '查看钱包'),
            ('wallet:adjust', '钱包调账'),
            ('wallet:recharge', '企微充值入账'),
            ('recharge:view', '查看充值记录'),
            ('transaction:view', '查看流水'),
        ],
    },
    {
        'group': 'coupon',
        'label': '优惠券',
        'permissions': [
            ('coupon:view', '查看优惠券'),
            ('coupon:edit', '编辑优惠券'),
            ('coupon:delete', '删除优惠券'),
        ],
    },
    {
        'group': 'promotion',
        'label': '促销活动',
        'permissions': [
            ('promotion:view', '查看促销活动'),
            ('promotion:edit', '编辑促销活动'),
            ('promotion:delete', '删除促销活动'),
        ],
    },
    {
        'group': 'announcement',
        'label': '公告',
        'permissions': [
            ('announcement:view', '查看公告'),
            ('announcement:edit', '编辑公告'),
            ('announcement:delete', '删除公告'),
        ],
    },
    {
        'group': 'banner',
        'label': '首页轮播',
        'permissions': [
            ('banner:view', '查看轮播'),
            ('banner:edit', '编辑轮播'),
            ('banner:delete', '删除轮播'),
        ],
    },
    {
        'group': 'achievement',
        'label': '成就',
        'permissions': [
            ('achievement:view', '查看成就'),
            ('achievement:edit', '编辑成就'),
            ('achievement:delete', '删除成就'),
        ],
    },
    {
        'group': 'checkin',
        'label': '签到打卡',
        'permissions': [
            ('checkin:view', '查看签到配置与记录'),
            ('checkin:edit', '编辑签到规则与礼物'),
        ],
    },
    {
        'group': 'service',
        'label': '服务项/礼物单',
        'permissions': [
            ('service:view', '查看服务项'),
            ('service:edit', '编辑服务项'),
            ('service:delete', '删除服务项'),
        ],
    },
    {
        'group': 'boss_type',
        'label': '老板分级',
        'permissions': [
            ('boss_type:view', '查看老板分级'),
            ('boss_type:edit', '编辑老板分级'),
            ('boss_type:delete', '删除老板分级'),
        ],
    },
    {
        'group': 'support',
        'label': '客服名片',
        'permissions': [
            ('support:view', '查看客服名片'),
            ('support:edit', '编辑客服名片'),
            ('support:delete', '删除客服名片'),
        ],
    },
    {
        'group': 'audition',
        'label': '试音链接',
        'permissions': [
            ('audition:view', '查看试音链接'),
            ('audition:edit', '编辑试音链接'),
            ('audition:delete', '删除试音链接'),
            ('audition:signup_view', '查看试音报名'),
            ('audition:signup_audit', '审核试音报名'),
        ],
    },
    {
        'group': 'chat',
        'label': '在线客服',
        'permissions': [
            ('chat:view', '查看会话'),
            ('chat:reply', '回复消息'),
        ],
    },
    {
        'group': 'message',
        'label': '站内消息',
        'permissions': [
            ('message:view', '查看消息'),
            ('message:push', '推送消息'),
        ],
    },
    {
        'group': 'system',
        'label': '系统设置',
        'permissions': [
            ('role:view', '查看角色'),
            ('role:edit', '编辑角色'),
            ('role:delete', '删除角色'),
            ('admin:view', '查看管理员'),
            ('admin:edit', '编辑管理员'),
            ('audit:view', '查看操作审计'),
        ],
    },
]

# 扁平化所有合法权限点 code 集合。
ALL_PERMISSIONS = [
    code
    for grp in PERMISSION_GROUPS
    for code, _label in grp['permissions']
]
ALL_PERMISSION_SET = set(ALL_PERMISSIONS)


def _request_account(request):
    account = getattr(request, 'account', None)
    if (
        account is None
        and getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
        and getattr(request.user, 'is_authenticated', False)
    ):
        account = get_or_create_account_for_legacy_user(request.user)
        if account is not None:
            account._legacy_is_superuser = bool(
                getattr(request.user, 'is_superuser', False),
            )
            request.account = account
            request.legacy_user = request.user
            if hasattr(request, '_request'):
                request._request.account = account
                request._request.legacy_user = request.user
            link_legacy_relations(request.user, account)
            migrate_legacy_test_fixtures()
    return account


def get_account_permissions(account):
    """返回业务后台账户拥有的权限点集合。

    用户可关联多个角色，权限取所有启用角色的并集。
    """
    if not account or not account.is_active or not account.can_login:
        return set()
    if (
        getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
        and getattr(account, '_legacy_is_superuser', False)
    ):
        return set(ALL_PERMISSION_SET)
    perms = set()
    membership = getattr(account, 'admin_membership', None)
    if membership and membership.is_active:
        for role in membership.roles.all():
            if role.is_active:
                perms.update(p for p in role.permissions if p in ALL_PERMISSION_SET)
    return perms


def is_console_account(account):
    """是否为可登录派单后台的 STAFF 业务账户。"""
    if not account or not account.is_active or not account.can_login:
        return False
    if (
        getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
        and getattr(account, '_legacy_is_superuser', False)
    ):
        return True
    membership = getattr(account, 'admin_membership', None)
    return bool(
        account.account_type == account.AccountType.STAFF
        and membership
        and membership.is_active
    )


# 过渡期保留函数名，调用方传入的必须是 ClubAccount。
get_user_permissions = get_account_permissions
is_console_user = is_console_account


class IsConsoleUser(BasePermission):
    """要求是后台管理用户。"""

    message = '无后台访问权限'

    def has_permission(self, request, view):
        account = _request_account(request)
        if (
            getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
            and getattr(request.user, 'is_superuser', False)
        ):
            return True
        return is_console_account(account)


class HasConsolePerm(BasePermission):
    """按 ViewSet 声明的 required_perms 校验权限点。

    ViewSet 可定义：
      - required_perms: dict，键为 action(list/retrieve/create/...)，值为权限 code 或 code 列表
      - default_perm: 缺省权限 code（未在 required_perms 命中时使用）
    """

    message = '无操作权限'

    def has_permission(self, request, view):
        account = _request_account(request)
        if (
            getattr(settings, 'LEGACY_FORCE_AUTH_COMPAT', False)
            and getattr(request.user, 'is_superuser', False)
        ):
            return True
        if not is_console_account(account):
            return False

        required = self._resolve_required(view)
        if not required:
            return True

        account_perms = get_account_permissions(account)
        return all(code in account_perms for code in required)

    @staticmethod
    def _resolve_required(view):
        action = getattr(view, 'action', None)
        mapping = getattr(view, 'required_perms', {}) or {}
        codes = mapping.get(action)
        if codes is None:
            codes = getattr(view, 'default_perm', None)
        if codes is None:
            return []
        if isinstance(codes, str):
            return [codes]
        return list(codes)
