"""Tencent SMS transport. No test codes, secrets or phone numbers are returned by APIs."""
import hashlib
import hmac
import json
import time
import urllib.request
from datetime import datetime, timezone
from django.conf import settings


class SmsUnavailable(ValueError):
    pass


def send_verification_code(phone, code):
    secret_id = getattr(settings, 'SMS_SECRET_ID', '')
    secret_key = getattr(settings, 'SMS_SECRET_KEY', '')
    app_id = getattr(settings, 'SMS_APP_ID', '')
    template_id = getattr(settings, 'SMS_TEMPLATE_ID', '')
    sign_name = getattr(settings, 'SMS_SIGN_NAME', '')
    if not all((secret_id, secret_key, app_id, template_id, sign_name)):
        raise SmsUnavailable('短信验证服务尚未配置，请联系平台客服')
    host, service = 'sms.tencentcloudapi.com', 'sms'
    timestamp = int(time.time())
    date = datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%d')
    payload = json.dumps({'PhoneNumberSet': ['+86' + phone], 'SmsSdkAppId': app_id,
        'SignName': sign_name, 'TemplateId': template_id,
        'TemplateParamSet': [code, '5']}, separators=(',', ':'), ensure_ascii=False).encode()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    canonical_headers = 'content-type:application/json; charset=utf-8\nhost:' + host + '\n'
    signed_headers = 'content-type;host'
    canonical_request = 'POST\n/\n\n' + canonical_headers + '\n' + signed_headers + '\n' + sha(payload)
    scope = date + '/sms/tc3_request'
    string_to_sign = 'TC3-HMAC-SHA256\n' + str(timestamp) + '\n' + scope + '\n' + sha(canonical_request.encode())
    sign = lambda key, value: hmac.new(key, value.encode(), hashlib.sha256).digest()
    signing_key = sign(sign(sign(('TC3' + secret_key).encode(), date), service), 'tc3_request')
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {'Content-Type': 'application/json; charset=utf-8', 'Host': host,
        'Authorization': f'TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders={signed_headers}, Signature={signature}',
        'X-TC-Action': 'SendSms', 'X-TC-Version': '2021-01-11', 'X-TC-Timestamp': str(timestamp),
        'X-TC-Region': getattr(settings, 'SMS_REGION', 'ap-guangzhou')}
    try:
        with urllib.request.urlopen(urllib.request.Request('https://' + host, data=payload, headers=headers), timeout=8) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise SmsUnavailable('短信服务暂时不可用，请稍后重试')
        result = json.loads(raw.decode())
        body = result.get('Response') if isinstance(result, dict) else None
        statuses = body.get('SendStatusSet') if isinstance(body, dict) and 'Error' not in body else None
        if (not isinstance(statuses, list) or len(statuses) != 1
                or not isinstance(statuses[0], dict) or statuses[0].get('Code') != 'Ok'
                or statuses[0].get('PhoneNumber') != '+86' + phone):
            raise SmsUnavailable('短信发送结果未确认，请稍后重试或联系平台客服')
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        if isinstance(error, SmsUnavailable):
            raise
        raise SmsUnavailable('短信服务暂时不可用，请稍后重试') from None
