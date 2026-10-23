import json
from types import SimpleNamespace

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from club_accounts.models import ClubAccount
from club_accounts.tokens import account_for_claims
from console.permissions import get_account_permissions, is_console_account


@database_sync_to_async
def current_socket_account(claims):
    if not claims or claims.get('exp', 0) <= timezone.now().timestamp():
        return None
    try:
        account = account_for_claims(claims)
    except TokenError:
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
    elif account.account_type != ClubAccount.AccountType.PROVIDER or report_provider_account_id(report) != account.pk:
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
def current_withdraw_payload(account, data):
    from wallet.models import WithdrawRequest
    from wallet.notifier import owner_account_id

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

        await self.channel_layer.group_add(self.account_group, self.channel_name)
        if account.account_type == ClubAccount.AccountType.STAFF:
            await self.channel_layer.group_add(self.operator_group, self.channel_name)
            permissions = await database_sync_to_async(get_account_permissions)(account)
            for permission, group in (('report:view', 'report_reviewers'), ('withdraw:view', 'withdraw_reviewers')):
                if permission in permissions:
                    await self.channel_layer.group_add(group, self.channel_name)
            if {'wallet:view', 'transaction:view'}.intersection(permissions):
                await self.channel_layer.group_add('wallet_reviewers', self.channel_name)
        if account.account_type == ClubAccount.AccountType.PROVIDER:
            await self.channel_layer.group_add('providers', self.channel_name)
        if account.account_type == ClubAccount.AccountType.BOSS:
            await self.channel_layer.group_add('club_bosses', self.channel_name)

        await self.accept()

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

    async def club_feed_update(self, event):
        account = await self.active_account()
        if account is not None and account.account_type == ClubAccount.AccountType.BOSS and account.can_view:
            await self.send(text_data=json.dumps({'type': 'club_feed_update', 'data': {}}))

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
            if isinstance(data, dict) and data.get('type') == 'ping' and await self.active_account():
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
                return
        await self.send(text_data=json.dumps({
            'type': 'order_status_update',
            'data': event['data'],
        }))

    async def report_status_update(self, event):
        account = await self.active_account()
        if account is None:
            return
        payload = await current_report_payload(account, event['data'])
        if payload is not None:
            await self.send(text_data=json.dumps({'type': 'report_status_update', 'data': payload}))

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
