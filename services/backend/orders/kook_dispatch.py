"""KOOK card adapter: the payout text is shared with clipboard and WeCom."""
import json
import logging
from urllib.parse import quote
from .models import KookDispatchRecord
from integrations.kook_config import get_kook_config, route_for_order

logger = logging.getLogger(__name__)


def build_card(text, *, role_ids=(), provider_order_url='', admin_order_url='', order_no=''):
    """纯展示链接；抢单仍由陪玩端权限和并发控制处理。"""
    modules = []
    role_ids = [value for value in role_ids if value.isascii() and value.isdigit()]
    if role_ids:
        modules.append({'type': 'section', 'text': {
            'type': 'kmarkdown', 'content': ' '.join(f'(rol){role_id}(rol)' for role_id in role_ids),
        }})
    for start in range(0, len(text), 2000):
        modules.append({'type': 'section', 'text': {'type': 'plain-text', 'content': text[start:start + 2000]}})
    buttons = []
    if provider_order_url:
        buttons.append({'type': 'button', 'theme': 'primary', 'value': provider_order_url,
            'click': 'link', 'text': {'type': 'plain-text', 'content': '打开陪玩端接单'}})
    if admin_order_url and order_no:
        separator = '&' if '?' in admin_order_url else '?'
        buttons.append({'type': 'button', 'theme': 'secondary',
            'value': f'{admin_order_url}{separator}order_no={quote(order_no)}',
            'click': 'link', 'text': {'type': 'plain-text', 'content': '客服查看订单'}})
    if buttons:
        modules.append({'type': 'action-group', 'elements': buttons})
    return json.dumps([{'type': 'card', 'theme': 'warning', 'size': 'lg', 'modules': modules}], ensure_ascii=False)


def build_order_card(order, trigger, *, config=None, route=None):
    from .dispatching import build_dispatch_text
    from .dispatch_templates import render_template
    config = config or get_kook_config()
    route = route or route_for_order(config, order)
    text = render_template(order, trigger, route['template']) if route and route.get('template') else build_dispatch_text(order)
    return build_card(text, role_ids=route.get('mention_role_ids', []) if route else [],
        provider_order_url=config.provider_order_url, admin_order_url=config.admin_order_url, order_no=order.order_no)


def enqueue_kook_dispatch(order, trigger):
    from .dispatching import queue_dispatch
    config = get_kook_config()
    if trigger == KookDispatchRecord.Trigger.NEW_ORDER and not config.auto_new_orders:
        return None
    if trigger == KookDispatchRecord.Trigger.PROVIDER_REJECTED and not config.auto_rejected_orders:
        return None
    try:
        return queue_dispatch(order, 'KOOK')
    except Exception:
        logger.warning('KOOK dispatch enqueue failed: order_id=%s', order.pk)
        return None


def _send_task_safely(record_id):
    from .tasks import send_kook_dispatch
    send_kook_dispatch.delay(record_id)
