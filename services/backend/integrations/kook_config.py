"""每次派单读取数据库配置，Web 与 Worker 无需重启即可使用同一规则。"""
import base64
from dataclasses import dataclass, field
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


DEFAULT_TEMPLATE = (
    '兴安电竞 · {trigger}\n'
    '打手单钱：{provider_income} 兴安币（¥{provider_income_yuan}）\n'
    '订单号：{order_no}\n'
    '服务：{service_name}\n'
    '模式：{escort_mode} · {game_rounds} {price_unit}\n'
    '区服：{game_region}\n'
    '玩法要求：{gameplay_requirements}\n'
    '以上为本单打手实得，报单审核通过后结算。\n'
    '请到打手端确认订单状态并接单。'
)
TEMPLATE_FIELDS = {
    'trigger': '新单待接 / 拒单回池', 'order_no': '订单号', 'service_name': '服务名称',
    'escort_mode': '单陪模式', 'game_rounds': '数量', 'price_unit': '单位',
    'game_region': '区服', 'gameplay_name': '玩法名称', 'platform': '平台',
    'security_level': '难度', 'gameplay_requirements': '履约要求',
    'provider_income': '打手实得（兴安币）', 'provider_income_yuan': '打手实得（人民币元）',
}


def _fernet(secret):
    key = hashlib.sha256(('xa:kook-bot-token:v1:' + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(token):
    return _fernet(settings.SECRET_KEY).encrypt(token.encode()).decode() if token else ''


def decrypt_token(ciphertext):
    if not ciphertext:
        return ''
    for secret in [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]:
        try:
            return _fernet(secret).decrypt(ciphertext.encode()).decode()
        except (InvalidToken, UnicodeError):
            pass
    return ''  # 密钥变更 / 密文损坏时禁止用旧环境凭据发送。


@dataclass(frozen=True)
class KookRuntimeConfig:
    enabled: bool
    token: str = field(repr=False)
    auto_new_orders: bool
    auto_rejected_orders: bool
    routes: list
    provider_order_url: str = ''
    admin_order_url: str = ''
    source: str = 'database'

    @property
    def ready(self):
        return bool(self.enabled and self.token and any(r['enabled'] for r in self.routes))


def get_kook_config():
    from console.models import KookDispatchConfig
    row = KookDispatchConfig.objects.filter(pk=1).first()
    if row:
        return KookRuntimeConfig(row.enabled, decrypt_token(row.bot_token_ciphertext),
            row.auto_new_orders, row.auto_rejected_orders, row.routes, row.provider_order_url)
    routes = []
    if settings.KOOK_DISPATCH_CHANNEL_ID:
        routes = [{'id': 'legacy', 'name': '默认派单频道', 'enabled': True,
            'channel_id': settings.KOOK_DISPATCH_CHANNEL_ID, 'game_category_id': None,
            'gameplay_id': None, 'platform': '', 'template': '',
            'mention_role_ids': list(settings.KOOK_DISPATCH_MENTION_ROLE_IDS)}]
    return KookRuntimeConfig(settings.KOOK_ENABLED, settings.KOOK_BOT_TOKEN,
        settings.KOOK_DISPATCH_NEW_ORDERS, settings.KOOK_DISPATCH_REJECTED_ORDERS,
        routes, admin_order_url=settings.KOOK_ADMIN_ORDER_URL, source='environment')


def route_for_order(config, order):
    """按后台规则顺序命中一个频道；兜底规则应放在末尾。"""
    for route in config.routes:
        if not route['enabled']:
            continue
        if route.get('game_category_id') and route['game_category_id'] != order.service.game_category_id:
            continue
        if route.get('gameplay_id') and route['gameplay_id'] != (order.gameplay_snapshot.get('id') or order.service.gameplay_id):
            continue
        if route.get('platform') and route['platform'] != (order.gameplay_snapshot.get('platform') or order.service.platform):
            continue
        return route
    return None
