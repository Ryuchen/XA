import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from stores.context import store_context
from users.h5_login import ConsoleH5StartView, H5LoginStartView
from users.web_login import WebLoginStartView


class WechatUnconfiguredTests(SimpleTestCase):
    def test_missing_credentials_do_not_enable_mock_by_default(self):
        env = os.environ.copy()
        for name in ('WECHAT_MOCK_LOGIN', 'WECHAT_APPID', 'WECHAT_SECRET'):
            env.pop(name, None)
        env['DJANGO_DEBUG'] = 'true'
        result = subprocess.run([sys.executable, '-c',
            'from config.settings import WECHAT_MOCK_LOGIN; print(WECHAT_MOCK_LOGIN)'],
            env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'False')

    @override_settings(WECHAT_H5_APPID='', WECHAT_H5_SECRET='',
                       WECHAT_MINIAPP_APPID='', WECHAT_MINIAPP_SECRET='', WECHAT_MOCK_LOGIN=False,
                       STORE_INTEGRATIONS={'xinan': {'WECHAT_H5_APPID': 'tenant-id',
                           'WECHAT_H5_SECRET': 'tenant-secret', 'WECHAT_MOCK_LOGIN': True}})
    def test_all_three_login_entries_report_unconfigured_without_external_calls(self):
        store = SimpleNamespace(slug='xinan', database_alias='store_b')
        with store_context(store), patch('urllib.request.urlopen') as network:
            for audience in ('boss', 'provider'):
                request = SimpleNamespace(data={'audience': audience}, META={})
                for view in (H5LoginStartView(), WebLoginStartView()):
                    response = view.post(request)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn(response.data['errorCode'], ('WECHAT_H5_NOT_CONFIGURED', 'WECHAT_NOT_CONFIGURED'))
            response = ConsoleH5StartView().post(SimpleNamespace(data={}, META={}))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data['errorCode'], 'WECHAT_H5_NOT_CONFIGURED')
            network.assert_not_called()
