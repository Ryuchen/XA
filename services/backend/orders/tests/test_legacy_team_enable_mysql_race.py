"""Real row-lock race between a legacy start and enabling team evidence."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from wallet.models import ProviderReport


@skipUnless(connection.vendor == 'mysql', 'Requires real MySQL row locks')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class LegacyTeamEnableMySQLRaceTest(TransactionTestCase):
    def test_report_creation_waits_for_staff_workflow_change(self):
        media = tempfile.TemporaryDirectory(); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff, first, second = make_user(), make_console_user(['order:view', 'order:dispatch']), make_provider(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, first, second)]
        for user, account in zip((boss, staff, first, second), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for user, account in zip((first, second), accounts[2:]):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(price=10000, billing_provider_count=2)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        staff_client = APIClient(); staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        created = staff_client.post('/api/admin/orders/dispatch/', {'customer_id': accounts[0].pk, 'service_id': service.pk,
            'escort_mode': 'DOUBLE', 'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'legacy-team-report-lock'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_id = created['data']['id']
        Order.objects.filter(pk=order_id).update(requires_boss_selection=False)

        def create_report():
            close_old_connections()
            try:
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
                return client.post('/api/wallet/reports/', {'order_id': order_id}, format='json').data
            finally:
                close_old_connections()

        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=order_id)
                    future = pool.submit(create_report)
                    deadline = monotonic() + 10
                    while monotonic() < deadline:
                        with observer.cursor() as cursor:
                            cursor.execute('SHOW ENGINE INNODB STATUS')
                            status = cursor.fetchone()[2]
                        if 'LOCK WAIT' in status and '`orders_order`' in status:
                            break
                        sleep(.02)
                    else:
                        self.fail('report creation did not wait for the order lock')
                    activated = staff_client.post(f'/api/admin/orders/{order_id}/enable-team-evidence/',
                                                  {'confirmed': True}, format='json').data
                    self.assertEqual(activated['code'], 0, activated)
                report = future.result(timeout=20)
            self.assertEqual(report['code'], 0, report)
            provider = APIClient(); provider.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
            upload = provider.post(f"/api/wallet/reports/{report['data']['id']}/images/",
                                   {'kind': 'ENTRY', 'image': screenshot()}, format='multipart').data
            self.assertEqual(upload['code'], 400, upload)
            self.assertIn('订单页', upload['msg'])
            for index, token in enumerate(tokens[2:]):
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                response = client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart').data
                self.assertEqual(response['code'], 0, response)
                self.assertEqual(Order.objects.get(pk=order_id).status, 'GRABBED' if index == 0 else 'IN_SERVICE')
        finally:
            observer.close()

    def test_start_waiting_on_lock_observes_enabled_team_workflow(self):
        media = tempfile.TemporaryDirectory(); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, staff, first, second = make_user(), make_console_user(['order:view', 'order:dispatch']), make_provider(), make_provider()
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, first, second)]
        for user, account in zip((boss, staff, first, second), accounts):
            link_legacy_relations(user, account)
        staff.admin_membership.account = accounts[1]; staff.admin_membership.save(update_fields=['account'])
        for user, account in zip((first, second), accounts[2:]):
            user.escort_profile.account = account; user.escort_profile.save(update_fields=['account'])
        make_wallet(boss, balance=100000)
        service = make_service(price=10000, billing_provider_count=2)
        tokens = [issue_account_tokens(account)[0] for account in accounts]
        staff_client = APIClient(); staff_client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[1])
        created = staff_client.post('/api/admin/orders/dispatch/', {'customer_id': accounts[0].pk, 'service_id': service.pk,
            'escort_mode': 'DOUBLE', 'providers': [{'provider_id': account.pk} for account in accounts[2:]],
            'client_request_id': 'legacy-team-row-lock'}, format='json').data
        self.assertEqual(created['code'], 0, created)
        order_id = created['data']['id']
        # This is a persisted historical order state, not a replacement for the
        # start, activation, evidence, or accounting implementations under test.
        Order.objects.filter(pk=order_id).update(requires_boss_selection=False)

        def start():
            close_old_connections()
            try:
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + tokens[2])
                return client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart').data
            finally:
                close_old_connections()

        observer = connections['default'].copy()
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=order_id)
                    future = pool.submit(start)
                    deadline = monotonic() + 10
                    while monotonic() < deadline:
                        with observer.cursor() as cursor:
                            cursor.execute('SHOW ENGINE INNODB STATUS')
                            status = cursor.fetchone()[2]
                        if 'LOCK WAIT' in status and '`orders_order`' in status:
                            break
                        sleep(.02)
                    else:
                        self.fail('provider start did not wait for the order lock')
                    activated = staff_client.post(f'/api/admin/orders/{order_id}/enable-team-evidence/',
                                                  {'confirmed': True}, format='json').data
                    self.assertEqual(activated['code'], 0, activated)
                stale = future.result(timeout=20)
            self.assertEqual(stale['code'], 400, stale)
            self.assertIn('请刷新', stale['msg'])
            self.assertEqual(Order.objects.get(pk=order_id).status, 'GRABBED')
            self.assertFalse(ProviderReport.objects.filter(order_id=order_id).exists())
            for index, token in enumerate(tokens[2:]):
                client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                response = client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart').data
                self.assertEqual(response['code'], 0, response)
                self.assertEqual(Order.objects.get(pk=order_id).status, 'GRABBED' if index == 0 else 'IN_SERVICE')
        finally:
            observer.close()
