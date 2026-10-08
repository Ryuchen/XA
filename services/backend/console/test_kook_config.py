import json
from unittest.mock import MagicMock, patch
from uuid import uuid4
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from integrations.kook import KookClient, KookDeliveryUnknown, KookError, KookMessageResult
from integrations.kook_config import DEFAULT_TEMPLATE, decrypt_token, encrypt_token, get_kook_config, route_for_order
from orders.dispatching import deliver_dispatch, dispatch_preview, queue_dispatch
from orders.kook_dispatch import enqueue_kook_dispatch
from orders.models import GameCategory, Gameplay, KookDispatchRecord, Order
from orders.tests.factories import make_console_user, make_order, make_service, make_user
from wallet.models import Transaction
from .models import AdminAuditLog, KookDispatchConfig, KookDispatchProbe


class TokenEncryptionTest(SimpleTestCase):
    def test_ciphertext_never_contains_token_and_key_rotation_can_use_fallback(self):
        ciphertext = encrypt_token('test-only-secret')
        self.assertNotIn('test-only-secret', ciphertext)
        self.assertEqual(decrypt_token(ciphertext), 'test-only-secret')
        with override_settings(SECRET_KEY='different', SECRET_KEY_FALLBACKS=[]):
            self.assertEqual(decrypt_token(ciphertext), '')
        with override_settings(SECRET_KEY='different', SECRET_KEY_FALLBACKS=['xa-test-only-secret-key-not-for-production-use']):
            self.assertEqual(decrypt_token(ciphertext), 'test-only-secret')

    @patch('integrations.kook.urlopen')
    def test_channel_check_uses_get_and_rejects_voice_or_category(self, network):
        response = MagicMock()
        network.return_value = response
        response.__enter__.return_value.read.return_value = json.dumps({'code': 0, 'data': {
            'id': '123', 'guild_id': '456', 'name': '派单', 'type': 1, 'is_category': False}}).encode()
        client = KookClient(token='test-only')
        self.assertEqual(client.get_channel('123')['guild_id'], '456')
        self.assertEqual(network.call_args.args[0].get_method(), 'GET')
        self.assertTrue(network.call_args.args[0].full_url.endswith('/channel/view?target_id=123'))
        response.__enter__.return_value.read.return_value = json.dumps({'code': 0, 'data': {'type': 2}}).encode()
        with self.assertRaisesMessage(KookError, '文字频道'):
            client.get_channel('123')

    @patch('integrations.kook.urlopen')
    def test_detail_permission_failure_can_confirm_metadata_from_authorized_guild_list(self, network):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'code': 0, 'data': {
            'items': [{'id': '123', 'name': '派单', 'type': 1}], 'meta': {'page_total': 1}}}).encode()
        network.side_effect = [HTTPError('https://www.kookapp.cn', 403, 'Forbidden', {}, None), response]
        result = KookClient(token='test-only').get_channel('123', guild_id='456')
        self.assertEqual(result['verification'], 'channel_list')
        self.assertEqual(result['channel_id'], '123')
        self.assertIn('/channel/list?', network.call_args.args[0].full_url)
        self.assertEqual(network.call_args.args[0].get_method(), 'GET')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, KOOK_BOT_TOKEN='')
class KookConfigTest(APITestCase):
    def setUp(self):
        self.staff = make_console_user(['dispatch_config:view', 'dispatch_config:edit', 'order:dispatch'])
        self.account = get_or_create_account_for_legacy_user(self.staff)
        membership = self.staff.admin_membership
        membership.account = self.account
        membership.save(update_fields=['account'])
        self.role = membership.roles.get()
        token, _ = issue_account_tokens(self.account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
        self.game = GameCategory.objects.create(name='测试游戏')
        self.gameplay = Gameplay.objects.create(name='测试玩法', code='kook-test')
        self.service = make_service(game_category=self.game, gameplay=self.gameplay, platform='PC', security_level='ANY')
        self.order = make_order(make_user(), service=self.service, amount=8000, provider_income=6400,
            remark='内部原价100元老板实付80元', game_region='微信区',
            gameplay_snapshot={'id': self.gameplay.pk, 'name': '测试玩法', 'platform': 'PC', 'price_unit': '小时'})
        self.url = '/api/admin/config/kook-dispatch/'

    def payload(self, **changes):
        data = {'revision': 0, 'enabled': True, 'bot_token': 'test-only-secret',
            'auto_new_orders': True, 'auto_rejected_orders': True,
            'provider_order_url': 'https://provider.xaesports.club', 'routes': [self.route()]}
        data.update(changes)
        return data

    def route(self, **changes):
        route = {'id': 'main', 'name': '测试派单', 'enabled': True, 'channel_id': '123456',
            'game_category_id': None, 'gameplay_id': None, 'platform': '',
            'template': DEFAULT_TEMPLATE, 'mention_role_ids': ['123']}
        route.update(changes)
        return route

    def save(self, **changes):
        response = self.client.put(self.url, self.payload(**changes), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['data']

    def test_save_encrypts_token_and_redacts_reads_and_audit(self):
        data = self.save()
        self.assertTrue(data['ready'])
        row = KookDispatchConfig.objects.get(pk=1)
        self.assertNotIn('test-only-secret', row.bot_token_ciphertext)
        self.assertEqual(decrypt_token(row.bot_token_ciphertext), 'test-only-secret')
        self.assertNotIn('test-only-secret', json.dumps(data, default=str))
        self.assertNotIn('bot_token_ciphertext', data)
        self.assertEqual(AdminAuditLog.objects.get(path=self.url).request_body['bot_token'], '***')
        self.assertNotIn('test-only-secret', repr(get_kook_config()))

    def test_blank_token_preserves_it_and_clear_requires_disabling(self):
        self.save()
        self.save(revision=1, bot_token='')
        self.assertEqual(get_kook_config().token, 'test-only-secret')
        response = self.client.put(self.url, self.payload(revision=2, bot_token='', clear_token=True), format='json')
        self.assertEqual(response.status_code, 400)
        self.save(revision=2, enabled=False, bot_token='', clear_token=True)
        self.assertFalse(get_kook_config().ready)
        self.assertEqual(get_kook_config().token, '')

    def test_stale_revision_cannot_overwrite_token_or_routes(self):
        self.save()
        response = self.client.put(self.url, self.payload(bot_token='stale-secret'), format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(get_kook_config().token, 'test-only-secret')

    def test_missing_token_rolls_back_first_save(self):
        response = self.client.put(self.url, self.payload(bot_token=''), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(KookDispatchConfig.objects.exists())

    def test_view_permission_cannot_edit_check_preview_or_send(self):
        self.role.permissions = ['dispatch_config:view']
        self.role.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.put(self.url, self.payload(), format='json').status_code, 403)
        for action in ['check', 'preview', 'test']:
            self.assertEqual(self.client.post(self.url + action + '/', {}, format='json').status_code, 403)
        self.role.permissions = ['order:dispatch']
        self.role.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_template_forbids_internal_fields_attributes_and_missing_payout(self):
        for template in ['{order_no} {amount} {provider_income}', '{order_no} {remark} {provider_income}',
                '{order_no} {provider_income.__class__}', '{order_no} {provider_income!r}',
                '{order_no} {provider_income:1000}', '{order_no}', '{order_no} {provider_income',
                '{order_no} {provider_income}' + '{gameplay_requirements}' * 5]:
            with self.subTest(template=template):
                response = self.client.put(self.url, self.payload(routes=[self.route(template=template)]), format='json')
                self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url + 'preview/', {'template': DEFAULT_TEMPLATE}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('TEST-KOOK-001', response.data['data']['content'])

    def test_route_and_link_validation(self):
        for changes in [{'channel_id': 'not-channel'}, {'mention_role_ids': ['all']}, {'game_category_id': 999999}]:
            self.assertEqual(self.client.put(self.url, self.payload(routes=[self.route(**changes)]), format='json').status_code, 400)
        for url in ['http://provider.xaesports.club', 'https://name:password@example.com']:
            self.assertEqual(self.client.put(self.url, self.payload(provider_order_url=url), format='json').status_code, 400)

    def test_order_routes_use_first_match_and_skip_disabled_or_mismatching_rules(self):
        routes = [self.route(id='disabled', enabled=False), self.route(id='mobile', platform='MOBILE'),
            self.route(id='game', game_category_id=self.game.pk, gameplay_id=self.gameplay.pk, platform='PC', channel_id='222222'),
            self.route(id='default')]
        self.save(routes=routes)
        self.assertEqual(route_for_order(get_kook_config(), self.order)['id'], 'game')
        self.order.gameplay_snapshot['id'] = -1
        self.assertEqual(route_for_order(get_kook_config(), self.order)['id'], 'default')

    @patch('orders.kook_dispatch._send_task_safely')
    @patch('integrations.kook.KookClient.send_channel_message', return_value=KookMessageResult('msg-1'))
    def test_actual_queued_card_matches_preview_and_retains_template_snapshot(self, send, enqueue):
        self.save(routes=[self.route(template='指定频道\n{order_no}\n单钱 {provider_income} 币 / ¥{provider_income_yuan}')])
        preview = dispatch_preview(self.order)['channels'][0]
        self.assertEqual(preview['target_id'], '123456')
        self.assertIn('640 币 / ¥64.00', preview['content'])
        with self.captureOnCommitCallbacks(execute=True):
            record = queue_dispatch(self.order, 'KOOK')
            again = queue_dispatch(self.order, 'KOOK')
        self.assertEqual(record.pk, again.pk)
        enqueue.assert_called_once()
        self.assertEqual(record.content, preview['content'])
        self.save(revision=1, routes=[self.route(template='新文案 {order_no} {provider_income}')])
        self.assertEqual(deliver_dispatch('KOOK', record.pk), 'sent:msg-1')
        self.assertIn('指定频道', send.call_args.kwargs['content'])
        self.assertNotIn('test-only-secret', send.call_args.kwargs['content'])
        self.assertNotIn('内部原价', send.call_args.kwargs['content'])

    @patch('orders.kook_dispatch._send_task_safely')
    @patch('integrations.kook.KookClient.send_channel_message')
    def test_changed_target_or_disabled_rule_blocks_old_queued_message(self, send, enqueue):
        self.save()
        record = queue_dispatch(self.order, 'KOOK')
        self.save(revision=1, routes=[self.route(channel_id='999999')])
        self.assertEqual(deliver_dispatch('KOOK', record.pk), 'failed:configuration')
        send.assert_not_called()

    @patch('orders.kook_dispatch._send_task_safely')
    def test_auto_switches_and_unmatched_orders_do_not_enqueue(self, enqueue):
        self.save(auto_new_orders=False, auto_rejected_orders=False)
        for trigger in ['NEW_ORDER', 'PROVIDER_REJECTED']:
            self.assertIsNone(enqueue_kook_dispatch(self.order, trigger))
        self.save(revision=1, routes=[self.route(platform='MOBILE')])
        self.assertIsNone(queue_dispatch(self.order, 'KOOK'))
        enqueue.assert_not_called()

    @patch('integrations.kook.KookClient.get_channel', return_value={'name': '测试频道', 'channel_id': '123456', 'guild_id': '888'})
    def test_channel_check_is_read_only_even_when_dispatch_disabled(self, check):
        self.save(enabled=False)
        response = self.client.post(self.url + 'check/', {'route_id': 'main'}, format='json')
        self.assertEqual(response.status_code, 200)
        check.assert_called_once_with('123456', guild_id='')
        self.assertFalse(KookDispatchProbe.objects.exists())

    @patch('integrations.kook.KookClient.send_channel_message', return_value=KookMessageResult('test-message'))
    def test_probe_is_explicit_idempotent_and_creates_no_orders_or_transactions(self, send):
        self.save(enabled=False)
        before = (Order.objects.count(), Transaction.objects.count())
        body = {'route_id': 'main', 'request_id': str(uuid4())}
        self.assertEqual(self.client.post(self.url + 'test/', body, format='json').status_code, 400)
        body['confirm'] = True
        for _ in range(2):
            result = self.client.post(self.url + 'test/', body, format='json')
            self.assertEqual(result.data['data']['status'], 'SENT')
        send.assert_called_once()
        self.assertIn('联调测试', send.call_args.kwargs['content'])
        self.assertNotIn('(rol)', send.call_args.kwargs['content'])
        self.assertEqual((Order.objects.count(), Transaction.objects.count()), before)

    @patch('integrations.kook.KookClient.send_channel_message', side_effect=KookDeliveryUnknown('timeout'))
    def test_probe_unknown_result_is_not_automatically_repeated(self, send):
        self.save()
        body = {'route_id': 'main', 'request_id': str(uuid4()), 'confirm': True}
        for _ in range(2):
            response = self.client.post(self.url + 'test/', body, format='json')
            self.assertEqual(response.data['data']['status'], 'UNKNOWN')
        send.assert_called_once()

    @override_settings(KOOK_ENABLED=True, KOOK_BOT_TOKEN='legacy-token', KOOK_DISPATCH_CHANNEL_ID='777777')
    def test_first_admin_save_can_migrate_environment_token_without_echoing_it(self):
        data = self.save(bot_token='')
        self.assertTrue(data['token_saved'])
        self.assertEqual(get_kook_config().token, 'legacy-token')
        self.assertNotIn('legacy-token', json.dumps(data, default=str))
