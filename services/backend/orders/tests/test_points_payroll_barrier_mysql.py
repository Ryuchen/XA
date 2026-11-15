"""Real points agreement and payroll barrier against an isolated MySQL server."""
import os
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest import skipUnless

from django.conf import settings
from django.db import close_old_connections, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.payroll_event_barrier import lock_payroll_events
from orders.models import NegotiationProposal, Order, OrderProvider, OrderRefund
from orders.points_checkout_test import require_isolated_checkout
from orders.tests.factories import make_console_user
from orders.tests import test_points_checkout_integration as checkout_fixture
from orders.tests.test_release_flow import screenshot
from stores.context import store_context
from stores.db import transaction


@skipUnless('store_b' in settings.DATABASES and
            os.environ.get('DJANGO_SETTINGS_MODULE') == 'config.ordinary_followup_mysql_test_settings',
            'Requires the dedicated isolated two-store MySQL runner')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    POINTS_REDEMPTION_PREVIEW_ENABLED=True, DISCOUNT_OPTIONS_PREVIEW_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
    SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, BOSS_FUNDING_SELECTION_ENABLED=True,
    SERVICE_POINTS_CHECKOUT_TESTING=True, SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
    REFUND_DEPOSIT_DEFICIT_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    NEGOTIATION_REFUND_EXECUTION_ENABLED=True, PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED=True,
    LICENSE_ACCOUNT_ADMISSION_ENABLED=False, LEGACY_FORCE_AUTH_COMPAT=False,
    CELERY_TASK_ALWAYS_EAGER=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class PointsPayrollBarrierMySQLTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = checkout_fixture.PointsFundingPlatformRecoveryTests.setUp
    payload = checkout_fixture.PointsFundingPlatformRecoveryTests.payload
    quoted_payload = checkout_fixture.PointsFundingPlatformRecoveryTests.quoted_payload
    create_assigned_points_order = checkout_fixture.PointsFundingPlatformRecoveryTests.create_assigned_points_order
    verify = checkout_fixture.PointsFundingPlatformRecoveryTests.verify
    fund = checkout_fixture.PointsFundingPlatformRecoveryTests.fund

    def test_agreed_points_refund_waits_before_order_lock_and_posts_once(self):
        with TemporaryDirectory(prefix='xa-points-gate-') as media, override_settings(MEDIA_ROOT=media), store_context(self.second):
            require_isolated_checkout()
            order = Order.objects.get(pk=self.order.pk)
            assignment = OrderProvider.objects.get(order=order)
            player = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
            boss = APIClient(HTTP_HOST='xa.esports.club',
                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image':screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff
            admin.admin_membership.save(update_fields=['account'])
            client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
            opened = client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order.pk,
                'reason':'积分协商退款', 'affected_provider_account_ids':[assignment.provider_account_id],
                'client_request_id':'points-gate-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            proposed = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id':'points-gate-proposal', 'expected_version':0, 'refund_units':1000,
                'provider_terms':[{'account_id':assignment.provider_account_id, 'retained_units':3800}],
                'explanation':'积分和店币按原构成退回'}, format='json')
            self.assertEqual(proposed.data['code'], 0, proposed.data)
            proposal_id = proposed.data['data']['id']
            user_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            for actor in (boss, player):
                accepted = actor.post(user_url, {'proposal_id':proposal_id, 'accepted':True}, format='json')
                self.assertEqual(accepted.data['code'], 0, accepted.data)
            order.refresh_from_db()
            accepted_proposal = NegotiationProposal.objects.get(pk=proposal_id)
            self.assertTrue(order.in_service_at and order.service_clock_version is not None
                and order.service_aftersale_hours is not None and order.service_freeze_hours is not None
                and order.service_deposit_percent is not None
                and order.in_service_at <= accepted_proposal.accepted_at <= timezone.now()
                and order.status == Order.Status.IN_SERVICE and order.settlement_mode == Order.SettlementMode.REPORT_APPROVAL, 
                {'in_service_at':order.in_service_at, 'accepted_at':accepted_proposal.accepted_at,
                 'now':timezone.now(), 'clock':order.service_clock_version,
                 'aftersale':order.service_aftersale_hours, 'freeze':order.service_freeze_hours,
                 'deposit':order.service_deposit_percent, 'status':order.status, 'mode':order.settlement_mode})
            url = f'/api/admin/aftersales/{case_id}/negotiation-execution/'
            payload = {'proposal_id':proposal_id, 'client_request_id':'points-gate-post'}
            def post():
                close_old_connections()
                try:
                    with store_context(self.second):
                        response = client.post(url, payload, format='json')
                        return response.status_code, response.data
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    lock_payroll_events()
                    pending = pool.submit(post)
                    deadline, found = monotonic() + 10, False
                    while monotonic() < deadline:
                        with connections['store_b'].cursor() as cursor:
                            cursor.execute('SHOW FULL PROCESSLIST')
                            found = any(row[3] == connections['store_b'].settings_dict['NAME'] and row[4] == 'Query'
                                and 'CONSOLE_PAYROLLEVENTBARRIER' in str(row[7] or '').upper()
                                and 'LOCK IN SHARE MODE' in str(row[7] or '').upper() for row in cursor.fetchall())
                        if found:
                            break
                        if pending.done():
                            self.fail(f'Points refund returned before barrier: {pending.result()}')
                        sleep(0.05)
                    self.assertTrue(found, 'Points refund did not wait for payroll barrier')
                    Order.objects.select_for_update(nowait=True).get(pk=order.pk)
                    self.assertFalse(OrderRefund.objects.filter(order=order).exists())
                status, data = pending.result(timeout=30)
            self.assertEqual((status, data['code']), (200, 0), data)
            self.assertEqual((data['data']['refund_coin_units'], data['data']['refund_points_hundredths']), (660, 1666))
            self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
            replay = client.post(url, payload, format='json')
            self.assertEqual(replay.data['code'], 0, replay.data)
            self.assertEqual(OrderRefund.objects.filter(order=order).count(), 1)
