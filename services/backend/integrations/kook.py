"""KOOK HTTP API v3 的最小客户端。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class KookError(Exception):
    """KOOK 请求或业务响应失败。"""


class KookConfigurationError(KookError):
    """KOOK 配置不完整，不应重试。"""


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
            detail = exc.read().decode('utf-8', errors='replace')[:300]
            raise KookError(f'KOOK HTTP {exc.code}: {detail}') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise KookError(f'KOOK 网络请求失败: {exc}') from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KookError('KOOK 返回了无法解析的响应') from exc

        if body.get('code') != 0:
            raise KookError(f"KOOK API 错误 {body.get('code')}: {body.get('message', '未知错误')}")
        data = body.get('data') or {}
        message_id = str(data.get('msg_id') or '')
        if not message_id:
            raise KookError('KOOK 响应缺少 msg_id')
        return KookMessageResult(message_id=message_id, timestamp=data.get('msg_timestamp'))
