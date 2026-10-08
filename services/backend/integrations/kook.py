"""KOOK HTTP API v3 的最小客户端。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from django.conf import settings


class KookError(Exception):
    """KOOK 请求或业务响应失败。"""


class KookConfigurationError(KookError):
    """KOOK 配置不完整，不应重试。"""


class KookDeliveryUnknown(KookError):
    """KOOK nonce 仅回传，不提供去重；未知结果不可自动重试。"""


@dataclass(frozen=True)
class KookMessageResult:
    message_id: str
    timestamp: int | None = None


class KookClient:
    def __init__(self, *, token: str | None = None, base_url: str | None = None,
                 timeout: float | None = None):
        self.token = token if token is not None else settings.KOOK_BOT_TOKEN
        self.base_url = (base_url or settings.KOOK_API_BASE_URL).rstrip('/')
        self.timeout = timeout if timeout is not None else settings.KOOK_REQUEST_TIMEOUT
        if not self.token:
            raise KookConfigurationError('KOOK_BOT_TOKEN 未配置')

    def get_channel(self, channel_id, *, guild_id=''):
        request = Request(f'{self.base_url}/channel/view?' + urlencode({'target_id': channel_id}),
            headers={'Authorization': f'Bot {self.token}', 'Accept-Language': 'zh-CN'}, method='GET')
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code == 403 and guild_id:
                return self._channel_from_list(channel_id, guild_id)
            raise KookError(f'KOOK 频道校验失败（HTTP {exc.code}），请检查 Bot Token 和查看频道权限') from None
        except (URLError, TimeoutError, OSError, UnicodeError, ValueError):
            raise KookError('KOOK 频道校验暂时无法完成，请稍后重试') from None
        if not isinstance(body, dict) or body.get('code') != 0 or not isinstance(body.get('data'), dict):
            raise KookError('KOOK 拒绝频道校验，请检查机器人权限')
        data = body['data']
        if data.get('type') != 1 or data.get('is_category'):
            raise KookError('请选择 KOOK 文字频道，不能使用服务器 ID、语音频道或分组 ID')
        return {'channel_id': str(data.get('id') or channel_id),
                'guild_id': str(data.get('guild_id') or ''), 'name': str(data.get('name') or '')[:100],
                'verification': 'detail'}

    def _channel_from_list(self, channel_id, guild_id):
        """详情权限不足时，只用已授权的列表接口确认频道元数据，不推断发送权限。"""
        for page in range(1, 11):
            request = Request(f'{self.base_url}/channel/list?' + urlencode({
                'guild_id': guild_id, 'type': 1, 'page_size': 100, 'page': page}),
                headers={'Authorization': f'Bot {self.token}', 'Accept-Language': 'zh-CN'}, method='GET')
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode('utf-8'))
            except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError):
                raise KookError('KOOK 无法确认该服务器内的文字频道，请检查服务器 ID 和机器人权限') from None
            data = body.get('data', {}) if isinstance(body, dict) and body.get('code') == 0 else {}
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise KookError('KOOK 无法确认文字频道列表，请稍后重试')
            for row in data.get('items', []):
                if isinstance(row, dict) and str(row.get('id')) == channel_id and row.get('type') == 1 and not row.get('is_category'):
                    return {'channel_id': channel_id, 'guild_id': guild_id,
                        'name': str(row.get('name') or '')[:100], 'verification': 'channel_list'}
            meta = data.get('meta') or {}
            pages = meta.get('page_total', 1) if isinstance(meta, dict) else 1
            if page >= (pages if isinstance(pages, int) else 1):
                break
        raise KookError('指定服务器的可见文字频道中未找到此 ID，请核对服务器、频道及机器人权限')

    def send_channel_message(self, *, channel_id: str, content: str,
                             message_type: int = 10, nonce: str = '') -> KookMessageResult:
        """发送频道消息。message_type=10 表示 Card Message。"""
        if not channel_id:
            raise KookConfigurationError('KOOK_DISPATCH_CHANNEL_ID 未配置')
        payload = {
            'type': message_type,
            'target_id': channel_id,
            'content': content,
        }
        if nonce:
            payload['nonce'] = nonce
        request = Request(
            f'{self.base_url}/message/create',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': f'Bot {self.token}',
                'Content-Type': 'application/json; charset=utf-8',
                'Accept-Language': 'zh-CN',
            },
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise KookError(f'KOOK 拒绝请求（HTTP {exc.code}）') from None
            raise KookDeliveryUnknown('KOOK 响应异常，请核对频道消息') from None
        except (URLError, TimeoutError, OSError) as exc:
            raise KookDeliveryUnknown('KOOK 响应未确认，请核对频道消息') from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KookDeliveryUnknown('KOOK 返回了无法解析的响应') from None

        if not isinstance(body, dict) or not isinstance(body.get('code'), int):
            raise KookDeliveryUnknown('KOOK 响应无法确认')
        if body.get('code') != 0:
            raise KookError(f"KOOK 拒绝发送（错误码 {body.get('code')}）")
        data = body.get('data') or {}
        message_id = str(data.get('msg_id') or '')
        if not message_id:
            raise KookDeliveryUnknown('KOOK 响应缺少 msg_id，请核对频道消息')
        return KookMessageResult(message_id=message_id, timestamp=data.get('msg_timestamp'))
