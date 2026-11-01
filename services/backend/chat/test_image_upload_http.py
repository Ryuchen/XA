"""Check chat image validation through the real HTTP and storage path."""
from io import BytesIO
import json
import struct
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from django.test import LiveServerTestCase, override_settings
from PIL import Image

from chat.models import ChatMessage
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens


def png_bytes():
    stream = BytesIO()
    Image.new('RGB', (2, 2), (19, 132, 140)).save(stream, format='PNG')
    return stream.getvalue()


def broken_png_crc():
    data = bytearray(png_bytes())
    offset = 8
    while offset < len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        if data[offset + 4:offset + 8] == b'IDAT':
            data[offset + 8 + length] ^= 1
            return bytes(data)
        offset += length + 12
    raise AssertionError('test PNG has no IDAT chunk')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ChatImageUploadHTTPTest(LiveServerTestCase):
    def test_corrupt_image_is_rejected_and_valid_image_can_be_sent(self):
        account = ClubAccount.objects.create_account(username='isolated-image-boss', account_type='BOSS')
        token, _ = issue_account_tokens(account)

        def send_image(image):
            boundary = 'isolated' + uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="proof.png"\r\n'
                    'Content-Type: image/png\r\n\r\n').encode() + image + f'\r\n--{boundary}--\r\n'.encode()
            request = Request(self.live_server_url + '/api/chat/send/', data=body, method='POST',
                              headers={'Authorization': 'Bearer ' + token,
                                       'Content-Type': 'multipart/form-data; boundary=' + boundary})
            try:
                with urlopen(request, timeout=10) as response:
                    return response.status, json.load(response)
            except HTTPError as error:
                return error.code, None

        with TemporaryDirectory(prefix='xa-chat-image-') as media_root, override_settings(MEDIA_ROOT=media_root):
            status, result = send_image(broken_png_crc())
            self.assertEqual(status, 200)
            self.assertEqual(result['code'], 400)
            self.assertEqual(result['msg'], '图片内容无效')
            self.assertEqual(ChatMessage.objects.count(), 0)

            status, result = send_image(png_bytes())
            self.assertEqual(status, 200)
            self.assertEqual(result['code'], 0, result)
            self.assertEqual(result['data']['content_type'], 'IMAGE')
            self.assertEqual(ChatMessage.objects.count(), 1)
