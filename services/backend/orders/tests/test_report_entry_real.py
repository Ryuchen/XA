"""Report entry follows real dispatch and transfer state for each participant."""
from uuid import uuid4

from django.test import override_settings
from rest_framework.test import APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.models import Order
from orders.tests.test_release_flow import screenshot
from users.models import EscortProfile
from wallet.models import ProviderReport


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ReportEntryRealWorkflowTest(APITestCase):
    def setUp(self):
        self.boss = make_user()
        self.staff = make_console_user(['order:view', 'order:dispatch', 'order:settle', 'report:view', 'report:audit'])
        self.boss_account = get_or_create_account_for_legacy_user(self.boss)
        self.staff_account = get_or_create_account_for_legacy_user(self.staff)
        self.staff.admin_membership.account = self.staff_account
        self.staff.admin_membership.save(update_fields=['account'])
        self.service = make_service(price=10000, billing_provider_count=1, commission_rate=20)
        make_wallet(self.boss, balance=100000)

    def login(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def entry(self, order_id):
        result = self.client.get('/api/orders/orders/')
        self.assertEqual(result.data['code'], 0, result.data)
        entry = next(item['report_entry'] for item in result.data['data'] if item['id'] == order_id)
        detail = self.client.get(f'/api/orders/orders/{order_id}/')
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual(detail.data['data']['report_entry'], entry)
        return entry

    def test_legacy_single_provider_can_create_report_from_owned_order(self):
        provider = make_provider()
        account = get_or_create_account_for_legacy_user(provider)
        EscortProfile.objects.filter(user=provider).update(account=account)
        self.login(self.boss_account)
        created = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        self.login(self.staff_account)
        assigned = self.client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        # Existing rows created before account backfill retain their legacy IDs.
        Order.objects.filter(pk=order_id).update(provider_account=None)
        Order.objects.get(pk=order_id).providers.update(provider_account=None)
        self.login(account)
        self.assertEqual(self.entry(order_id), {'available': True, 'reason': ''})
        result = self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(result.data['data']['order_id'], order_id)
        report_id = result.data['data']['id']
        started = self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart')
        self.assertEqual(started.data['code'], 0, started.data)
        completed = self.client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': screenshot()}, format='multipart')
        self.assertEqual(completed.data['code'], 0, completed.data)
        uploaded = self.client.post(f'/api/wallet/reports/{report_id}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
        self.assertEqual(uploaded.data['code'], 0, uploaded.data)
        submitted = self.client.post(f'/api/wallet/reports/{report_id}/submit/', {}, format='json')
        self.assertEqual(submitted.data['code'], 0, submitted.data)
        self.login(self.staff_account)
        approved = self.client.post(f'/api/admin/reports/{report_id}/approve/', {}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)

    def test_explicit_assignment_account_cannot_be_overridden_by_legacy_user(self):
        provider, stale = make_provider(), make_provider()
        account, stale_account = (get_or_create_account_for_legacy_user(user) for user in (provider, stale))
        EscortProfile.objects.filter(user=provider).update(account=account)
        EscortProfile.objects.filter(user=stale).update(account=stale_account)
        self.login(self.boss_account)
        created = self.client.post('/api/orders/create/', {
            'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4()),
        }, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order_id = created.data['data']['id']
        self.login(self.staff_account)
        assigned = self.client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id': account.pk}, format='json')
        self.assertEqual(assigned.data['code'], 0, assigned.data)
        Order.objects.filter(pk=order_id).update(provider=stale)
        Order.objects.get(pk=order_id).providers.update(provider=stale)
        self.login(stale_account)
        self.assertNotEqual(self.client.get(f'/api/orders/orders/{order_id}/').data['code'], 0)
        self.assertNotEqual(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json').data['code'], 0)

    def test_report_entry_tracks_real_transfer_and_preserves_history(self):
        for kept_percent in (0, 40):
            with self.subTest(kept_percent=kept_percent):
                providers = [make_provider(), make_provider()]
                accounts = [get_or_create_account_for_legacy_user(user) for user in providers]
                for user, account in zip(providers, accounts):
                    EscortProfile.objects.filter(user=user).update(account=account)
                self.login(self.boss_account)
                created = self.client.post('/api/orders/create/', {
                    'service_id': self.service.pk, 'dispatch_route': 'STAFF', 'client_request_id': str(uuid4()),
                }, format='json')
                self.assertEqual(created.data['code'], 0, created.data)
                order_id = created.data['data']['id']
                self.assertIsNone(created.data['data']['report_entry'])
                self.login(self.staff_account)
                assigned = self.client.post(f'/api/admin/orders/{order_id}/assign/', {'provider_id': accounts[0].pk}, format='json')
                self.assertEqual(assigned.data['code'], 0, assigned.data)
                self.login(accounts[0])
                self.assertEqual(self.entry(order_id), {'available': True, 'reason': ''})
                draft = self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json')
                self.assertEqual(draft.data['code'], 0, draft.data)
                report_id = draft.data['data']['id']
                self.login(self.staff_account)
                started = self.client.post(f'/api/admin/orders/{order_id}/start/', {}, format='json')
                self.assertEqual(started.data['code'], 0, started.data)
                transferred = self.client.post(f'/api/admin/orders/{order_id}/transfer/', {'transfers': [{
                    'old_provider_id': accounts[0].pk, 'new_provider_id': accounts[1].pk,
                    'split_type': 'PERCENT', 'split_value': kept_percent, 'penalty_amount': 0,
                }]}, format='json')
                self.assertEqual(transferred.data['code'], 0, transferred.data)
                self.login(accounts[0])
                entry = self.entry(order_id)
                opened = self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json')
                if kept_percent:
                    self.assertEqual(entry, {'available': True, 'reason': ''})
                    self.assertEqual(opened.data['code'], 0, opened.data)
                    self.assertEqual(opened.data['data']['id'], report_id)
                else:
                    self.assertEqual(entry, {'available': False, 'reason': '转单后本人无结算份额，无需提交报单'})
                    self.assertEqual(opened.data['msg'], entry['reason'])
                history = self.client.get('/api/wallet/reports/').data['data']
                old_report = next(item for item in history if item['id'] == report_id)
                self.assertEqual(old_report['can_edit'], bool(kept_percent))
                self.login(accounts[1])
                self.assertEqual(self.entry(order_id), {'available': True, 'reason': ''})
                new_report = self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json')
                self.assertEqual(new_report.data['code'], 0, new_report.data)
                self.login(self.staff_account)
                completed = self.client.post(f'/api/admin/orders/{order_id}/complete/', {}, format='json')
                self.assertEqual(completed.data['code'], 0, completed.data)
                self.login(accounts[0])
                self.assertEqual(self.entry(order_id)['available'], bool(kept_percent))
                self.login(accounts[1])
                self.assertEqual(self.entry(order_id), {'available': True, 'reason': ''})
                self.assertEqual(self.client.post('/api/wallet/reports/', {'order_id': order_id}, format='json').data['code'], 0)
                self.assertTrue(ProviderReport.objects.filter(order_id=order_id, provider_account=accounts[0]).exists())
