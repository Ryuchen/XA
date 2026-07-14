import json

from channels.generic.websocket import AsyncWebsocketConsumer


class OrderConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope['user']
        if user.is_anonymous:
            await self.close()
            return

        self.user = user
        self.user_group = f'user_{user.id}'
        self.operator_group = 'operators'

        await self.channel_layer.group_add(self.user_group, self.channel_name)
        if user.role == user.Role.OPERATOR:
            await self.channel_layer.group_add(self.operator_group, self.channel_name)
        if user.role == user.Role.PROVIDER:
            await self.channel_layer.group_add('providers', self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        # 匿名连接在 connect 中被拒后未设置分组，跳过清理避免 AttributeError
        if not hasattr(self, 'user_group'):
            return
        await self.channel_layer.group_discard(self.user_group, self.channel_name)
        await self.channel_layer.group_discard(self.operator_group, self.channel_name)
        await self.channel_layer.group_discard('providers', self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
        except json.JSONDecodeError:
            pass

    async def order_status_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'order_status_update',
            'data': event['data'],
        }))

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'data': event['data'],
        }))

    async def chat_session_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_session_update',
            'data': event['data'],
        }))
