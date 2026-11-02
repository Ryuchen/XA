"""Real API coverage for a two-provider order and its report handoff."""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import override_settings
from django.conf import settings
from django.db import connection
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from wallet.models import ProviderReport, Transaction


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class TeamReportHandoffTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge

    def test_transfer_messages_describe_zero_share_and_existing_service(self):
        second = make_provider(display_name='留守陪玩')
        incoming = make_provider(display_name='转入陪玩')
        for provider in (second, incoming):
            self.accounts[provider.pk] = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = self.accounts[provider.pk]
            provider.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-transfer-zero-message',
        }, format='json'))['id']
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[incoming.pk].pk,
                           'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0}],
            'reason': '隔离转单消息验证',
        }, format='json'))
        self.login(self.provider)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        old = next(item for item in messages if item['title'] == '订单已转出' and item['related_order_id'] == order_id)
        self.assertIn('无需提交报单', old['preview'])
        refused = self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json')
        self.assertIn('无需提交报单', refused.data['msg'])
        self.login(incoming)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        new = next(item for item in messages if item['title'] == '客服转单给你' and item['related_order_id'] == order_id)
        self.assertIn('继续服务', new['preview'])
        self.assertNotIn('开始服务', new['preview'])
        current = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(current['status'], 'IN_SERVICE')
        self.login(second)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        retained = [item for item in messages if item['title'] == '服务搭档已更换' and item['related_order_id'] == order_id]
        self.assertEqual(len(retained), 1)
        self.assertIn('查看订单', retained[0]['preview'])
        current = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual({item['id'] for item in current['participants']},
                         {self.accounts[second.pk].pk, self.accounts[incoming.pk].pk})
        self.login(self.admin)
        repeated = self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[incoming.pk].pk,
                           'split_type': 'PERCENT', 'split_value': 0, 'penalty_amount': 0}],
        }, format='json')
        self.assertNotEqual(repeated.data['code'], 0)
        self.login(second)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        self.assertEqual(sum(item['title'] == '服务搭档已更换' and item['related_order_id'] == order_id
                             for item in messages), 1)

    def test_returned_member_must_complete_new_team_assignment(self):
        second = make_provider(display_name='留守陪玩')
        incoming = make_provider(display_name='临时代班')
        for provider in (second, incoming):
            self.accounts[provider.pk] = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = self.accounts[provider.pk]
            provider.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-return-handoff',
        }, format='json'))['id']
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        prior_report = ProviderReport.objects.get(order_id=order_id, provider=self.provider)
        self.ok(self.client.post(f'/api/wallet/reports/{prior_report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        old_image = prior_report.completion_image.name
        self.login(self.admin)
        for old, new in ((self.provider, incoming), (incoming, self.provider)):
            self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
                'transfers': [{'old_provider_id': self.accounts[old.pk].pk,
                               'new_provider_id': self.accounts[new.pk].pk,
                               'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
                'reason': '隔离转回验证',
            }, format='json'))
        self.login(self.boss)
        progress = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['team_progress']
        self.assertEqual(ProviderReport.objects.get(order_id=order_id, provider=self.provider).completion_image.name, old_image)
        returned = next(row for row in progress['members'] if row['id'] == self.accounts[self.provider.pk].pk)
        self.assertFalse(returned['submitted'])
        self.login(second)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'IN_SERVICE')
        self.login(self.provider)
        report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        self.assertTrue(report['entry_image_url'] and report['completion_image_url'] and report['result_image_urls'])
        self.assertFalse(report['can_submit'])
        self.assertIn('COMPLETION', report['evidence_upload_block_reasons'])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED')
        current = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        self.assertEqual(current['evidence_upload_block_reasons'], {})
        self.assertTrue(current['can_submit'])
        self.assertTrue(ProviderReport.objects.get(order_id=order_id, provider=self.provider).completion_image.storage.exists(old_image))

    def test_transferred_member_can_prepare_evidence_without_completing_team(self):
        second = make_provider(display_name='留守陪玩')
        incoming = make_provider(display_name='转入陪玩')
        for provider in (second, incoming):
            self.accounts[provider.pk] = get_or_create_account_for_legacy_user(provider)
            provider.escort_profile.account = self.accounts[provider.pk]
            provider.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-transfer-report-handoff',
        }, format='json'))['id']
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
        layer = get_channel_layer()
        reviewer_channel = async_to_sync(layer.new_channel)()
        async_to_sync(layer.group_add)('report_reviewers', reviewer_channel)
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order_id}/transfer/', {
            'transfers': [{'old_provider_id': self.accounts[self.provider.pk].pk,
                           'new_provider_id': self.accounts[incoming.pk].pk,
                           'split_type': 'PERCENT', 'split_value': 40, 'penalty_amount': 0}],
            'reason': '隔离双陪转单',
        }, format='json'))
        notice = async_to_sync(asyncio.wait_for)(layer.receive(reviewer_channel), timeout=2)
        self.assertEqual((notice['type'], notice['data']['order_id']), ('report_status_update', order_id))
        self.login(self.provider)
        messages = self.ok(self.client.get('/api/messages/'))['messages']
        old_notice = next(item for item in messages if item['title'] == '订单已转出' and item['related_order_id'] == order_id)
        self.assertIn('保留收益待整单报单通过后结算', old_notice['preview'])
        old_detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertTrue(old_detail['my_assignment_released'])
        self.assertFalse(old_detail['my_assignment_settled'])
        self.assertEqual(old_detail['settlement_mode'], 'REPORT_APPROVAL')
        old_list = self.ok(self.client.get('/api/orders/orders/', {'status': 'in_progress'}))
        self.assertTrue(next(row for row in old_list if row['id'] == order_id)['my_assignment_released'])
        node = shutil.which('node')
        if os.environ.get('XA_TEAM_REPORT_BROWSER') == '1':
            self.assertTrue(node)
            self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                for _ in range(150):
                    if process.poll() is not None: self.fail('isolated ASGI server exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.1)
                else: self.fail('isolated ASGI server did not start')
                repo = Path(settings.BASE_DIR).parent.parent
                providers = []
                for user, released, income in ((self.provider, True, '320币'), (incoming, False, '480币')):
                    account = self.accounts[user.pk]
                    providers.append({'id': account.pk, 'token': issue_account_tokens(account)[0], 'released': released,
                        'income': income, 'canReport': True, 'waitingNotice': '报单全部通过、售后窗口结束且无未结客诉后结算'})
                result = subprocess.run([node, str(repo / 'scripts/provider-transfer-375-live.cjs')],
                    input=json.dumps({'api': f'http://127.0.0.1:{port}', 'orderNo': old_detail['order_no'],
                                      'participantText': '留守陪玩 + 转入陪玩', 'providers': providers}),
                    cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=70)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        if node:
            repo = Path(__file__).resolve().parents[4]
            result = subprocess.run(
                [node, '--test', '--test-name-pattern=transferred provider sees',
                 str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30,
                env={**os.environ, 'XA_TRANSFERRED_ORDER_JSON': json.dumps(old_detail)},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        old_report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        old_share = Order.objects.get(pk=order_id).providers.get(provider_account=self.accounts[self.provider.pk])
        self.assertEqual(old_report['amount'], old_share.settlement_base)
        self.assertNotIn('COMPLETION', old_report['evidence_upload_block_reasons'])
        self.ok(self.client.post(f"/api/wallet/reports/{old_report['id']}/images/",
                                 {'kind': 'COMPLETION', 'image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'IN_SERVICE')
        self.login(self.boss)
        team = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['team_progress']
        self.assertEqual(team['submitted_count'], 0)
        for provider in (second, incoming):
            self.login(provider)
            active_detail = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
            self.assertFalse(active_detail['my_assignment_released'])
            if provider == incoming:
                messages = self.ok(self.client.get('/api/messages/'))['messages']
                new_notice = next(item for item in messages if item['title'] == '客服转单给你' and item['related_order_id'] == order_id)
                self.assertIn('接手并继续服务', new_notice['preview'])
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            self.assertIn('COMPLETION', report['evidence_upload_block_reasons'])
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED')
        for provider in (self.provider, second, incoming):
            self.login(provider)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            if provider == incoming:
                self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                    {'kind': 'ENTRY', 'image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            submitted = self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/submit/"))
            self.assertEqual(submitted['status'], 'PENDING')
            self.assertEqual(submitted['amount'], Order.objects.get(pk=order_id).providers.get(
                provider_account=self.accounts[provider.pk]).settlement_base)
        self.login(self.admin)
        reports = list(ProviderReport.objects.filter(order_id=order_id).order_by('pk'))
        self.assertEqual(len(reports), 3)
        # Even after the aftersale window, one approved report cannot settle a
        # transferred team while the other participants still await review.
        Order.objects.filter(pk=order_id).update(completed_at=timezone.now() - timedelta(hours=73))
        from engagement.confirmation import process_order
        self.ok(self.client.post(f'/api/admin/reports/{reports[0].pk}/approve/'))
        process_order(order_id)
        self.assertIsNone(Order.objects.get(pk=order_id).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=order_id, tx_type='INCOME').exists())
        rows = self.ok(self.client.get('/api/admin/reports/'))['list']
        by_id = {row['id']: row for row in rows if row['order'] == order_id}
        self.assertEqual(set(by_id), {report.pk for report in reports})
        self.assertEqual(by_id[reports[0].pk]['status'], 'APPROVED')
        self.assertTrue(all(by_id[report.pk]['status'] == 'PENDING' for report in reports[1:]))
        self.assertTrue(all(row['settlement_status'] == 'WAITING' for row in by_id.values()))
        for report in reports[1:]:
            self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        # The completion-based aftersale window elapses. Execute the real
        # settlement workflow rather than manufacturing wallet transactions.
        process_order(order_id)
        self.assertIsNotNone(Order.objects.get(pk=order_id).settled_at)
        process_order(order_id)
        rows = self.ok(self.client.get('/api/admin/reports/'))['list']
        by_id = {row['id']: row for row in rows if row['order'] == order_id}
        self.assertEqual(set(by_id), {report.pk for report in reports})
        self.assertTrue(all(row['status'] == 'APPROVED' and row['settlement_status'] == 'SETTLED' for row in by_id.values()))
        for provider in (self.provider, second, incoming):
            self.login(provider)
            row = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['order_id'] == order_id)
            self.assertEqual(row['settlement_status'], 'SETTLED', row)
            account = self.accounts[provider.pk]
            share = Order.objects.get(pk=order_id).providers.get(provider_account=account)
            self.assertEqual(row['expected_income'], share.provider_income)
            self.assertEqual(row['payout_amount'], share.provider_income)
            self.assertEqual(Transaction.objects.filter(order_id=order_id, wallet__account=account,
                tx_type='INCOME', amount=share.provider_income, status='SUCCESS').count(), 1)
            self.assertEqual(by_id[row['id']]['expected_income'], share.provider_income)

    def test_evidence_cannot_bypass_team_handoff_and_both_reports_reach_review(self):
        second = make_provider(display_name='第二陪玩')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        data = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-report-handoff',
        }, format='json'))
        order_id = data['id']
        reports = []
        for provider in (self.provider, second):
            self.login(provider)
            report = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
            reports.append(report['id'])
            self.assertIn('订单页', report['evidence_upload_block_reasons']['ENTRY'])
            self.assertIn('入队', report['evidence_upload_block_reasons']['COMPLETION'])
            for kind in ('ENTRY', 'COMPLETION'):
                response = self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                            {'kind': kind, 'image': flow.screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 400, response.data)
            self.assertIn('START', self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['allowed_actions'])
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                     {'entry_image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f"/api/wallet/reports/{report['id']}/images/",
                                     {'kind': 'ENTRY', 'image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['status'], 'IN_SERVICE')
        for provider, report_id in zip((self.provider, second), reports):
            self.login(provider)
            report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report_id)
            self.assertNotIn('ENTRY', report['evidence_upload_block_reasons'])
            self.assertIn('订单页', report['evidence_upload_block_reasons']['COMPLETION'])
            blocked = self.client.post(f'/api/wallet/reports/{report_id}/images/',
                                       {'kind': 'COMPLETION', 'image': flow.screenshot()}, format='multipart')
            self.assertEqual(blocked.data['code'], 400, blocked.data)
            self.assertIn('COMPLETE', self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['allowed_actions'])
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                     {'completion_image': flow.screenshot()}, format='multipart'))
            self.ok(self.client.post(f'/api/wallet/reports/{report_id}/images/',
                                     {'kind': 'COMPLETION', 'image': flow.screenshot()}, format='multipart'))
        self.login(self.boss)
        self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['status'], 'COMPLETED')
        for provider, report_id in zip((self.provider, second), reports):
            self.login(provider)
            report = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report_id)
            self.assertEqual(report['evidence_upload_block_reasons'], {})
            self.ok(self.client.post(f'/api/wallet/reports/{report_id}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.assertEqual(self.ok(self.client.post(f'/api/wallet/reports/{report_id}/submit/'))['status'], 'PENDING')
        self.login(self.admin)
        for report_id in reports:
            self.assertEqual(self.ok(self.client.post(f'/api/admin/reports/{report_id}/approve/'))['status'], 'APPROVED')
        order = Order.objects.get(pk=order_id)
        self.assertIsNone(order.settled_at)
        self.assertIsNotNone(order.consumption_ready_at)

    def test_legacy_two_submitted_images_can_resume_the_existing_transition(self):
        second = make_provider(display_name='第二陪玩')
        self.accounts[second.pk] = get_or_create_account_for_legacy_user(second)
        second.escort_profile.account = self.accounts[second.pk]
        second.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[second.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'legacy-team-handoff',
        }, format='json'))['id']
        for provider in (self.provider, second):
            self.login(provider)
            self.ok(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json'))
        # Existing historical rows can contain both references without the order transition.
        ProviderReport.objects.filter(order_id=order_id).update(entry_image='legacy/entry.png')
        self.login(self.provider)
        self.assertIn('START', self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/',
                                 {'entry_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'IN_SERVICE')
        ProviderReport.objects.filter(order_id=order_id).update(completion_image='legacy/completion.png')
        self.login(second)
        self.assertIn('COMPLETE', self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['allowed_actions'])
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/',
                                 {'completion_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED')
