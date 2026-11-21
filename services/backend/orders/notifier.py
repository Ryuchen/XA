from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
import logging
from types import SimpleNamespace
from copy import copy
from django.contrib.auth import get_user_model
from rest_framework.renderers import JSONRenderer

from .serializers import OrderSerializer


def report_provider_account_id(report):
    """Explicit ownership wins; historical user ids are never account ids."""
    if report.provider_account_id:
        return report.provider_account_id
    from club_accounts.models import LegacyAccountMap
    return LegacyAccountMap.objects.filter(
        legacy_user_id=report.provider_id,
    ).values_list('account_id', flat=True).first()


def notify_report_update(report, *, notify_owner=True):
    """Invalidate the owner's list and authorized reviewers after a commit."""
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        event = {'type': 'report_status_update', 'data': {
            'report_id': report.pk,
            'order_id': report.order_id,
            'status': report.status,
        }}
        groups = [('report_reviewers', event)]
        account_id = report_provider_account_id(report) if notify_owner else None
        if account_id and notify_owner:
            owner_event = event
            if report.order_id:
                from site_messages.order_cycle import recipient_cycle
                cycle = recipient_cycle(report.order, account_id)
                owner_event = ({'type': event['type'], 'data': {**event['data'],
                                'recipient_business_cycle': cycle}} if cycle else None)
            if owner_event is not None:
                groups.insert(0, (f'account_{account_id}', owner_event))
        for group, outgoing in groups:
            try:
                async_to_sync(channel_layer.group_send)(group, outgoing)
            except Exception:
                logging.getLogger(__name__).warning(
                    '报单实时通知失败: report_id=%s group=%s', report.pk, group, exc_info=True,
                )
    except Exception:
        logging.getLogger(__name__).warning('报单实时通知失败: report_id=%s', report.pk, exc_info=True)


def notify_order_update(order, *, refresh_providers=False):
    # 订单和账务已经提交，消息通道故障不能把成功操作伪装成失败。
    try:
        _notify_order_update(order, refresh_providers=refresh_providers)
    except Exception:
        logging.getLogger(__name__).warning('订单实时通知失败: order_id=%s', order.pk, exc_info=True)
    try:
        from chat.order_card_notices import schedule as schedule_chat_card_refresh
        schedule_chat_card_refresh(order.pk)
    except Exception:
        logging.getLogger(__name__).warning('咨询订单卡片刷新调度失败: order_id=%s', order.pk, exc_info=True)


def notify_evaluation_update():
    """Prompt evaluation reviewers to reload their current snapshot."""
    try:
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)('evaluation_reviewers', {'type': 'evaluation_status_update'})
    except Exception:
        logging.getLogger(__name__).warning('评价实时通知失败', exc_info=True)


def _notify_order_update(order, *, refresh_providers=False):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    # Method fields can contain datetime/Decimal values which DRF normally
    # converts during HTTP rendering. The channel transport and consumer also
    # need that conversion; otherwise a completed order's aftersale deadline
    # breaks Redis serialization or disconnects an already-open socket.
    def rendered(request=None):
        instance = order
        if request is not None:
            instance = copy(order)
            # Do not reuse another HTTP caller's requester-specific prefetch.
            instance.requester_assignments = [row for row in order.providers.all()
                if row.provider_account_id == request.account.pk or (
                    row.provider_account_id is None and request.legacy_user is not None
                    and row.provider_id == request.legacy_user.pk)]
        return json.loads(JSONRenderer().render(OrderSerializer(
            instance, context={'request': request},
        ).data))

    def send(group, data, *, recipient_id=None, recipient_cycle=None):
        try:
            event = {'type': 'order_status_update', 'data': data}
            if recipient_id is not None:
                event['recipient_account_id'] = recipient_id
                event['recipient_business_cycle'] = recipient_cycle
            async_to_sync(channel_layer.group_send)(
                group, event,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                '订单实时通知失败: order_id=%s group=%s', order.pk, group, exc_info=True,
            )

    from club_accounts.models import ClubAccount, LegacyAccountMap
    # A private event is the same role-scoped representation as HTTP, never a
    # shared team aggregate. Explicit account relations win over legacy maps.
    owners = [(order.customer_account_id, order.customer_id),
              (order.provider_account_id, order.provider_id)]
    owners.extend(order.providers.values_list('provider_account_id', 'provider_id'))
    missing_users = {user_id for account_id, user_id in owners if not account_id and user_id}
    mapped = dict(LegacyAccountMap.objects.filter(
        legacy_user_id__in=missing_users,
    ).values_list('legacy_user_id', 'account_id'))
    personal_ids = {account_id or mapped.get(user_id) for account_id, user_id in owners}
    personal_ids.discard(None)
    accounts = ClubAccount.objects.filter(pk__in=personal_ids, is_active=True, can_login=True)
    legacy_ids = dict(LegacyAccountMap.objects.filter(
        account_id__in=personal_ids,
    ).values_list('account_id', 'legacy_user_id'))
    users = get_user_model().objects.in_bulk(legacy_ids.values())
    from site_messages.order_cycle import recipient_cycle
    for account in accounts:
        cycle = recipient_cycle(order, account.pk)
        if (type(cycle) is not int or cycle != account.business_cycle
                or not account.license_membership_active):
            continue
        if refresh_providers and account.account_type == ClubAccount.AccountType.PROVIDER:
            # The client reloads the order. Rendering report permissions here
            # would take the financial order lock again after commit.
            send(f'account_{account.pk}', {'id': order.pk, 'status': order.status},
                 recipient_id=account.pk, recipient_cycle=cycle)
            continue
        request = SimpleNamespace(
            account=account, legacy_user=users.get(legacy_ids.get(account.pk)),
            # There is no HTTP Host in a queued notification. Preserve the
            # existing relative URL contract (MEDIA_BASE_URL still takes priority).
            build_absolute_uri=lambda url: url,
        )
        send(f'account_{account.pk}', rendered(request), recipient_id=account.pk, recipient_cycle=cycle)

    application_cycles = {}
    for applicant_id, applicant_cycle, partner_id, partner_cycle in order.applications.values_list(
            'applicant_id', 'applicant_business_cycle', 'partner_id', 'partner_business_cycle'):
        for account_id, cycle in ((applicant_id, applicant_cycle), (partner_id, partner_cycle)):
            if account_id and account_id not in personal_ids:
                application_cycles.setdefault(account_id, set()).add(cycle)
    for account in ClubAccount.objects.filter(pk__in=application_cycles, is_active=True, can_login=True,
                                               license_membership_active=True):
        cycles = application_cycles[account.pk]
        if len(cycles) == 1 and account.business_cycle in cycles:
            send(f'account_{account.pk}', {'id': order.pk, 'status': order.status},
                 recipient_id=account.pk, recipient_cycle=account.business_cycle)

    # Operators are independently gated by current order:view in the consumer.
    send('operators', rendered())

    # 抢单池相关状态广播给全部陪玩：PENDING 加入/退回，GRABBED/CANCELLED 移除。
    if order.dispatch_route == order.DispatchRoute.PUBLIC and order.payment_status != order.PaymentStatus.UNPAID and order.status in (order.Status.PENDING, order.Status.GRABBED, order.Status.CANCELLED):
        # 公共组只提供无订单编号的刷新信号；通行证尚未到可见时间的陪玩
        # 不能通过 WebSocket 提前获知具体订单。列表接口仍按本人资格过滤。
        send('providers', {})
