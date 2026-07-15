"""将待接单订单转换为 KOOK 派单卡片并异步投递。"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import KookDispatchRecord, Order

logger = logging.getLogger(__name__)


def build_order_card(order: Order, trigger: str) -> str:
    """生成 KOOK Card Message JSON 字符串；用户输入均使用 plain-text。"""
    trigger_label = dict(KookDispatchRecord.Trigger.choices).get(trigger, trigger)
    fields = [
        _field('订单号', order.order_no),
        _field('服务项目', order.service_name_snapshot or order.service.name),
        _field('陪玩模式', order.get_escort_mode_display()),
        _field('订单金额', f'{order.amount / 10:g} 兴安币'),
        _field('局数', str(order.game_rounds)),
        _field('游戏区服', order.game_region or '未填写'),
    ]
    details = []
    if order.game_nickname:
        details.append(f'游戏昵称：{order.game_nickname}')
    if order.remark:
        details.append(f'备注：{order.remark}')

    modules = []
    role_ids = [value for value in settings.KOOK_DISPATCH_MENTION_ROLE_IDS if value.isdigit()]
    if role_ids:
        modules.append({
            'type': 'section',
            'text': {
                'type': 'kmarkdown',
                'content': ' '.join(f'(rol){role_id}(rol)' for role_id in role_ids),
            },
        })
    modules.extend([
        {'type': 'header', 'text': {'type': 'plain-text', 'content': '兴安电竞 · 待派单'}},
        {'type': 'section', 'text': {'type': 'paragraph', 'cols': 2, 'fields': fields}},
    ])
    if details:
        modules.append({
            'type': 'section',
            'text': {'type': 'plain-text', 'content': '\n'.join(details)[:1900]},
        })
    modules.extend([
        {'type': 'divider'},
        {
            'type': 'context',
            'elements': [{
                'type': 'plain-text',
                'content': (
                    f'{trigger_label} · {timezone.localtime(order.updated_at):%Y-%m-%d %H:%M} · '
                    '接单前请先在运营后台确认订单仍为待接单状态'
                ),
            }],
        },
    ])
    if settings.KOOK_ADMIN_ORDER_URL:
        separator = '&' if '?' in settings.KOOK_ADMIN_ORDER_URL else '?'
        url = f'{settings.KOOK_ADMIN_ORDER_URL}{separator}order_no={quote(order.order_no)}'
        modules.append({
            'type': 'action-group',
            'elements': [{
                'type': 'button',
                'theme': 'primary',
                'value': url,
                'click': 'link',
                'text': {'type': 'plain-text', 'content': '前往运营后台派单'},
            }],
        })
    card = [{'type': 'card', 'theme': 'warning', 'size': 'lg', 'modules': modules}]
    return json.dumps(card, ensure_ascii=False, separators=(',', ':'))


def _field(label: str, value: str) -> dict:
    return {'type': 'plain-text', 'content': f'{label}\n{value}'[:2000]}


def enqueue_kook_dispatch(order: Order, trigger: str) -> KookDispatchRecord | None:
    """幂等创建发送记录并投递 Celery；任何异常均不影响订单主流程。"""
    if not settings.KOOK_ENABLED:
        return None
    if trigger == KookDispatchRecord.Trigger.NEW_ORDER and not settings.KOOK_DISPATCH_NEW_ORDERS:
        return None
    if (trigger == KookDispatchRecord.Trigger.PROVIDER_REJECTED
            and not settings.KOOK_DISPATCH_REJECTED_ORDERS):
        return None
    if not settings.KOOK_BOT_TOKEN or not settings.KOOK_DISPATCH_CHANNEL_ID:
        logger.warning('KOOK 已启用，但机器人 Token 或派单频道 ID 未配置')
        return None

    sequence = order.reject_count if trigger == KookDispatchRecord.Trigger.PROVIDER_REJECTED else 0
    try:
        record, created = KookDispatchRecord.objects.get_or_create(
            order=order,
            sequence=sequence,
            defaults={
                'trigger': trigger,
                'channel_id': settings.KOOK_DISPATCH_CHANNEL_ID,
            },
        )
        if created:
            transaction.on_commit(lambda: _send_task_safely(record.id))
        return record
    except Exception:
        logger.exception('创建 KOOK 派单记录失败: order_id=%s', order.id)
        return None


def _send_task_safely(record_id: int) -> None:
    try:
        from .tasks import send_kook_dispatch
        send_kook_dispatch.delay(record_id)
    except Exception:
        logger.exception('投递 KOOK 派单任务失败: record_id=%s', record_id)
