"""仅允许公开履约信息和冻结的打手收益作为文案变量。"""
from decimal import Decimal
from string import Formatter

from integrations.kook_config import DEFAULT_TEMPLATE, TEMPLATE_FIELDS
from .gameplay import fulfillment_text


def plain(value, limit):
    return ' '.join(str(value or '').split())[:limit].replace('<@', '＜@')


def template_error(template):
    try:
        fields = []
        for _, field, format_spec, conversion in Formatter().parse(template):
            if field is None:
                continue
            if field not in TEMPLATE_FIELDS or format_spec or conversion:
                return '只支持列出的 {变量}，不支持属性访问、格式表达式或其他字段'
            fields.append(field)
        if not {'order_no', 'provider_income'}.issubset(fields):
            return '文案必须包含 {order_no} 和 {provider_income}，便于核对订单和打手实得'
        worst = {field: '字' * 100 for field in TEMPLATE_FIELDS}
        worst['gameplay_requirements'] = '字' * 500
        if len(template.format(**worst)) > 2000:
            return '展开后的文案可能超过 KOOK 的 2000 字限制，请减少文字或重复变量'
    except (ValueError, KeyError):
        return '变量花括号格式不正确，普通花括号请写成 {{ 和 }}'
    return ''


def dispatch_values(order, trigger):
    income = order.provider_income
    snapshot = order.gameplay_snapshot or {}
    return {
        'trigger': '拒单回池' if trigger == 'PROVIDER_REJECTED' else '待接单',
        'order_no': plain(order.order_no, 32),
        'provider_income': format(Decimal(income) / 10, 'f').rstrip('0').rstrip('.') if income % 10 else str(income // 10),
        'provider_income_yuan': f'{Decimal(income) / 100:.2f}',
        'service_name': plain(order.service_name_snapshot or order.service.name, 100),
        'escort_mode': plain(order.get_escort_mode_display(), 20),
        'game_rounds': str(order.game_rounds)[:20], 'price_unit': plain(snapshot.get('price_unit') or '局', 20),
        'game_region': plain(order.game_region, 40) or '待客服确认',
        'gameplay_name': plain(snapshot.get('name'), 100) or '常规陪玩',
        'platform': plain(snapshot.get('platform_name'), 30) or '待客服确认',
        'security_level': plain(snapshot.get('security_level_name'), 30) or '常规',
        'gameplay_requirements': plain(fulfillment_text(order), 500) if snapshot else '按服务规则履约',
    }


def render_template(order, trigger, template=DEFAULT_TEMPLATE):
    text = template.format(**dispatch_values(order, trigger))
    if order.provider_gender != 'ANY':
        text += '\n陪玩选择：' + order.get_provider_gender_display()
    return text


def sample_values():
    return {'trigger': '待接单', 'order_no': 'TEST-KOOK-001', 'service_name': '联调演示服务',
        'escort_mode': '单陪', 'game_rounds': '1', 'price_unit': '小时', 'game_region': '微信区',
        'gameplay_name': '技术陪玩', 'platform': 'PC端', 'security_level': '常规',
        'gameplay_requirements': '这是功能测试，不可接单，不创建业务订单或资金流水',
        'provider_income': '640', 'provider_income_yuan': '64.00'}
