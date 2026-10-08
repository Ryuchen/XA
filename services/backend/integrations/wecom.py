"""企业微信群机器人：固定官方 Webhook，发送 UTF-8 纯文本。"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings


class WecomError(Exception):
    pass


class WecomConfigurationError(WecomError):
    pass


class WecomDeliveryUnknown(WecomError):
    """请求可能已送达；没有上游幂等键，不自动重发。"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urlopen = build_opener(_NoRedirect()).open


def valid_webhook(url):
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        return bool(
            parts.scheme == 'https' and parts.netloc == 'qyapi.weixin.qq.com'
            and parts.path == '/cgi-bin/webhook/send' and not parts.fragment
            and set(query) == {'key'} and len(query['key']) == 1 and query['key'][0]
        )
    except (TypeError, ValueError):
        return False


class WecomClient:
    def __init__(self, webhook=None):
        self.webhook = webhook if webhook is not None else settings.WECOM_DISPATCH_WEBHOOK
        if not valid_webhook(self.webhook):
            raise WecomConfigurationError('请配置有效的企业微信群机器人 Webhook')

    def send_text(self, content):
        if not content or len(content.encode('utf-8')) > 2048:
            raise WecomConfigurationError('派单文案须为 1 至 2048 字节')
        request = Request(
            self.webhook,
            data=json.dumps({'msgtype': 'text', 'text': {'content': content}}, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST',
        )
        try:
            with urlopen(request, timeout=settings.WECOM_REQUEST_TIMEOUT) as response:
                body = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise WecomError(f'企微拒绝请求（HTTP {exc.code}）') from None
            raise WecomDeliveryUnknown('企微响应异常，请核对群消息后处理') from None
        except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            # 不回显异常 URL，避免将 Webhook key 泄漏到日志或前端。
            raise WecomDeliveryUnknown('企微响应未确认，请核对群消息后处理') from None
        if not isinstance(body, dict) or not isinstance(body.get('errcode'), int):
            raise WecomDeliveryUnknown('企微响应无法确认，请核对群消息后处理')
        if body['errcode'] != 0:
            raise WecomError(f"企微拒绝发送（错误码 {body['errcode']}）")
