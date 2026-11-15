"""各发单渠道共用单钱快照、订单校验与投递去重。"""
import hashlib
import json
import logging

from stores.db import transaction
from django.utils import timezone

from .models import KookDispatchRecord, Order, WecomDispatchRecord
from integrations.kook_config import get_kook_config, route_for_order
from stores.conf import store_name

logger = logging.getLogger(__name__)
MODELS = {'KOOK': KookDispatchRecord, 'WECOM': WecomDispatchRecord}
CONTENT_CONTRACT_VERSION = 1
MISSING_ORIGINAL_PRICE = '订单原价快照缺失，请先核对后发单'
QUEUE_ERROR = '任务入队结果未确认，请先检查群内消息后处理'


def _pool_group_enabled():
    from .grab_group_dispatch import enabled
    return enabled()


def _contract_version():
    # A queued pool message must never fall back to legacy immediate sending
    # if an operator disables the new gate while that message is pending.
    return 2 if _pool_group_enabled() else CONTENT_CONTRACT_VERSION


def eligibility_error(order):
    pool_group = _pool_group_enabled()
    from .models import OrderGrabPoolOpeningIntent
    if OrderGrabPoolOpeningIntent.objects.filter(order=order).exists():
        if not pool_group:
            return '抢单订单须使用按通行证可见时间的群发'
        from .grab_single_flow import enabled as single_enabled
        from .grab_team_flow import enabled as team_enabled
        if not (team_enabled() if order.required_provider_count == 2 else single_enabled()):
            return '抢单窗口尚未开放'
    if order.dispatch_route != Order.DispatchRoute.PUBLIC and not pool_group:
        return '该订单由客服派单，不进入接单频道'
    if order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID:
        return '仅已支付、待接单的订单可发单'
    if order.requires_boss_selection:
        return '老板选人订单不进入普通抢单群发'
    from .direct_payment_models import DirectPaymentIntent
    from .direct_payment_quote import quote_matches_intent
    if 'direct_payment' in order._state.fields_cache:
        intent = order._state.fields_cache['direct_payment']
    else:
        intent = DirectPaymentIntent.objects.filter(order=order).first()
    if intent and (intent.status != DirectPaymentIntent.Status.PAID or intent.evidence_risk_at
                   or not quote_matches_intent(intent, order)):
        return '支付证据待核对，不能发单'
    if order.provider_id or order.provider_account_id:
        return '订单已经指定打手'
    if pool_group and order.providers.filter(released_at__isnull=True).exists():
        return '订单已经安排陪玩'
    if not order.original_amount:
        return MISSING_ORIGINAL_PRICE
    from .team_rules import required_team_size
    if required_team_size(order) != 1 and not pool_group:
        return '双陪订单暂不支持群发，避免将整单收益误认为单人收益'
    if order.source_order_id or order.is_gift_snapshot:
        return '礼物订单不进入接单频道'
    if pool_group:
        from .grab_group_dispatch import delivery_timing
        try:
            _, deadline = delivery_timing(order)
        except ValueError as exc:
            return str(exc)
        from .grab_group_dispatch import deadline_reached
        if deadline_reached(deadline):
            return '订单接单时间已过期'
    elif order.auto_cancel_at and order.auto_cancel_at <= timezone.now():
        return '订单接单时间已过期'
    return ''


def _plain(value, limit):
    # 不允许订单文本生成额外字段或触发群成员提醒。
    return ' '.join(str(value or '').split())[:limit].replace('<@', '＜@')


def build_dispatch_text(order):
    """只公开订单原价及履约要求，不承诺某位陪玩的收益。"""
    from .dispatch_templates import dispatch_values, team_requirement
    values = dispatch_values(order, 'NEW_ORDER')
    lines = [
        f'{_plain(store_name(), 80)} · 待接单',
        f'服务原价：{values["original_amount"]} {values["currency_name"]}',
        f'订单号：{order.order_no}',
        f'服务：{_plain(order.service_name_snapshot or order.service.name, 100)}',
        f'模式：{values["escort_mode"]} · {order.game_rounds} {_plain(order.gameplay_snapshot.get("price_unit") or "局", 20)}',
        f'区服：{_plain(order.game_region, 40) or "待客服确认"}',
        '请到打手端确认订单状态并接单。',
    ]
    if order.provider_gender != 'ANY':
        lines.insert(5, '陪玩选择：' + order.get_provider_gender_display())
    requirement = team_requirement(order)
    if requirement:
        lines.insert(5, requirement)
    from .gameplay import fulfillment_text
    if order.gameplay_snapshot:
        lines.insert(5, '玩法要求：' + _plain(fulfillment_text(order), 500))
    return '\n'.join(lines)


def channel_config(channel, order=None, *, kook_config=None):
    if channel == 'KOOK':
        config = kook_config if kook_config is not None else get_kook_config()
        route = route_for_order(config, order) if order else next((r for r in config.routes if r['enabled']), None)
        return bool(config.ready and route), route['channel_id'] if route else '', 'KOOK 频道'
    from integrations.wecom_config import get_wecom_config
    config = get_wecom_config()
    ready = config.ready
    target = hashlib.sha256(config.webhook.encode()).hexdigest() if ready else ''
    return ready, target, '企业微信群'


def prepare_dispatch(order, channel, *, kook_config=None):
    """Construct a server-side plan that can be checked and queued unchanged."""
    ready, target, label = channel_config(channel, order, kook_config=kook_config)
    defaults = None
    if ready:
        model = MODELS[channel]
        trigger = model.Trigger.PROVIDER_REJECTED if order.reject_count else model.Trigger.NEW_ORDER
        defaults = {'trigger': trigger, 'channel_id': target, 'content': build_dispatch_text(order),
                    'content_contract_version': _contract_version()}
        if channel == 'KOOK':
            from .dispatch_templates import render_template
            from .kook_dispatch import build_order_card
            config = kook_config if kook_config is not None else get_kook_config()
            route = route_for_order(config, order)
            if not config.ready or not route:
                return {'ready': False, 'target': '', 'label': label, 'defaults': None}
            defaults.update(channel_id=route['channel_id'], route_key=route['id'],
                card_content=build_order_card(order, trigger, config=config, route=route))
            if route.get('template'):
                defaults['content'] = render_template(order, trigger, route['template'])
    return {'ready': ready, 'target': target, 'label': label, 'defaults': defaults}


def dispatch_preview(order, *, kook_config=None, include_plans=False):
    config = kook_config if kook_config is not None else get_kook_config()
    reason = eligibility_error(order)
    channels = []
    plans = {}
    for channel, model in MODELS.items():
        plan = prepare_dispatch(order, channel, kook_config=config)
        ready, target, label = plan['ready'], plan['target'], plan['label']
        record = model.objects.filter(order=order, sequence=order.reject_count).first()
        signature = {'order_id': order.pk, 'sequence': order.reject_count, 'channel': channel,
                     'reason': reason, 'status': record.status if record else 'NOT_SENT',
                     'record_id': record.pk if record else None, 'plan': plan}
        confirmation_key = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        plans[channel] = plan
        # A failed record may be retried against the current configuration;
        # all other existing records describe their original queued attempt.
        recorded = bool(record and record.status != 'FAILED')
        channels.append({
            'channel': channel, 'label': label, 'configured': ready,
            'status': record.status if record else 'NOT_SENT',
            'confirmation_key': confirmation_key,
            'last_error': record.last_error if record else '',
            'sent_at': record.sent_at if record else None,
            'target_id': record.channel_id if recorded else target,
        })
        if channel == 'KOOK':
            from stores.license_benefits import check_new_benefits
            outcome, _ = check_new_benefits('CONTENT_OPERATIONS')
            channels[-1]['license_status'] = outcome
            channels[-1]['can_create'] = outcome == 'ALLOW'
            from .dispatch_templates import render_template, template_error
            from .kook_dispatch import summarize_card
            route = route_for_order(config, order)
            legacy_pending = bool(record and record.status == 'PENDING'
                                  and record.content_contract_version != _contract_version())
            channels[-1]['target_source'] = 'recorded' if recorded else 'current'
            channels[-1]['route_name'] = '' if recorded else (route['name'] if route else '')
            channels[-1]['previous_target_id'] = record.channel_id if record and record.status == 'FAILED' and record.channel_id != target else ''
            invalid_template = bool(route and route.get('template') and template_error(route['template']))
            channels[-1]['content_source'] = (('unavailable' if legacy_pending else 'recorded' if record.content else 'unavailable') if recorded
                else ('fallback' if not route or invalid_template else 'retry' if record else 'current'))
            channels[-1]['content'] = (('' if legacy_pending else record.content) if recorded else
                render_template(order, 'PROVIDER_REJECTED' if order.reject_count else 'NEW_ORDER', route['template'])
                if route and route.get('template') else build_dispatch_text(order))
            card = ('' if legacy_pending else record.card_content) if recorded else (plan['defaults']['card_content'] if plan['defaults'] else '')
            channels[-1]['card_details'] = summarize_card(card)
    data = {
        'order_id': order.id, 'order_no': order.order_no,
        'provider_income': order.provider_income, 'content': build_dispatch_text(order),
        'can_send': not reason, 'reason': reason, 'channels': channels,
    }
    if _pool_group_enabled() and not reason:
        from .grab_group_dispatch import delivery_timing
        data['earliest_delivery_at'] = delivery_timing(order)[0]
    return (data, plans) if include_plans else data


def queue_dispatch(order, channel, *, manual=False, prepared=None):
    """同一订单待接单轮次、同一渠道只发一次；明确失败才允许人工重试。"""
    model = MODELS[channel]
    if prepared is None:
        ready, _, _ = channel_config(channel, order)
    else:
        ready = prepared['ready']
    if not ready:
        return None
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if eligibility_error(locked):
            return None
        # Automatic callers build their plan after acquiring the order lock.
        plan = prepared if prepared is not None else prepare_dispatch(locked, channel)
        if not plan['ready']:
            return None
        defaults = plan['defaults']
        if not defaults or defaults.get('content_contract_version') != _contract_version():
            return None
        defaults = dict(defaults)
        # Existing in-flight attempts remain recoverable after a downgrade.
        # A new send, including a manual retry after a definite failure, needs
        # current content-operations entitlement under the tenant context.
        existing = model.objects.select_for_update().filter(
            order=locked, sequence=locked.reject_count).first()
        if (existing is None or (manual and existing.status == model.Status.FAILED)):
            from stores.license_benefits import require_content_operations
            require_content_operations()
        due = None
        if _pool_group_enabled():
            from .grab_group_dispatch import delivery_timing
            due, _ = delivery_timing(locked)
            defaults.update(pool_delivery_due_at=due, pool_recovery_lease_until=None, pool_enqueue_failed=False)
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
            if _pool_group_enabled():
                transaction.on_commit(lambda: send_task_safely(channel, record.pk, eta=due))
            else:
                transaction.on_commit(lambda: send_task_safely(channel, record.pk))
        return record


def reschedule_pool_dispatch(order_id):
    """Wake pending messages after a persisted pool reopening.

    The reopening transaction must hold the same order lock and update its
    deadline before calling this hook. Duplicate tasks remain safe via the
    record's delivery-state compare-and-set.
    """
    if not _pool_group_enabled():
        return 0
    from .grab_group_dispatch import delivery_timing
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if eligibility_error(order):
            return 0
        due, _ = delivery_timing(order)
        count = 0
        for channel, model in MODELS.items():
            from django.db.models import Q
            record = model.objects.select_for_update().filter(
                Q(status='PENDING') | Q(status='FAILED', pool_enqueue_failed=True),
                order=order, sequence=order.reject_count, content_contract_version=2).first()
            if record:
                record.status, record.last_error, record.pool_enqueue_failed = 'PENDING', '', False
                record.pool_delivery_due_at, record.pool_recovery_lease_until = due, None
                record.save(update_fields=['status', 'last_error', 'pool_enqueue_failed',
                                           'pool_delivery_due_at', 'pool_recovery_lease_until'])
                transaction.on_commit(lambda channel=channel, pk=record.pk: send_task_safely(channel, pk, eta=due))
                count += 1
        return count


def send_task_safely(channel, record_id, *, eta=None):
    try:
        if channel == 'KOOK':
            # 兼容现有 Celery 任务名和历史队列。
            from .kook_dispatch import _send_task_safely
            if eta is None:
                _send_task_safely(record_id)
            else:
                from .tasks import send_kook_dispatch
                send_kook_dispatch.apply_async(args=[record_id], eta=eta)
        else:
            from .tasks import send_wecom_dispatch
            if eta is None:
                send_wecom_dispatch.delay(record_id)
            else:
                send_wecom_dispatch.apply_async(args=[record_id], eta=eta)
    except Exception:
        MODELS[channel].objects.filter(pk=record_id, status='PENDING').update(
            status='FAILED', last_error=QUEUE_ERROR, pool_enqueue_failed=True,
        )
        logger.warning('发单任务入队结果未确认: channel=%s record_id=%s', channel, record_id)


def enqueue_wecom_dispatch(order, trigger):
    from integrations.wecom_config import get_wecom_config
    try:
        # Keep database/configuration errors inside a savepoint so an outer
        # payment transaction can continue without an invalid connection state.
        with transaction.atomic():
            config = get_wecom_config()
            if not config.ready:
                return None
            if trigger == KookDispatchRecord.Trigger.NEW_ORDER and not config.auto_new_orders:
                return None
            if trigger == KookDispatchRecord.Trigger.PROVIDER_REJECTED and not config.auto_rejected_orders:
                return None
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
        if _pool_group_enabled():
            from .grab_clock_contract import production_enabled
            from .models import OrderGrabRound
            if production_enabled():
                latest = (OrderGrabRound.objects.filter(pool__order=order)
                          .select_related('opening_evidence').order_by('-number').first())
                if latest and latest.number > 1:
                    evidence = getattr(latest, 'opening_evidence', None)
                    if not evidence or evidence.state != 'OBSERVED':
                        # Keep the pending delivery recoverable until a real
                        # activation, rather than sending or marking skipped.
                        return 'waiting:round_activation'
        reason = eligibility_error(order)
        if record.sequence != order.reject_count:
            reason = '订单已进入新的派单轮次'
        if reason:
            # Missing historical price is repairable; a cancelled/assigned
            # order is instead permanently skipped for this dispatch attempt.
            record.status = 'FAILED' if reason == MISSING_ORIGINAL_PRICE else 'SKIPPED'
            record.last_error = reason
            record.save(update_fields=['status', 'last_error'])
            return 'failed:missing_original_price' if reason == MISSING_ORIGINAL_PRICE else f'skip:status={order.status}'
        if record.content_contract_version != _contract_version():
            record.status, record.last_error = 'FAILED', '旧版群发内容需要重新预览后重试'
            record.save(update_fields=['status', 'last_error'])
            return 'failed:outdated_content'
        if _pool_group_enabled():
            from .grab_group_dispatch import delivery_timing
            due, deadline = delivery_timing(order)
            from .grab_group_dispatch import current_time, deadline_reached
            now = current_time()
            if deadline_reached(deadline):
                record.status, record.last_error = 'SKIPPED', '订单接单时间已过期'
                record.save(update_fields=['status', 'last_error'])
                return f'skip:status={order.status}'
            if now < due:
                record.pool_delivery_due_at, record.pool_recovery_lease_until = due, None
                record.save(update_fields=['pool_delivery_due_at', 'pool_recovery_lease_until'])
                transaction.on_commit(lambda: send_task_safely(channel, record.pk, eta=due))
                return 'deferred'
        ready, target, _ = channel_config(channel, order)
        wecom_config = None
        if channel == 'WECOM':
            from integrations.wecom_config import get_wecom_config
            from stores.license_benefits import check_new_benefits
            wecom_config = get_wecom_config()
            ready = bool(wecom_config.ready)
            target = hashlib.sha256(wecom_config.webhook.encode()).hexdigest() if ready else ''
            decision, _ = check_new_benefits('CONTENT_OPERATIONS')
            if decision != 'ALLOW':
                record.status = 'FAILED'
                record.last_error = ('企微派单授权暂不可核验，请核对后重试' if decision == 'UNKNOWN'
                                     else '企微派单授权不可用，请核对后重试')
                record.save(update_fields=['status', 'last_error'])
                return 'failed:license'
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
        record.pool_recovery_lease_until = None
        record.pool_enqueue_failed = False
        # New WeCom records already carry the checked text. Only legacy rows
        # without a text snapshot need a fallback; KOOK keeps its card snapshot.
        if (channel == 'WECOM' and not record.content) or (channel == 'KOOK' and not record.card_content):
            record.content = build_dispatch_text(order)
        record.save(update_fields=['status', 'attempts', 'content', 'pool_recovery_lease_until', 'pool_enqueue_failed'])
    try:
        message_id = ''
        if channel == 'KOOK':
            result = KookClient(token=kook_config.token).send_channel_message(
                channel_id=record.channel_id, content=record.card_content or build_order_card(order, record.trigger, config=kook_config, route=route),
                message_type=10, nonce=f'xa-order-{order.pk}-{record.sequence}',
            )
            message_id = result.message_id
        else:
            WecomClient(webhook=wecom_config.webhook).send_text(record.content)
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
