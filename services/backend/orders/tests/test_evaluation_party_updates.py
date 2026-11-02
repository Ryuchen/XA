"""Real order evaluation and reply remain visible to both order parties."""
from asgiref.sync import async_to_sync
import json
import os
from pathlib import Path
import shutil
import subprocess

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from orders.tests import test_release_flow as flow


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class EvaluationPartyUpdatesTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_evaluation_only_staff_receives_live_queue_updates(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_console_user
        from rest_framework.test import APIClient

        order = self.new_order()
        self.finish(order)
        staff = make_console_user(['dashboard:view', 'evaluation:view', 'evaluation:delete'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        role = staff.admin_membership.roles.get()
        clients = []
        for account in (self.accounts[self.boss.pk], self.accounts[self.provider.pk], staff_account):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        token = issue_account_tokens(staff_account)[0]

        async def run():
            socket = WebsocketCommunicator(application, f'/ws/orders/?token={token}')
            self.assertTrue((await socket.connect())[0])
            async def assert_queue_refreshes():
                # Dashboard and evaluation refreshes are independent events;
                # their delivery order is not a client contract.
                received = {(await socket.receive_json_from(timeout=3))['type'] for _ in range(2)}
                self.assertEqual(received, {'dashboard_order_update', 'evaluation_status_update'})
            try:
                response = await database_sync_to_async(clients[0].post)('/api/orders/evaluate/',
                    {'order_id': order.pk, 'score': 4}, format='json')
                evaluation = self.ok(response)
                await assert_queue_refreshes()
                stats = self.ok(await database_sync_to_async(clients[2].get)('/api/admin/dashboard/stats'))
                self.assertEqual(stats['unreplied_evaluation_total'], 1)
                response = await database_sync_to_async(clients[1].post)(
                    f"/api/orders/evaluations/{evaluation['id']}/reply/",
                    {'reply_content': '谢谢', 'reply_version': 0}, format='json')
                self.ok(response)
                await assert_queue_refreshes()
                stats = self.ok(await database_sync_to_async(clients[2].get)('/api/admin/dashboard/stats'))
                self.assertEqual(stats['unreplied_evaluation_total'], 0)
                response = await database_sync_to_async(clients[2].delete)(
                    f"/api/admin/evaluations/{evaluation['id']}/", {'reply_version': 1}, format='json')
                self.ok(response)
                await assert_queue_refreshes()
                role.permissions = ['dashboard:view']
                await database_sync_to_async(role.save)(update_fields=['permissions'])
                await socket.send_json_to({'type': 'ping'})
                self.assertEqual(await socket.receive_json_from(), {'type': 'pong'})
                response = await database_sync_to_async(clients[0].post)('/api/orders/evaluate/',
                    {'order_id': order.pk, 'score': 3}, format='json')
                self.ok(response)
                self.assertEqual((await socket.receive_json_from(timeout=3))['type'], 'dashboard_order_update')
                self.assertTrue(await socket.receive_nothing(timeout=0.2))
                stats = self.ok(await database_sync_to_async(clients[2].get)('/api/admin/dashboard/stats'))
                self.assertIsNone(stats['unreplied_evaluation_total'])
            finally:
                await socket.disconnect()
        async_to_sync(run)()

    def test_staff_reply_notifies_boss_and_refreshes_both_order_views(self):
        order = self.new_order()
        self.finish(order)
        self.login(self.boss)
        evaluation = self.ok(self.client.post('/api/orders/evaluate/', {
            'order_id': order.pk, 'score': 3, 'content': '隔离评价', 'is_anonymous': True,
        }, format='json'))
        role = self.admin.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'evaluation:view', 'evaluation:reply', 'evaluation:delete']
        role.save(update_fields=['permissions'])
        boss_token, provider_token, staff_token = [issue_account_tokens(self.accounts[user.pk])[0]
                                                    for user in (self.boss, self.provider, self.admin)]

        async def run():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}')
                       for token in (boss_token, provider_token)]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            try:
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                response = await database_sync_to_async(self.client.post)(
                    f"/api/admin/evaluations/{evaluation['id']}/reply/",
                    {'reply_content': '客服代回复', 'reply_version': 0}, format='json')
                self.ok(response)
                for socket in sockets:
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=3)
                        if event['type'] == 'order_status_update' and event['data']['id'] == order.pk:
                            self.assertEqual(event['data']['evaluation']['reply_content'], '客服代回复')
                            break
                    else:
                        self.fail('未收到评价回复的订单更新')
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                notices = [item for item in inbox if item['title'] == '客服回复了你的评价'
                           and item['related_order_id'] == order.pk]
                self.assertEqual(len(notices), 1)
                self.assertNotIn('隔离评价', notices[0]['preview'])
                provider_account = self.accounts[self.provider.pk]
                self.assertIn(provider_account.nickname or provider_account.username, notices[0]['preview'])
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                conflict = await database_sync_to_async(self.client.post)(
                    f"/api/admin/evaluations/{evaluation['id']}/reply/",
                    {'reply_content': '旧版本', 'reply_version': 0}, format='json')
                self.assertEqual(conflict.status_code, 409)
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                self.assertEqual(len([item for item in inbox if item['title'] == '客服回复了你的评价'
                                      and item['related_order_id'] == order.pk]), 1)
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
                deleted = await database_sync_to_async(self.client.delete)(
                    f"/api/admin/evaluations/{evaluation['id']}/", {'reply_version': 1}, format='json')
                self.ok(deleted)
                for socket in sockets:
                    for _ in range(8):
                        event = await socket.receive_json_from(timeout=3)
                        if event['type'] == 'order_status_update' and event['data']['id'] == order.pk:
                            self.assertFalse(event['data']['is_evaluated'])
                            self.assertIsNone(event['data']['evaluation'])
                            break
                    else:
                        self.fail('未收到评价删除的订单更新')
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(run)()

    def test_evaluation_and_reply_notify_only_their_parties_after_commit(self):
        order = self.new_order()
        self.finish(order)
        tokens = [issue_account_tokens(self.accounts[user.pk])[0] for user in (self.boss, self.provider)]

        async def run():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={token}') for token in tokens]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])

            async def order_update(socket, reply=''):
                for _ in range(8):
                    event = await socket.receive_json_from(timeout=3)
                    if event['type'] == 'order_status_update' and event['data']['id'] == order.pk:
                        self.assertTrue(event['data']['is_evaluated'])
                        self.assertEqual(event['data']['evaluation']['reply_content'], reply)
                        return
                self.fail('missing evaluation order update')

            try:
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
                evaluated = await database_sync_to_async(self.client.post)('/api/orders/evaluate/', {
                    'order_id': order.pk, 'score': 3, 'content': '私人评价内容', 'is_anonymous': True,
                }, format='json')
                evaluation = self.ok(evaluated)
                for socket in sockets:
                    await order_update(socket)

                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                notices = [m for m in inbox if m['title'] == '收到新的服务评价' and m['related_order_id'] == order.pk]
                self.assertEqual(len(notices), 1)
                self.assertNotIn('私人评价内容', notices[0]['preview'])
                provider_notice_id = notices[0]['id']
                notice_detail = self.ok(await database_sync_to_async(self.client.get)(f'/api/messages/{provider_notice_id}/'))
                self.assertEqual(notice_detail['related_order_id'], order.pk)
                linked_order = self.ok(await database_sync_to_async(self.client.get)(f'/api/orders/orders/{order.pk}/'))
                self.assertTrue(linked_order['is_evaluated'])
                node = shutil.which('node')
                if node:
                    repo = Path(__file__).resolve().parents[4]
                    env = {**os.environ, 'XA_EVALUATION_NOTICE': json.dumps(notice_detail, ensure_ascii=False)}
                    result = await database_sync_to_async(subprocess.run)(
                        [node, '--test', str(repo / 'apps/provider-miniapp/tests/message-order-navigation.cjs')],
                        env=env, cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('real evaluation notice opens', result.stdout)
                received = self.ok(await database_sync_to_async(self.client.get)('/api/orders/evaluations/mine/'))['list']
                self.assertEqual(received[0]['customer_name'], '匿名用户')
                reply = await database_sync_to_async(self.client.post)(
                    f"/api/orders/evaluations/{evaluation['id']}/reply/",
                    {'reply_content': '感谢你的建议', 'reply_version': 0}, format='json')
                self.ok(reply)
                for socket in sockets:
                    await order_update(socket, '感谢你的建议')

                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                notices = [m for m in inbox if m['title'] == '陪玩回复了你的评价' and m['related_order_id'] == order.pk]
                self.assertEqual(len(notices), 1)
                notice_detail = self.ok(await database_sync_to_async(self.client.get)(f"/api/messages/{notices[0]['id']}/"))
                self.assertEqual(notice_detail['related_order_id'], order.pk)
                unrelated_notice = await database_sync_to_async(self.client.get)(f'/api/messages/{provider_notice_id}/')
                self.assertEqual(unrelated_notice.data['code'], 404)
                detail = self.ok(await database_sync_to_async(self.client.get)(f'/api/orders/orders/{order.pk}/'))
                self.assertEqual(detail['evaluation']['reply_content'], '感谢你的建议')
                duplicate = await database_sync_to_async(self.client.post)('/api/orders/evaluate/',
                    {'order_id': order.pk, 'score': 3}, format='json')
                self.assertNotEqual(duplicate.data['code'], 0)
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
                stale = await database_sync_to_async(self.client.post)(
                    f"/api/orders/evaluations/{evaluation['id']}/reply/",
                    {'reply_content': '重复', 'reply_version': 0}, format='json')
                self.assertEqual(stale.status_code, 409)
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                self.assertEqual(len([m for m in inbox if m['title'] == '收到新的服务评价' and m['related_order_id'] == order.pk]), 1)
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[0])
                inbox = self.ok(await database_sync_to_async(self.client.get)('/api/messages/'))['messages']
                self.assertEqual(len([m for m in inbox if m['title'] == '陪玩回复了你的评价' and m['related_order_id'] == order.pk]), 1)
            finally:
                for socket in sockets:
                    await socket.disconnect()

        async_to_sync(run)()
