"""启动期配置自检：把「生产环境跑在开发默认值上」变成启动失败，而不是线上事故。

设计原则
--------
1. **本地零配置**：``DEBUG=True`` 时全部跳过，开发体验不变。
2. **生产 fail-fast**：``DEBUG=False`` 时缺失关键配置或沿用不安全默认值，直接抛
   :class:`django.core.exceptions.ImproperlyConfigured`。因为本模块在
   ``config.settings`` 末尾被调用，异常会在 ``manage.py check`` /
   ``manage.py runserver`` / gunicorn / daphne 的启动阶段就把进程打挂，
   而不是等到线上第一个请求。
3. **纯函数、无 Django 依赖**：本模块**不** import ``django.conf.settings``，
   所有待校验的值由 ``config.settings`` 显式传入，避免与 settings 模块循环导入。

新增校验项请统一返回「错误消息列表」，由 :func:`run_startup_checks` 汇总后一次性抛出，
让运维一次就能看全所有待补的环境变量，而不是改一个报一个。
"""

from typing import Any, Dict, List, Sequence

from django.core.exceptions import ImproperlyConfigured

# 与 config/settings.py 保持一致的开发用兜底密钥。生产若仍是它即视为「未配置」。
DEV_INSECURE_SECRET_KEY = (
    'django-insecure-xa-local-development-key-change-before-production'
)

# 开发用默认数据库口令。生产若仍是它即视为「未配置」。
DEV_INSECURE_DB_PASSWORD = 'xa_dev_password'

# SECRET_KEY 最小长度：低于此长度的密钥熵不足，等同于没设。
MIN_SECRET_KEY_LENGTH = 32


def check_secret_key(debug: bool, secret_key: str) -> List[str]:
    """校验 ``SECRET_KEY``：生产必须由环境变量注入且足够长。"""
    if debug:
        return []
    errors: List[str] = []
    value = (secret_key or '').strip()
    if not value:
        errors.append(
            'DJANGO_SECRET_KEY 未配置：生产环境（DJANGO_DEBUG=False）必须通过环境变量注入。'
        )
        return errors
    if value == DEV_INSECURE_SECRET_KEY:
        errors.append(
            'DJANGO_SECRET_KEY 仍是开发默认值：请为生产环境生成独立密钥并通过环境变量注入。'
        )
    elif len(value) < MIN_SECRET_KEY_LENGTH:
        errors.append(
            f'DJANGO_SECRET_KEY 长度不足 {MIN_SECRET_KEY_LENGTH} 位，熵不足，请重新生成。'
        )
    return errors


def check_allowed_hosts(debug: bool, allowed_hosts: Sequence[str]) -> List[str]:
    """校验 ``ALLOWED_HOSTS``：生产必须显式白名单，禁止通配。"""
    if debug:
        return []
    hosts = [host for host in (allowed_hosts or []) if host]
    if not hosts:
        errors = [
            'DJANGO_ALLOWED_HOSTS 未配置：生产环境必须显式列出对外域名（逗号分隔）。'
        ]
        return errors
    if '*' in hosts:
        return [
            "DJANGO_ALLOWED_HOSTS 含通配符 '*'：生产环境请改为具体域名，避免 Host 头攻击。"
        ]
    return []


def check_cors(
    debug: bool,
    cors_allow_all_origins: bool,
    cors_allowed_origins: Sequence[str],
) -> List[str]:
    """校验跨域策略：生产禁止 ``CORS_ALLOW_ALL_ORIGINS``。"""
    if debug:
        return []
    if cors_allow_all_origins:
        return [
            'CORS_ALLOW_ALL_ORIGINS 在生产环境为 True：请改为通过 CORS_ALLOWED_ORIGINS '
            '显式白名单前端域名。'
        ]
    return []


def check_database(debug: bool, databases: Dict[str, Any]) -> List[str]:
    """校验数据库口令：生产禁止沿用开发默认口令。"""
    if debug:
        return []
    default = (databases or {}).get('default') or {}
    engine = str(default.get('ENGINE', ''))
    # 仅对真实的网络型数据库做口令校验；SQLite 无口令概念。
    if 'sqlite' in engine:
        return []
    if str(default.get('PASSWORD', '')) == DEV_INSECURE_DB_PASSWORD:
        return [
            'MYSQL_PASSWORD 仍是开发默认口令：生产环境必须通过环境变量注入真实口令。'
        ]
    return []


def check_wechat(debug: bool, mock_login: bool) -> List[str]:
    """校验微信登录：生产禁止 mock 登录（会让任何人伪造身份登录）。"""
    if debug:
        return []
    if mock_login:
        return [
            'WECHAT_MOCK_LOGIN 在生产环境为 True：请配置 WECHAT_APPID / WECHAT_SECRET，'
            '否则任何人都能伪造微信身份登录。'
        ]
    return []


def run_startup_checks(
    *,
    debug: bool,
    secret_key: str,
    allowed_hosts: Sequence[str],
    cors_allow_all_origins: bool,
    cors_allowed_origins: Sequence[str],
    databases: Dict[str, Any],
    wechat_mock_login: bool,
) -> None:
    """汇总执行全部启动校验；任一不通过即抛 ``ImproperlyConfigured``。

    Args:
        debug: ``settings.DEBUG``；为 True 时所有校验跳过（本地零配置）。
        secret_key: ``settings.SECRET_KEY``。
        allowed_hosts: ``settings.ALLOWED_HOSTS``。
        cors_allow_all_origins: ``settings.CORS_ALLOW_ALL_ORIGINS``。
        cors_allowed_origins: ``settings.CORS_ALLOWED_ORIGINS``。
        databases: ``settings.DATABASES``。
        wechat_mock_login: ``settings.WECHAT_MOCK_LOGIN``。

    Raises:
        ImproperlyConfigured: 生产环境存在未配置或不安全的配置项。
    """
    errors: List[str] = []
    errors.extend(check_secret_key(debug, secret_key))
    errors.extend(check_allowed_hosts(debug, allowed_hosts))
    errors.extend(check_cors(debug, cors_allow_all_origins, cors_allowed_origins))
    errors.extend(check_database(debug, databases))
    errors.extend(check_wechat(debug, wechat_mock_login))

    if errors:
        numbered = '\n'.join(f'  {idx}. {msg}' for idx, msg in enumerate(errors, 1))
        raise ImproperlyConfigured(
            '生产环境配置校验未通过，已阻止启动：\n'
            f'{numbered}\n'
            '（本地开发请设置 DJANGO_DEBUG=1 以跳过上述校验）'
        )
