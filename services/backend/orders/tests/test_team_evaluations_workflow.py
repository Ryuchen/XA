"""A boss rates each participant of an actual two-provider order separately."""
from datetime import timedelta
from tempfile import TemporaryDirectory
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamEvaluationsWorkflowTest(LiveServerTestCase):
    def test_boss_reviews_each_provider_and_receives_independent_replies(self):
        media = TemporaryDirectory(prefix='xa-team-evaluations-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                                   pass_expires_at=timezone.now() + timedelta(days=7)) for name in ('评价陪玩甲', '评价陪玩乙')]
        staff = make_console_user(['dashboard:view', 'evaluation:view', 'evaluation:reply', 'evaluation:delete', 'wallet:recharge'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, *providers, staff)]
        staff.admin_membership.account = accounts[-1]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2)
        for user, account in zip(providers, accounts[1:3]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)

        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        ok(clients[-1].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
           'amount': 50000, 'gift_amount': 0, 'trade_no': 'team-evaluation-recharge',
           'proof_image': screenshot(), 'client_request_id': 'team-evaluation-recharge'}, format='multipart'))
        order_id = ok(clients[0].post('/api/orders/create/', {'service_id': service.pk,
            'provider_ids': [account.pk for account in accounts[1:3]], 'client_request_id': 'team-evaluation-order'}, format='json'))['id']
        for client in clients[1:3]:
            ok(client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart'))
        for client in clients[1:3]:
            ok(client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': screenshot()}, format='multipart'))
        details = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual({target['provider_account_id'] for target in details['evaluation_targets']}, {accounts[1].pk, accounts[2].pk})
        completed_notices = [item for item in ok(clients[0].get('/api/messages/'))['messages']
                             if item['title'] == '订单已完成' and item['related_order_id'] == order_id]
        self.assertEqual(len(completed_notices), 1)
        self.assertIn('可在订单中评价', completed_notices[0]['preview'])
        self.assertNotIn('积分', completed_notices[0]['preview'])
        self.assertNotEqual(clients[0].post('/api/orders/evaluate/', {'order_id': order_id,
            'provider_account_id': accounts[-1].pk, 'score': 5}, format='json').data['code'], 0)
        self.assertNotEqual(clients[0].post('/api/orders/evaluate/', {'order_id': order_id, 'score': 5}, format='json').data['code'], 0)
        repo = Path(settings.BASE_DIR).parent.parent
        native = subprocess.run(['node', str(repo / 'scripts/native-team-evaluations-live.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(accounts[0])[0],
                'bossId': str(accounts[0].pk), 'orderId': order_id, 'providerIds': [account.pk for account in accounts[1:3]]}),
            text=True, encoding='utf-8', capture_output=True, timeout=40)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        self.assertEqual(ok(clients[-1].get('/api/admin/dashboard/stats'))['unreplied_evaluation_total'], 2)
        pending = ok(clients[-1].get('/api/admin/evaluations/?only_unreplied=1'))
        self.assertEqual(len([row for row in pending['list'] if row['order'] == order_id]), 2)
        if os.environ.get('XA_TEAM_EVALUATION_BROWSER') == '1':
            queue = subprocess.run(['node', str(repo / 'scripts/team-evaluations-375-live.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'orderNo': details['order_no'], 'queueOnly': True,
                    'providerRoot': os.environ['XA_TEAM_EVALUATION_PROVIDER_ROOT'],
                    'adminRoot': os.environ['XA_TEAM_EVALUATION_ADMIN_ROOT'],
                    'staffToken': issue_account_tokens(accounts[-1])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(queue.returncode, 0, queue.stdout + queue.stderr)
        for index, account in enumerate(accounts[1:3]):
            own = ok(clients[index + 1].get('/api/orders/evaluations/mine/'))['list']
            self.assertEqual(len(own), 1)
            self.assertEqual(own[0]['score'], 5 - index)
            visible = ok(clients[index + 1].get(f'/api/orders/orders/{order_id}/'))['evaluations']
            self.assertEqual([item['id'] for item in visible], [own[0]['id']])
            ok(clients[index + 1].post(f"/api/orders/evaluations/{own[0]['id']}/reply/",
               {'reply_content': f'独立回复{index}', 'reply_version': 0}, format='json'))
        details = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(ok(clients[-1].get('/api/admin/dashboard/stats'))['unreplied_evaluation_total'], 0)
        self.assertEqual(ok(clients[-1].get('/api/admin/evaluations/?only_unreplied=1'))['list'], [])
        self.assertTrue(details['is_evaluated'])
        self.assertEqual({item['reply_content'] for item in details['evaluations']}, {'独立回复0', '独立回复1'})
        inbox = ok(clients[0].get('/api/messages/'))['messages']
        notices = [item for item in inbox if item['title'] == '陪玩回复了你的评价' and item['related_order_id'] == order_id]
        self.assertEqual(len(notices), 2)
        for name in ('评价陪玩甲', '评价陪玩乙'):
            self.assertEqual(sum(name in item['preview'] for item in notices), 1)
        self.assertTrue(all(ok(clients[0].get(f"/api/messages/{item['id']}/"))['related_order_id'] == order_id
                            for item in notices))
        public_reviews = ok(clients[0].get(f'/api/orders/services/{service.pk}/evaluations/'))
        self.assertEqual(public_reviews['total'], 2)
        self.assertEqual({row['content']: row['provider_name'] for row in public_reviews['list']},
                         {'独立评价0': '评价陪玩甲', '独立评价1': '评价陪玩乙'})
        self.assertNotEqual(clients[0].post('/api/orders/evaluate/', {'order_id': order_id,
            'provider_account_id': accounts[1].pk, 'score': 5}, format='json').data['code'], 0)
        rows = ok(clients[-1].get('/api/admin/evaluations/'))['list']
        self.assertEqual(len([row for row in rows if row['order'] == order_id]), 2)
        self.assertEqual({row['provider_name'] for row in rows if row['order'] == order_id}, {'评价陪玩甲', '评价陪玩乙'})
        filtered = ok(clients[-1].get('/api/admin/evaluations/', {'keyword': '评价陪玩乙'}))['list']
        self.assertEqual([row['provider_name'] for row in filtered], ['评价陪玩乙'])
        if os.environ.get('XA_TEAM_EVALUATION_BROWSER') == '1':
            browser = subprocess.run(['node', str(repo / 'scripts/team-evaluations-375-live.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'orderNo': details['order_no'],
                    'providerRoot': os.environ['XA_TEAM_EVALUATION_PROVIDER_ROOT'],
                    'adminRoot': os.environ['XA_TEAM_EVALUATION_ADMIN_ROOT'],
                    'providers': [{'token': issue_account_tokens(account)[0], 'id': account.pk} for account in accounts[1:3]],
                    'staffToken': issue_account_tokens(accounts[-1])[0]}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
            details = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
            self.assertEqual({item['reply_content'] for item in details['evaluations']}, {'评价页更新回复', '独立回复1'})
            rows = ok(clients[-1].get('/api/admin/evaluations/'))['list']

        first = next(row for row in rows if row['order'] == order_id and row['provider'] == providers[0].pk)
        second = next(row for row in rows if row['order'] == order_id and row['provider'] == providers[1].pk)
        self.assertEqual(first['reply_author'], 'PROVIDER')
        staff_reply = ok(clients[-1].post(f"/api/admin/evaluations/{first['id']}/reply/",
            {'reply_content': '客服核实回复', 'reply_version': first['reply_version']}, format='json'))
        self.assertEqual(staff_reply['reply_author'], 'STAFF')
        boss_detail = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(next(row for row in boss_detail['evaluations'] if row['id'] == first['id'])['reply_author'], 'STAFF')
        self.assertEqual(next(row for row in ok(clients[0].get(f'/api/orders/services/{service.pk}/evaluations/'))['list']
                              if row['id'] == first['id'])['reply_author'], 'STAFF')
        own = ok(clients[1].get('/api/orders/evaluations/mine/'))['list'][0]
        self.assertEqual((own['reply_author'], own['reply_content']), ('STAFF', '客服核实回复'))
        notices = ok(clients[1].get('/api/messages/'))['messages']
        self.assertEqual(len([item for item in notices if item['title'] == '客服更新了评价回复'
                              and item['related_order_id'] == order_id]), 1)
        self.assertFalse(any(item['title'] == '客服更新了评价回复' and item['related_order_id'] == order_id
                             for item in ok(clients[2].get('/api/messages/'))['messages']))
        if os.environ.get('XA_REPLY_AUTHOR_NATIVE') == '1':
            native_author = subprocess.run(['node', str(repo / 'scripts/native-evaluation-author-375.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(accounts[0])[0],
                    'bossId': str(accounts[0].pk), 'orderId': order_id, 'serviceId': service.pk}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(native_author.returncode, 0, native_author.stdout + native_author.stderr)
        if os.environ.get('XA_REPLY_AUTHOR_BROWSER') == '1':
            browser = subprocess.run(['node', str(repo / 'scripts/team-evaluations-375-live.cjs')], cwd=repo,
                input=json.dumps({'base': self.live_server_url, 'replyAuthorOnly': True,
                    'providerRoot': os.environ['XA_TEAM_EVALUATION_PROVIDER_ROOT'],
                    'adminRoot': os.environ['XA_TEAM_EVALUATION_ADMIN_ROOT'],
                    'orderNo': details['order_no'], 'staffToken': issue_account_tokens(accounts[-1])[0],
                    'provider': {'token': issue_account_tokens(accounts[1])[0], 'id': accounts[1].pk}}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(browser.returncode, 0, browser.stdout + browser.stderr)
        provider_reply = ok(clients[1].post(f"/api/orders/evaluations/{first['id']}/reply/",
            {'reply_content': '陪玩再次回复', 'reply_version': own['reply_version']}, format='json'))
        self.assertEqual(provider_reply['reply_author'], 'PROVIDER')
        first = ok(clients[-1].get(f"/api/admin/evaluations/{first['id']}/"))
        self.assertEqual((first['reply_author'], first['reply_content']), ('PROVIDER', '陪玩再次回复'))
        deleted = clients[-1].delete(f"/api/admin/evaluations/{first['id']}/",
                                     {'reply_version': first['reply_version']}, format='json')
        self.assertEqual(deleted.data['code'], 0, deleted.data)
        details = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
        self.assertFalse(details['is_evaluated'])
        self.assertEqual([row['id'] for row in details['evaluations']], [second['id']])
        self.assertEqual({target['provider_account_id']: target['reviewed'] for target in details['evaluation_targets']},
                         {accounts[1].pk: False, accounts[2].pk: True})
        providers[0].escort_profile.refresh_from_db()
        providers[1].escort_profile.refresh_from_db()
        self.assertEqual((providers[0].escort_profile.rating_count, providers[1].escort_profile.rating_count), (0, 1))
        ok(clients[0].post('/api/orders/evaluate/', {'order_id': order_id,
           'provider_account_id': accounts[1].pk, 'score': 3, 'content': '重新评价甲'}, format='json'))
        self.assertEqual(ok(clients[-1].get('/api/admin/dashboard/stats'))['unreplied_evaluation_total'], 1)
        details = ok(clients[0].get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(details['is_evaluated'])
        self.assertEqual({row['content'] for row in details['evaluations']}, {'重新评价甲', '独立评价1'})
        providers[0].escort_profile.refresh_from_db()
        providers[1].escort_profile.refresh_from_db()
        self.assertEqual((providers[0].escort_profile.rating_count, providers[1].escort_profile.rating_count), (1, 1))
        role = staff.admin_membership.roles.first()
        role.permissions = ['dashboard:view']
        role.save(update_fields=['permissions'])
        self.assertIsNone(ok(clients[-1].get('/api/admin/dashboard/stats'))['unreplied_evaluation_total'])
