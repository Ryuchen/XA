"""Offline order parties receive committed aftersale updates via real messages."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.test import override_settings
from rest_framework.test import APITransactionTestCase

from orders.tests import test_provider_aftersale_status as flow
from orders.tests.factories import make_provider
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AfterSalePartyNoticeTest(APITransactionTestCase):
    setUp = flow.ProviderAfterSaleStatusTest.setUp
    login = flow.ProviderAfterSaleStatusTest.login
    ok = flow.ProviderAfterSaleStatusTest.ok
    prepare = flow.ProviderAfterSaleStatusTest.prepare
    resolve = flow.ProviderAfterSaleStatusTest.resolve

    def messages(self, user, order_id, title):
        self.login(user)
        return [row for row in self.ok(self.client.get('/api/messages/'))['messages']
                if row['related_order_id'] == order_id and row['title'] == title]

    def test_offline_parties_receive_open_and_resolution_once_per_case(self):
        order_id, report_id = self.prepare()
        outsider = make_provider()
        outsider_account = get_or_create_account_for_legacy_user(outsider)
        self.tokens[outsider.pk] = issue_account_tokens(outsider_account)[0]
        self.login(self.boss)
        payload = {'reason': '仅客服查看的隐私事项', 'client_request_id': 'offline-case-one'}
        ticket = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', payload, format='json'))
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', payload, format='json'))
        opened = self.messages(self.provider, order_id, '订单售后处理中')
        self.assertEqual(len(opened), 1)
        self.assertNotIn(payload['reason'], str(opened))
        self.assertEqual(self.messages(outsider, order_id, '订单售后处理中'), [])
        self.resolve(ticket['id'])
        resolved = self.messages(self.boss, order_id, '售后已处理')
        self.assertEqual(len(resolved), 1)
        self.assertIn('查看订单', resolved[0]['preview'])
        self.assertEqual(len(self.messages(self.provider, order_id, '订单售后已处理')), 1)
        self.login(self.provider)
        provider_order = self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))
        self.assertEqual(provider_order['aftersale_status'], 'RESOLVED')
        node = shutil.which('node')
        if not node:
            self.fail('Node runtime required to verify provider presentation')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, '--test', '--test-name-pattern=real resolved aftersale response',
                                 str(repo / 'apps/provider-miniapp/tests/order-report-actions.cjs')],
            env={**os.environ, 'XA_REAL_RESOLVED_AFTERSALE': json.dumps(provider_order, cls=DjangoJSONEncoder)},
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        report = next(item for item in reports if item['id'] == report_id)
        self.assertEqual((report['aftersale_status'], report['settlement_status']), ('RESOLVED', 'WAITING'))
        result = subprocess.run([node, '--test', '--test-name-pattern=real resolved aftersale report',
                                 str(repo / 'apps/provider-miniapp/tests/report-flow.cjs')],
            env={**os.environ, 'XA_REAL_RESOLVED_REPORT': json.dumps(report, cls=DjangoJSONEncoder)},
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.login(self.staff)
        again = self.client.post(f'/api/admin/aftersales/{ticket["id"]}/resolve/', {'resolution': '再次提交'}, format='json')
        self.assertEqual(again.status_code, 400)
        self.assertEqual(len(self.messages(self.boss, order_id, '售后已处理')), 1)
        self.login(self.boss)
        payload = {'reason': '补充说明', 'client_request_id': 'offline-case-two'}
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', payload, format='json'))
        self.assertEqual(len(self.messages(self.provider, order_id, '订单售后处理中')), 2)
        self.resolve(ticket['id'])
        self.assertEqual(len(self.messages(self.boss, order_id, '售后已处理')), 2)

    def test_both_team_members_receive_the_case_notice(self):
        teammate = make_provider()
        teammate_account = get_or_create_account_for_legacy_user(teammate)
        teammate.escort_profile.account = teammate_account
        teammate.escort_profile.save(update_fields=['account'])
        self.accounts[teammate.pk] = teammate_account
        self.tokens[teammate.pk] = issue_account_tokens(teammate_account)[0]
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, teammate_account.pk],
            'client_request_id': 'offline-team-order',
        }, format='json'))['id']
        for provider in (self.provider, teammate):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': flow.image()}, format='multipart'))
        for provider in (self.provider, teammate):
            self.login(provider)
            self.ok(self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': flow.image()}, format='multipart'))
        self.login(self.boss)
        ticket = self.ok(self.client.post(f'/api/orders/orders/{order_id}/aftersale/', {
            'reason': '双人服务体验问题', 'client_request_id': 'offline-team-case',
        }, format='json'))
        for provider in (self.provider, teammate):
            self.assertEqual(len(self.messages(provider, order_id, '订单售后处理中')), 1)
        self.resolve(ticket['id'])
        for provider in (self.provider, teammate):
            self.assertEqual(len(self.messages(provider, order_id, '订单售后已处理')), 1)
