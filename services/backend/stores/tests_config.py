from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from stores.conf import parse_store_integrations, settings, store_name
from stores.context import store_context
from stores.links import audition_url, route_query_url, store_url


class StoreConfigurationTest(SimpleTestCase):
    def setUp(self):
        self.store = SimpleNamespace(slug='xinan', name='南岸电竞', database_alias='store_b',
            boss_url='https://boss.example.org', provider_url='https://provider.example.org')

    @override_settings(KOOK_ENABLED=True, KOOK_BOT_TOKEN='original-recipient-token',
                       WECOM_DISPATCH_ENABLED=True, WECOM_DISPATCH_WEBHOOK='original-recipient-webhook',
                       STORE_INTEGRATIONS={})
    def test_new_store_does_not_inherit_original_message_recipients(self):
        self.assertTrue(settings.KOOK_ENABLED)
        with store_context(self.store):
            self.assertFalse(settings.KOOK_ENABLED)
            self.assertFalse(settings.WECOM_DISPATCH_ENABLED)
            self.assertEqual(settings.KOOK_BOT_TOKEN, '')
            self.assertEqual(settings.WECOM_DISPATCH_WEBHOOK, '')
            self.assertEqual(store_name(), '南岸电竞')

    @override_settings(WECHAT_H5_APPID='platform-wechat-app', WECHAT_MOCK_LOGIN=False,
        SMS_APP_ID='platform-sms-app', STORE_INTEGRATIONS={'xinan': {
        'KOOK_ENABLED': True, 'KOOK_BOT_TOKEN': 'xinan-recipient-token',
        'WECHAT_H5_APPID': 'xinan-wechat-app', 'WECHAT_MOCK_LOGIN': True,
        'SMS_APP_ID': 'xinan-sms-app',
    }})
    def test_explicit_server_configuration_resolves_per_store(self):
        with store_context(self.store):
            self.assertTrue(settings.KOOK_ENABLED)
            self.assertEqual(settings.KOOK_BOT_TOKEN, 'xinan-recipient-token')
            self.assertEqual(settings.WECHAT_H5_APPID, 'platform-wechat-app')
            self.assertFalse(settings.WECHAT_MOCK_LOGIN)
            self.assertEqual(settings.SMS_APP_ID, 'platform-sms-app')

    def test_generated_dispatch_template_uses_the_current_store_name_literally(self):
        from integrations.kook_config import default_template
        from orders.dispatch_templates import sample_values
        self.store.name = '南岸{二店}'
        with store_context(self.store):
            content = default_template().format(**sample_values())
            self.assertTrue(content.startswith('南岸{二店} · '))
            self.assertNotIn('声浪电竞', content)

    def test_configuration_rejects_ambiguous_booleans_and_non_integration_settings(self):
        for raw in ('[]', '{"xinan":{"KOOK_ENABLED":"false"}}',
                    '{"xinan":{"SECRET_KEY":"wrong"}}'):
            with self.subTest(raw=raw), self.assertRaises(ImproperlyConfigured):
                parse_store_integrations(raw)

    def test_share_links_preserve_hash_route_and_replace_foreign_store(self):
        with store_context(self.store):
            link = audition_url('https://boss.example.org/?store=other#/audition?source=staff',
                                'private-invitation-token', 'boss')
            parts = urlsplit(link)
            self.assertEqual(parse_qs(parts.query), {'store': ['xinan']})
            route, query = parts.fragment.split('?')
            self.assertEqual(route, '/audition')
            self.assertEqual(parse_qs(query), {'source': ['staff'],
                'token': ['private-invitation-token'], 'role': ['boss']})
            self.assertEqual(parse_qs(urlsplit(store_url('https://provider.example.org/#/orders')).query),
                             {'store': ['xinan']})
            target = urlsplit(route_query_url(store_url('https://admin.example.org/#/orders'), order_no='ORDER-1'))
            self.assertEqual(parse_qs(target.query), {'store': ['xinan']})
            self.assertEqual(target.fragment, '/orders?order_no=ORDER-1')

    @override_settings(AUDITION_LINK_BASE_URL='https://h5.example.com/audition')
    def test_real_store_entry_is_not_reported_as_placeholder(self):
        from console.serializers import AdminAuditionLinkSerializer
        with store_context(self.store):
            serializer = AdminAuditionLinkSerializer()
            self.assertFalse(serializer._placeholder('AUDITION_BOSS_LINK_BASE_URL'))
            self.assertEqual(urlsplit(serializer._build_url('ticket', 'provider')).hostname,
                             'provider.example.org')
