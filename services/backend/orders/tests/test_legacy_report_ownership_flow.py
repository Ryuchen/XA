"""Existing reports stay usable across the legacy-account migration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests import test_release_flow as flow
from orders.tests.factories import make_provider
from orders.models import Order
from wallet.models import ProviderReport


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class LegacyReportOwnershipFlowTest(APITransactionTestCase):
    setUp = flow.ReleaseFlowTest.setUp
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish

    def test_unmapped_historical_report_remains_visible_and_can_reach_review(self):
        order = self.new_order()
        report = self.finish(order)
        # Simulate an existing pre-backfill report; all service actions above
        # were performed through the real order APIs.
        ProviderReport.objects.filter(pk=report.pk).update(provider_account=None)
        self.login(self.provider)
        reports = self.ok(self.client.get('/api/wallet/reports/'))
        own = next(row for row in reports if row['id'] == report.pk)
        self.assertTrue(own['can_edit'])
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
        submitted = self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        self.assertEqual(submitted['status'], 'PENDING')
        self.login(self.admin)
        approved = self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        self.assertEqual(approved['status'], 'APPROVED')
        self.login(self.provider)
        own = next(row for row in self.ok(self.client.get('/api/wallet/reports/')) if row['id'] == report.pk)
        self.assertEqual((own['status'], own['settlement_status']), ('APPROVED', 'WAITING'))
        self.assertEqual(ProviderReport.objects.filter(order=order, provider=self.provider).count(), 1)

    def test_explicit_other_account_is_not_overridden_by_legacy_user(self):
        order = self.new_order()
        report = self.finish(order)
        other = make_provider()
        other_account = get_or_create_account_for_legacy_user(other)
        ProviderReport.objects.filter(pk=report.pk).update(provider_account=other_account)
        self.login(self.provider)
        self.assertFalse(any(row['id'] == report.pk for row in self.ok(self.client.get('/api/wallet/reports/'))))
        created = self.client.post('/api/wallet/reports/', {'order_id': order.pk}, format='json')
        self.assertEqual((created.status_code, created.data['code']), (409, 409))
        upload = self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                  {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart')
        self.assertNotEqual(upload.data['code'], 0)
        report.refresh_from_db()
        self.assertEqual(report.provider_account_id, other_account.pk)

    def test_historical_team_report_keeps_progress_and_reuses_the_same_draft(self):
        partner = make_provider(display_name='搭档')
        self.accounts[partner.pk] = get_or_create_account_for_legacy_user(partner)
        partner.escort_profile.account = self.accounts[partner.pk]
        partner.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[partner.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'legacy-team-report-ownership',
        }, format='json'))
        self.login(self.provider)
        created = self.ok(self.client.post('/api/wallet/reports/', {'order_id': order['id']}, format='json'))
        report_id = created['id']
        ProviderReport.objects.filter(pk=report_id).update(provider_account=self.accounts[partner.pk])
        conflict = self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart')
        self.assertEqual((conflict.status_code, conflict.data['code']), (409, 409))
        self.assertFalse(ProviderReport.objects.get(pk=report_id).entry_image)
        ProviderReport.objects.filter(pk=report_id).update(provider_account=None)
        self.assertTrue(any(row['id'] == report_id for row in self.ok(self.client.get('/api/wallet/reports/'))))
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(pk=report_id)
        self.assertEqual(report.provider_account_id, self.accounts[self.provider.pk].pk)
        # A mismatched old account must not credit this screenshot to the teammate.
        ProviderReport.objects.filter(pk=report_id).update(provider_account=self.accounts[partner.pk])
        self.login(self.boss)
        conflict_progress = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))['team_progress']
        self.assertEqual(conflict_progress['submitted_count'], 0)
        self.login(partner)
        conflict = self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart')
        self.assertEqual((conflict.status_code, conflict.data['code']), (409, 409))
        self.assertEqual(Order.objects.get(pk=order['id']).status, 'GRABBED')
        ProviderReport.objects.filter(pk=report_id).update(provider_account=None)
        self.login(self.provider)
        current = self.ok(self.client.get(f"/api/orders/orders/{order['id']}/"))
        submitted = next(row for row in current['team_progress']['members'] if row['id'] == self.accounts[self.provider.pk].pk)
        self.assertTrue(submitted['submitted'])
        self.assertNotIn('START', current['allowed_actions'])
        self.login(partner)
        self.ok(self.client.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order['id']).status, 'IN_SERVICE')
        for provider in (self.provider, partner):
            self.login(provider)
            self.ok(self.client.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': flow.screenshot()}, format='multipart'))
        self.assertEqual(Order.objects.get(pk=order['id']).status, 'COMPLETED')
        report.refresh_from_db()
        self.assertEqual(report.provider_account_id, self.accounts[self.provider.pk].pk)
        self.assertEqual(ProviderReport.objects.filter(order_id=order['id'], provider=self.provider).count(), 1)
        for provider in (self.provider, partner):
            self.login(provider)
            own = ProviderReport.objects.get(order_id=order['id'], provider=provider)
            self.ok(self.client.post(f'/api/wallet/reports/{own.pk}/images/',
                                     {'kind': 'RESULT', 'image': flow.screenshot()}, format='multipart'))
            self.assertEqual(self.ok(self.client.post(f'/api/wallet/reports/{own.pk}/submit/', {}, format='json'))['status'], 'PENDING')
        self.login(self.admin)
        for own in ProviderReport.objects.filter(order_id=order['id']):
            self.assertEqual(self.ok(self.client.post(f'/api/admin/reports/{own.pk}/approve/', {}, format='json'))['status'], 'APPROVED')

    @skipUnless(connection.vendor == 'mysql', 'Requires real MySQL order row locks')
    def test_two_team_members_submit_each_stage_concurrently_once(self):
        partner = make_provider(display_name='搭档')
        self.accounts[partner.pk] = get_or_create_account_for_legacy_user(partner)
        partner.escort_profile.account = self.accounts[partner.pk]
        partner.escort_profile.save(update_fields=['account'])
        self.service.billing_provider_count = 2
        self.service.save(update_fields=['billing_provider_count'])
        self.ok(self.recharge())
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {
            'service_id': self.service.pk,
            'provider_ids': [self.accounts[self.provider.pk].pk, self.accounts[partner.pk].pk],
            'support_contact_id': self.card.pk, 'client_request_id': 'team-evidence-concurrent',
        }, format='json'))['id']

        def stage(action, field):
            barrier = Barrier(2)
            def submit(provider):
                close_old_connections()
                try:
                    client = APIClient()
                    client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.accounts[provider.pk])[0])
                    reached_lock = False
                    def synchronize(execute, sql, params, many, context):
                        nonlocal reached_lock
                        if not reached_lock and 'orders_order' in sql and 'FOR UPDATE' in sql.upper():
                            reached_lock = True
                            barrier.wait(timeout=10)
                        return execute(sql, params, many, context)
                    with connection.execute_wrapper(synchronize):
                        response = client.post(f'/api/orders/orders/{order_id}/{action}/',
                                               {field: flow.screenshot()}, format='multipart')
                    return response.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result(timeout=30) for future in
                           [pool.submit(submit, self.provider), pool.submit(submit, partner)]]
            self.assertEqual([row['code'] for row in results], [0, 0], results)

        stage('start', 'entry_image')
        self.assertEqual(Order.objects.get(pk=order_id).status, 'IN_SERVICE')
        stage('complete', 'completion_image')
        order = Order.objects.get(pk=order_id)
        self.assertEqual(order.status, 'COMPLETED')
        self.assertEqual(order.status_logs.filter(to_status='IN_SERVICE').count(), 1)
        self.assertEqual(order.status_logs.filter(to_status='COMPLETED').count(), 1)
        self.assertEqual(ProviderReport.objects.filter(order_id=order_id).count(), 2)
