"""各发单渠道共用单钱快照、订单校验与投递去重。"""
import hashlib
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from integrations.wecom import valid_webhook
from .models import KookDispatchRecord, Order, WecomDispatchRecord
from integrations.kook_config import get_kook_config, route_for_order

logger = logging.getLogger(__name__)
MODELS = {'KOOK': KookDispatchRecord, 'WECOM': WecomDispatchRecord}


def eligibility_error(order):
    if order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID:
        return '仅已支付、待接单的订单可发单'
    if order.provider_id or order.provider_account_id:
        return '订单已经指定打手'
    if order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
        return '订单接单时间已过期'
    from .team_rules import required_team_size
    if required_team_size(order) != 1:
        return '双陪订单请直接指定两名打手，避免将汇总单钱误认为个人单钱'
    if order.source_order_id or (order.service.service_category and order.service.service_category.is_gift):
        return '礼物订单不进入接单频道'
    return ''


def _plain(value, limit):
    # 不允许订单文本生成额外字段或触发群成员提醒。
    return ' '.join(str(value or '').split())[:limit].replace('<@', '＜@')


def build_dispatch_text(order):
    """只展示冻结的打手收入；不携带原价、实付、折扣、抽成或内部备注。"""
    coins = format(Decimal(order.provider_income) / 10, 'f').rstrip('0').rstrip('.') if order.provider_income % 10 else str(order.provider_income // 10)
    yuan = Decimal(order.provider_income) / 100
    lines = [
        '兴安电竞 · 待接单',
        f'打手单钱：{coins} 兴安币（¥{yuan:.2f}）',
        f'订单号：{order.order_no}',
        f'服务：{_plain(order.service_name_snapshot or order.service.name, 100)}',
        f'模式：{order.get_escort_mode_display()} · {order.game_rounds} {_plain(order.gameplay_snapshot.get("price_unit") or "局", 20)}',
        f'区服：{_plain(order.game_region, 40) or "待客服确认"}',
        '以上为本单打手实得，报单审核通过后结算。',
        '请到打手端确认订单状态并接单。',
    ]
    if order.provider_gender != 'ANY':
        lines.insert(5, '陪玩选择：' + order.get_provider_gender_display())
    from .gameplay import fulfillment_text
    if order.gameplay_snapshot:
        lines.insert(5, '玩法要求：' + _plain(fulfillment_text(order), 500))
    return '\n'.join(lines)


def channel_config(channel, order=None):
    if channel == 'KOOK':
        config = get_kook_config()
        route = route_for_order(config, order) if order else next((r for r in config.routes if r['enabled']), None)
        return bool(config.ready and route), route['channel_id'] if route else '', 'KOOK 频道'
    ready = bool(settings.WECOM_DISPATCH_ENABLED and valid_webhook(settings.WECOM_DISPATCH_WEBHOOK))
    target = hashlib.sha256(settings.WECOM_DISPATCH_WEBHOOK.encode()).hexdigest() if ready else ''
    return ready, target, '企业微信群'


def dispatch_preview(order):
    channels = []
    for channel, model in MODELS.items():
        ready, target, label = channel_config(channel, order)
        record = model.objects.filter(order=order, sequence=order.reject_count).first()
        channels.append({
            'channel': channel, 'label': label, 'configured': ready,
            'status': record.status if record else 'NOT_SENT',
            'last_error': record.last_error if record else '',
            'sent_at': record.sent_at if record else None,
            'target_id': record.channel_id if record else target,
        })
        if channel == 'KOOK':
            from .dispatch_templates import render_template
            config = get_kook_config()
            route = route_for_order(config, order)
            channels[-1]['route_name'] = route['name'] if route else ''
            channels[-1]['content'] = (record.content if record and record.status in ('SENT', 'SENDING', 'UNKNOWN')
                else render_template(order, 'PROVIDER_REJECTED' if order.reject_count else 'NEW_ORDER', route['template'])
                if route and route.get('template') else build_dispatch_text(order))
    reason = eligibility_error(order)
    return {
        'order_id': order.id, 'order_no': order.order_no,
        'provider_income': order.provider_income, 'content': build_dispatch_text(order),
        'can_send': not reason, 'reason': reason, 'channels': channels,
    }


def queue_dispatch(order, channel, *, manual=False):
    """同一订单待接单轮次、同一渠道只发一次；明确失败才允许人工重试。"""
    model = MODELS[channel]
    ready, target, _ = channel_config(channel, order)
    if not ready:
        return None
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if eligibility_error(locked):
            return None
        defaults = {
            'trigger': model.Trigger.PROVIDER_REJECTED if locked.reject_count else model.Trigger.NEW_ORDER,
            'channel_id': target, 'content': build_dispatch_text(locked),
        }
        if channel == 'KOOK':
            from .dispatch_templates import render_template
            from .kook_dispatch import build_order_card
            config = get_kook_config()
            route = route_for_order(config, locked)
            if not config.ready or not route:
                return None
            defaults.update(channel_id=route['channel_id'], route_key=route['id'],
                card_content=build_order_card(locked, defaults['trigger'], config=config, route=route))
            if route.get('template'):
                defaults['content'] = render_template(locked, defaults['trigger'], route['template'])
        record, created = model.objects.get_or_create(
            order=locked, sequence=locked.reject_count,
            defaults=defaults,
        )
        if not created and manual and record.status == model.Status.FAILED:
            record.status = model.Status.PENDING
            record.last_error = ''
            for key, value in defaults.items():
                setattr(record, key, value)
            record.save(update_fields=['status', 'last_error', *defaults])
            created = True
        if created:
            transaction.on_commit(lambda: send_task_safely(channel, record.pk))
        return record


def send_task_safely(channel, record_id):
    try:
        if channel == 'KOOK':
            # 兼容现有 Celery 任务名和历史队列。
            from .kook_dispatch import _send_task_safely
            _send_task_safely(record_id)
        else:
            from .tasks import send_wecom_dispatch
            send_wecom_dispatch.delay(record_id)
    except Exception:
        MODELS[channel].objects.filter(pk=record_id, status='PENDING').update(
            status='FAILED', last_error='任务未能入队，请重试或复制文案发群',
        )
        logger.warning('发单任务未能入队: channel=%s record_id=%s', channel, record_id)


def enqueue_wecom_dispatch(order, trigger):
    if trigger == KookDispatchRecord.Trigger.NEW_ORDER and not settings.WECOM_DISPATCH_NEW_ORDERS:
        return None
    if trigger == KookDispatchRecord.Trigger.PROVIDER_REJECTED and not settings.WECOM_DISPATCH_REJECTED_ORDERS:
        return None
    try:
        return queue_dispatch(order, 'WECOM')
    except Exception:
        logger.warning('企微发单入队失败: order_id=%s', order.pk)
        return None


def deliver_dispatch(channel, record_id):
    from integrations.kook import KookClient, KookError, KookDeliveryUnknown
    from integrations.wecom import WecomClient, WecomError, WecomDeliveryUnknown
    from .kook_dispatch import build_order_card

    model = MODELS[channel]
    # CAS 在发请求前提交，重复任务不会并行发送；崩溃留下 SENDING，禁止自动重发。
    with transaction.atomic():
        record = model.objects.filter(pk=record_id).first()
        if record is None:
            return 'record_not_found'
        order = Order.objects.select_for_update().get(pk=record.order_id)
        record = model.objects.select_for_update().get(pk=record_id)
        if record.status == 'SENT':
            return f'already_sent:{record.message_id}'
        if record.status != 'PENDING':
            return f'skip:delivery={record.status}'
        reason = eligibility_error(order)
        if record.sequence != order.reject_count:
            reason = '订单已进入新的派单轮次'
        if reason:
            record.status, record.last_error = 'SKIPPED', reason
            record.save(update_fields=['status', 'last_error'])
            return f'skip:status={order.status}'
        ready, target, _ = channel_config(channel, order)
        kook_config, route = None, None
        if channel == 'KOOK':
            kook_config = get_kook_config()
            route = route_for_order(kook_config, order)
            if record.route_key and (not route or record.route_key != route['id']):
                ready = False
        if not ready or target != record.channel_id:
            record.status, record.last_error = 'FAILED', '渠道未配置或目标已变更，请检查配置后重试'
            record.save(update_fields=['status', 'last_error'])
            return 'failed:configuration'
        record.status = 'SENDING'
        record.attempts += 1
        if channel != 'KOOK' or not record.card_content:
            record.content = build_dispatch_text(order)
        record.save(update_fields=['status', 'attempts', 'content'])
    try:
        message_id = ''
        if channel == 'KOOK':
            result = KookClient(token=kook_config.token).send_channel_message(
                channel_id=record.channel_id, content=record.card_content or build_order_card(order, record.trigger, config=kook_config, route=route),
                message_type=10, nonce=f'xa-order-{order.pk}-{record.sequence}',
            )
            message_id = result.message_id
        else:
            WecomClient().send_text(record.content)
    except (KookDeliveryUnknown, WecomDeliveryUnknown):
        model.objects.filter(pk=record_id).update(status='UNKNOWN', last_error='发送结果待核对，请先检查群内消息，避免重复发单')
        return 'unknown'
    except (KookError, WecomError) as exc:
        model.objects.filter(pk=record_id).update(status='FAILED', last_error=str(exc)[:500])
        return 'failed:rejected'
    except Exception:
        model.objects.filter(pk=record_id).update(status='UNKNOWN', last_error='发送结果待核对，请先检查群内消息')
        return 'unknown'
    model.objects.filter(pk=record_id).update(status='SENT', message_id=message_id, last_error='', sent_at=timezone.now())
    return f'sent:{message_id}'
