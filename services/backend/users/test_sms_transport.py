import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from stores.context import store_context
from users.sms_transport import SmsUnavailable, send_verification_code


@override_settings(SMS_SECRET_ID='platform-id', SMS_SECRET_KEY='platform-key',
                   SMS_APP_ID='platform-app', SMS_TEMPLATE_ID='template', SMS_SIGN_NAME='sign',
                   STORE_INTEGRATIONS={'other': {'SMS_APP_ID': 'store-app', 'SMS_SECRET_ID': 'store-id'}})
class SmsTransportTests(SimpleTestCase):
    @contextmanager
    def response(self, raw):
        class Response:
            def read(self, size):
                return raw[:size]
        yield Response()

    def test_platform_config_shared_and_receipt_matches_recipient(self):
        body = {'Response': {'SendStatusSet': [{'Code': 'Ok', 'PhoneNumber': '+8613800001111'}]}}
        with store_context(SimpleNamespace(slug='other', database_alias='other')):
            with patch('urllib.request.urlopen', return_value=self.response(json.dumps(body).encode())) as urlopen:
                send_verification_code('13800001111', '123456')
        request = urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data)['SmsSdkAppId'], 'platform-app')
        self.assertIn('Credential=platform-id/', request.get_header('Authorization'))

    def test_provider_error_and_malformed_receipts_fail_closed(self):
        bodies = [None, [], {}, {'Response': None}, {'Response': {'Error': {}}},
                  {'Response': {'SendStatusSet': None}}, {'Response': {'SendStatusSet': [None]}},
                  {'Response': {'SendStatusSet': [{'Code': 'Ok', 'PhoneNumber': '+8613800002222'}]}},
                  {'Response': {'SendStatusSet': [{'Code': 'Failed', 'PhoneNumber': '+8613800001111'}]}}]
        for body in bodies:
            with self.subTest(body=body), patch('urllib.request.urlopen', return_value=self.response(json.dumps(body).encode())):
                with self.assertRaises(SmsUnavailable):
                    send_verification_code('13800001111', '123456')

    def test_invalid_encoding_oversized_and_network_failure_are_unavailable(self):
        for raw in [b'\xff', b' ' * 65537, b'{']:
            with self.subTest(raw=raw[:5]), patch('urllib.request.urlopen', return_value=self.response(raw)):
                with self.assertRaises(SmsUnavailable):
                    send_verification_code('13800001111', '123456')
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            with self.assertRaises(SmsUnavailable):
                send_verification_code('13800001111', '123456')

    @override_settings(SMS_SECRET_ID='')
    def test_unconfigured_service_does_not_send(self):
        with patch('urllib.request.urlopen') as urlopen:
            with self.assertRaises(SmsUnavailable):
                send_verification_code('13800001111', '123456')
            urlopen.assert_not_called()
