import json
from types import SimpleNamespace

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from django.db import DatabaseError
from rest_framework_simplejwt.exceptions import TokenError
from club_accounts.models import ClubAccount
from club_accounts.tokens import account_for_claims
from console.permissions import get_account_permissions, is_console_account


@database_sync_to_async
def current_socket_account(claims):
    if not claims or claims.get('exp', 0) <= timezone.now().timestamp():
        return None
    try:
        from stores.context import resolve_store
        # Long-lived sockets must observe store suspension immediately.
        store_slug = claims.get('store_slug')
        if not isinstance(store_slug, str) or not store_slug:
            return None
        resolve_store(store_slug, allow_cached=True)
        account = account_for_claims(claims)
    except (TokenError, ValueError, DatabaseError):
        return None
    if account.account_type == ClubAccount.AccountType.STAFF and not is_console_account(account):
        return None
    return account


@database_sync_to_async
def current_chat_payload(account, data, *, message=False):
    """Recheck live roles/ownership on every delivery, including open sockets."""
    from chat.models import ChatMessage, ChatSession
    from chat.serializers import ChatMessageSerializer, ChatSessionSerializer
    from common.private_media import may_read
    if (account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)
            and (type(data.get('recipient_business_cycle')) is not int
                 or data['recipient_business_cycle'] != account.business_cycle)):
        return None

    session = ChatSession.objects.filter(pk=data.get('session_id')).first()
    if session is None:
        return None
    if account.account_type == ClubAccount.AccountType.STAFF:
        if not is_console_account(account) or 'chat:view' not in get_account_permissions(account):
            return None
    elif session.account_id != account.pk:
        return None
    payload = {'session_id': session.pk, 'account_id': session.account_id}
    if message:
        item = ChatMessage.objects.select_related('session').filter(
            pk=data.get('message', {}).get('id'), session=session,
        ).first()
        if item is None or not may_read(account, item):
            return None
        payload['message'] = ChatMessageSerializer(
            item, context={'request': SimpleNamespace(account=account)},
        ).data
    else:
        if account.account_type == ClubAccount.AccountType.STAFF:
            from console.serializers import AdminChatSessionSerializer
            payload['session'] = AdminChatSessionSerializer(
                session, context={'request': SimpleNamespace(account=account)},
            ).data
        else:
            payload['session'] = ChatSessionSerializer(session).data
    return payload


@database_sync_to_async
def current_report_payload(account, data):
    from wallet.models import ProviderReport
    from .notifier import report_provider_account_id

    report = ProviderReport.objects.filter(pk=data.get('report_id')).first()
    if report is None:
        return None
    if account.account_type == ClubAccount.AccountType.STAFF:
        if not is_console_account(account) or 'report:view' not in get_account_permissions(account):
            return None
    else:
        if account.account_type != ClubAccount.AccountType.PROVIDER or report_provider_account_id(report) != account.pk:
            return None
        if report.order_id:
            from site_messages.order_cycle import recipient_cycle
            cycle = data.get('recipient_business_cycle')
            if (type(cycle) is not int or cycle != account.business_cycle
                    or not account.license_membership_active
                    or recipient_cycle(report.order, account.pk) != cycle):
                return None
    return {'report_id': report.pk, 'order_id': report.order_id, 'status': report.status}


@database_sync_to_async
def current_wallet_payload(account, data):
    from wallet.models import Wallet
    from wallet.notifier import owner_account_id

    if not isinstance(data, dict):
        return None
    wallet_id = data.get('wallet_id')
    if type(wallet_id) is int and wallet_id > 0:
        wallet = Wallet.objects.filter(pk=wallet_id).first()
    else:
        account_id = data.get('account_id')
        wallet = Wallet.objects.filter(account_id=account_id).first() if type(account_id) is int and account_id > 0 else None
    if wallet is None:
        return None
    owner_id = owner_account_id(wallet)
    if account.account_type == ClubAccount.AccountType.STAFF:
        if not is_console_account(account) or not {'wallet:view', 'transaction:view'}.intersection(get_account_permissions(account)):
            return None
    elif account.account_type not in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER) or owner_id != account.pk:
        return None
    return {'account_id': owner_id, 'wallet_id': wallet.pk}


@database_sync_to_async
def current_reservation_payload(account, data):
    from .models import Reservation
    reservation_id = data.get('reservation_id') if isinstance(data, dict) else None
    reservation = Reservation.objects.filter(pk=reservation_id).first() if type(reservation_id) is int and reservation_id > 0 else None
    if reservation is None:
        return None
    if account.account_type == ClubAccount.AccountType.STAFF:
        if not is_console_account(account) or 'order:view' not in get_account_permissions(account):
            return None
    elif account.pk != reservation.customer_account_id:
        from .reservation_views import _provider_reservations
        if (account.account_type != ClubAccount.AccountType.PROVIDER
                or not Reservation.objects.filter(pk=reservation.pk).filter(_provider_reservations(account)).exists()):
            return None
    return {'reservation_id': reservation.pk}


@database_sync_to_async
def current_room_message_payload(account, data):
    from .models import Order, OrderRoom
    from .rooms import message_role
    order_id = data.get('order_id') if isinstance(data, dict) else None
    if type(order_id) is not int or not 0 < order_id <= 9223372036854775807:
        return None
    order = Order.objects.filter(pk=order_id).first()
    if not order or not OrderRoom.objects.filter(order=order).exists():
        return None
    role = message_role(order, account)
    return {'order_id': order.pk} if role in ('BOSS', 'PROVIDER', 'STAFF') else None


@database_sync_to_async
def current_withdraw_payload(account, data):
    from wallet.models import WithdrawRequest
    from wallet.notifier import owner_account_id
    from django.conf import settings

    if not isinstance(data, dict):
        return None
    withdraw_id = data.get('withdraw_id')
    withdraw = WithdrawRequest.objects.filter(pk=withdraw_id).first() if type(withdraw_id) is int and withdraw_id > 0 else None
    if withdraw is None:
        return None
    owner_id = owner_account_id(withdraw)
    if account.account_type == ClubAccount.AccountType.STAFF:
        if not is_console_account(account) or 'withdraw:view' not in get_account_permissions(account):
            return None
    elif account.account_type != ClubAccount.AccountType.PROVIDER or owner_id != account.pk:
        return None
    elif (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            and (type(withdraw.account_business_cycle) is not int
                 or withdraw.account_business_cycle != account.business_cycle
                 or not account.license_membership_active)):
        return None
    return {'withdraw_id': withdraw.pk, 'account_id': owner_id, 'status': withdraw.status}


class OrderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.closing = False
        account = await current_socket_account(self.scope.get('account_auth'))
        if account is None:
            await self.close()
            return

        self.account = account
        self.account_group = f'account_{account.id}'
        self.operator_group = 'operators'
        self.reviewer_groups = set()

        await self.channel_layer.group_add(self.account_group, self.channel_name)
        if account.account_type == ClubAccount.AccountType.STAFF:
            await self.channel_layer.group_add(self.operator_group, self.channel_name)
            await self.sync_reviewer_groups(account)
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            await self.channel_layer.group_add('providers', self.channel_name)
        if account.account_type == ClubAccount.AccountType.BOSS:
            await self.channel_layer.group_add('club_bosses', self.channel_name)

        await self.accept()

    async def sync_reviewer_groups(self, account):
        """Keep an open staff connection useful after its role changes."""
        permissions = await database_sync_to_async(get_account_permissions)(account)
        desired = {group for permission, group in (
            ('report:view', 'report_reviewers'), ('withdraw:view', 'withdraw_reviewers'),
            ('evaluation:view', 'evaluation_reviewers'),
            ('checkin:view', 'checkin_reviewers'),
        ) if permission in permissions}
        if {'wallet:view', 'transaction:view'}.intersection(permissions):
            desired.add('wallet_reviewers')
        for group in desired - self.reviewer_groups:
            await self.channel_layer.group_add(group, self.channel_name)
        for group in self.reviewer_groups - desired:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.reviewer_groups = desired

    async def disconnect(self, close_code):
        # 匿名连接在 connect 中被拒后未设置分组，跳过清理避免 AttributeError
        if not hasattr(self, 'account_group'):
            return
        await self.channel_layer.group_discard(self.account_group, self.channel_name)
        await self.channel_layer.group_discard(self.operator_group, self.channel_name)
        await self.channel_layer.group_discard('providers', self.channel_name)
        await self.channel_layer.group_discard('club_bosses', self.channel_name)
        await self.channel_layer.group_discard('report_reviewers', self.channel_name)
        await self.channel_layer.group_discard('wallet_reviewers', self.channel_name)
        await self.channel_layer.group_discard('withdraw_reviewers', self.channel_name)
        await self.channel_layer.group_discard('evaluation_reviewers', self.channel_name)
        await self.channel_layer.group_discard('checkin_reviewers', self.channel_name)

    async def club_feed_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.BOSS and account.can_view:
            await self.send(text_data=json.dumps({'type': 'club_feed_update', 'data': {}}))

    async def vip_config_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.BOSS and account.can_view:
            await self.send(text_data=json.dumps({'type': 'vip_config_update', 'data': {}}))

    async def provider_tags_update(self, event):
        account = await self.active_account()
        provider_id = (event.get('data') or {}).get('provider_id')
        if (account is not None and type(provider_id) is int and provider_id > 0 and
            ((account.account_type == ClubAccount.AccountType.BOSS and account.can_view) or
             (account.account_type == ClubAccount.AccountType.PROVIDER and account.pk == provider_id))):
            await self.send(text_data=json.dumps({'type': 'provider_tags_update', 'data': {'provider_id': provider_id}}))

    async def checkin_config_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.BOSS and account.can_view:
            await self.send(text_data=json.dumps({'type': 'checkin_config_update', 'data': {}}))

    async def coupon_catalog_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.BOSS and account.can_view:
            await self.send(text_data=json.dumps({'type': 'coupon_catalog_update', 'data': {}}))

    async def reward_catalog_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER) and account.can_view:
            await self.send(text_data=json.dumps({'type': 'reward_catalog_update', 'data': {}}))

    async def withdraw_config_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.PROVIDER and account.can_view:
            await self.send(text_data=json.dumps({'type':'withdraw_config_update','data':{}}))

    async def withdraw_account_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.PROVIDER and (event.get('data') or {}).get('account_id') == account.pk:
            await self.send(text_data=json.dumps({'type':'withdraw_account_update','data':{}}))

    async def coupon_claim_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.STAFF:
            permissions = await database_sync_to_async(get_account_permissions)(account)
            if 'coupon:view' in permissions:
                await self.send(text_data=json.dumps({'type': 'coupon_claim_update', 'data': event['data']}))

    async def checkin_progress_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.STAFF:
            permissions = await database_sync_to_async(get_account_permissions)(account)
            if 'checkin:view' in permissions:
                await self.send(text_data=json.dumps({'type': 'checkin_progress_update', 'data': event['data']}))

    async def active_account(self):
        if self.closing:
            return None
        account = await current_socket_account(self.scope.get('account_auth'))
        if account is None:
            self.closing = True
            await self.close(code=4401)
        return account

    async def receive(self, text_data=None, bytes_data=None):
        if self.closing:
            return
        if bytes_data is not None or (text_data and len(text_data) > 1024):
            self.closing = True
            await self.close(code=1003 if bytes_data is not None else 1009)
            return
        try:
            data = json.loads(text_data)
            if isinstance(data, dict) and data.get('type') == 'ping':
                account = await self.active_account()
                if account is not None:
                    if account.account_type == ClubAccount.AccountType.STAFF:
                        await self.sync_reviewer_groups(account)
                    await self.send(text_data=json.dumps({'type': 'pong'}))
        except (json.JSONDecodeError, TypeError):
            pass

    async def order_status_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        if account.account_type == ClubAccount.AccountType.STAFF:
            permissions = await database_sync_to_async(get_account_permissions)(account)
            if 'order:view' not in permissions:
                if 'dashboard:view' in permissions:
                    await self.send(text_data=json.dumps({'type': 'dashboard_order_update', 'data': {}}))
                if 'wallet:view' in permissions:
                    # Refresh wallet aggregates without disclosing order data to
                    # staff who are not allowed to inspect individual orders.
                    await self.send(text_data=json.dumps({'type': 'platform_funds_update', 'data': {}}))
                return
        else:
            data = event.get('data') or {}
            # The public provider group carries only an empty refresh signal.
            # Personal order data must belong to this account's original cycle.
            if not (account.account_type == ClubAccount.AccountType.PROVIDER and not data
                    and event.get('recipient_account_id') is None):
                if (event.get('recipient_account_id') != account.pk
                        or type(event.get('recipient_business_cycle')) is not int
                        or event['recipient_business_cycle'] != account.business_cycle
                        or not account.license_membership_active):
                    return
        await self.send(text_data=json.dumps({
            'type': 'order_status_update',
            'data': event['data'],
        }))

    async def room_message_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_room_message_payload(account, event.get('data'))
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'room_message_update', 'data': payload}))

    async def report_status_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_report_payload(account, event['data'])
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'report_status_update', 'data': payload}))

    async def evaluation_status_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.STAFF:
            permissions = await database_sync_to_async(get_account_permissions)(account)
            if 'evaluation:view' in permissions:
                await self.send(text_data=json.dumps({'type': 'evaluation_status_update', 'data': {}}))

    async def claim_queue_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.STAFF:
            permissions = await database_sync_to_async(get_account_permissions)(account)
            if 'engagement:fulfill' in permissions:
                await self.send(text_data=json.dumps({'type': 'claim_queue_update', 'data': {}}))

    async def site_message_update(self, event):
        account = await self.active_account()
        data = event.get('data') or {}
        from django.conf import settings
        guarded = (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                   or (account is not None and account.account_type in
                       (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)))
        if (guarded
                and (account is None or type(data.get('business_cycle')) is not int
                     or data['business_cycle'] != account.business_cycle)):
            return
        if account is not None and data.get('account_id') == account.pk:
            payload = {}
            if account.account_type == ClubAccount.AccountType.PROVIDER and data.get('assignment_message_id'):
                payload['assignment_message_id'] = data['assignment_message_id']
            await self.send(text_data=json.dumps({'type': 'site_message_update', 'data': payload}))

    async def member_points_update(self, event):
        account = await self.active_account()
        data = event.get('data') or {}
        if account is not None and account.account_type in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER) and data.get('account_id') == account.pk:
            await self.send(text_data=json.dumps({'type': 'member_points_update', 'data': {}}))

    async def reservation_status_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_reservation_payload(account, event.get('data'))
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'reservation_status_update', 'data': payload}))

    async def wallet_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_wallet_payload(account, event['data'])
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'wallet_update', 'data': payload}))

    async def withdraw_status_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_withdraw_payload(account, event['data'])
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'withdraw_status_update', 'data': payload}))

    async def chat_message(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_chat_payload(account, event['data'], message=True)
        if payload is None:
            return
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'data': payload,
        }))

    async def chat_session_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_chat_payload(account, event['data'])
        if payload is None:
            return
        await self.send(text_data=json.dumps({
            'type': 'chat_session_update',
            'data': payload,
        }))
