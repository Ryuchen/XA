from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from console.reservation_deadlines import save_deadlines
from engagement.models import MemberProfile, PointFundingLot
from engagement.services import config
from orders.models import Order, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortProfile
from wallet.models import Wallet
from wallet.services import get_wallet


def seed_principal_balance(wallet, amount):
    wallet.balance = amount
    wallet.save(update_fields=['balance'])
    PointFundingLot.objects.create(account_id=wallet.account_id,
        source_key=f'isolated-reservation-principal-{wallet.account_id}',
        kind=PointFundingLot.Kind.PREPAID, remaining=amount)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False, ALLOWED_HOSTS=['*'],
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_REDEMPTION_PREVIEW_ENABLED=True,
    POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ReservationPointsStoresTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_never_allow_cross_store_points_receipt(self):
        start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                config()
                save_deadlines(30, 60, 0, lambda *_: None)
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                user = make_user()
                boss = get_or_create_account_for_legacy_user(user)
                link_legacy_relations(user, boss)
                wallet = get_wallet(user=user, account=boss)
                seed_principal_balance(wallet, 20000)
                MemberProfile.objects.create(account=boss, points_balance=10000)
                player_user = make_provider(deposit_mode='EARNINGS')
                player = get_or_create_account_for_legacy_user(player_user)
                link_legacy_relations(player_user, player)
                EscortProfile.objects.filter(user=player_user).update(account=player)
                service = make_service(price=10000, commission_rate=20)
                boss_token, player_token, service_id, player_id = (issue_account_tokens(boss)[0],
                    issue_account_tokens(player)[0], service.pk, player.pk)
            boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + boss_token)
            player_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + player_token)
            response = boss_client.post('/api/orders/reservations/', {
                'provider_account_id': player_id, 'service_id': service_id,
                'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
            }, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            booking_id = response.data['data']['id']
            path = f'/api/orders/reservations/{booking_id}/'
            self.assertEqual(player_client.post(path + 'confirm/', {}, format='json').data['code'], 0)
            result = boss_client.get(path + 'quote/', {'preview_points_hundredths': 10000})
            self.assertEqual(result.data['code'], 0, result.data)
            quote = result.data['data']
            rows.append((store, boss_client, booking_id, boss.pk, quote))
        self.assertEqual(rows[0][2:4], rows[1][2:4])
        path = f'/api/orders/reservations/{rows[1][2]}/convert/'
        payload = {'discount_selection_token': rows[1][4]['discount_selection_token'],
                   'points_checkout_test_token': rows[0][4]['points_preview']['checkout_test_token'],
                   'expected_amount': 6000, 'client_request_id': 'cross-store-points'}
        self.assertNotEqual(rows[1][1].post(path, payload, format='json').data.get('code'), 0)
        with store_context(rows[1][0]):
            self.assertFalse(Order.objects.exists())
            self.assertEqual(Wallet.objects.get(account_id=rows[1][3]).balance, 20000)
            self.assertEqual(PointFundingLot.objects.get(account_id=rows[1][3]).remaining, 20000)
            self.assertEqual(MemberProfile.objects.get(account_id=rows[1][3]).points_balance, 10000)
        payload['points_checkout_test_token'] = rows[1][4]['points_preview']['checkout_test_token']
        self.assertEqual(rows[1][1].post(path, payload, format='json').data['code'], 0)
        with store_context(rows[0][0]):
            self.assertFalse(Order.objects.exists())
            self.assertEqual(Reservation.objects.get().status, 'CONFIRMED')
            self.assertEqual(Wallet.objects.get(account_id=rows[0][3]).balance, 20000)

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True)
    def test_admin_refunds_only_own_store_despite_matching_order_ids(self):
        from orders.models import OrderPointsRefund
        start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        rows = []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                config()
                save_deadlines(30, 60, 0, lambda *_: None)
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                boss_user = make_user()
                boss = get_or_create_account_for_legacy_user(boss_user)
                link_legacy_relations(boss_user, boss)
                wallet = get_wallet(user=boss_user, account=boss)
                seed_principal_balance(wallet, 20000)
                MemberProfile.objects.create(account=boss, points_balance=10000)
                provider_user = make_provider(deposit_mode='EARNINGS')
                provider = get_or_create_account_for_legacy_user(provider_user)
                link_legacy_relations(provider_user, provider)
                EscortProfile.objects.filter(user=provider_user).update(account=provider)
                service = make_service(price=10000, commission_rate=20)
                admin = make_console_user(['order:view', 'order:refund'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                tokens = [issue_account_tokens(account)[0] for account in (boss, provider, staff)]
                boss_id, provider_id, service_id = boss.pk, provider.pk, service.pk
            boss_client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + tokens[0])
            player = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + tokens[1])
            created = boss_client.post('/api/orders/reservations/', {'provider_account_id': provider_id,
                'service_id': service_id, 'start_time': start.isoformat(),
                'end_time': (start+timedelta(hours=1)).isoformat()}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            path = f"/api/orders/reservations/{created.data['data']['id']}/"
            self.assertEqual(player.post(path+'confirm/', {}, format='json').data['code'], 0)
            quoted = boss_client.get(path+'quote/', {'preview_points_hundredths': 10000})
            self.assertEqual(quoted.data['code'], 0, quoted.data)
            quote = quoted.data['data']
            paid = boss_client.post(path+'convert/', {'discount_selection_token': quote['discount_selection_token'],
                'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
                'expected_amount': 6000, 'client_request_id': 'same-store-payment'}, format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            rows.append((store, boss_id, paid.data['data']['order']['id'], tokens[2]))
        self.assertEqual(rows[0][1:3], rows[1][1:3])
        admin_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + rows[1][3])
        refund = admin_client.post(f'/api/admin/orders/{rows[1][2]}/refund/', {
            'reason': '取消本店预约', 'client_request_id': 'same-store-refund'}, format='json')
        self.assertEqual(refund.data['code'], 0, refund.data)
        for store, boss_id, order_id, token in rows:
            with store_context(store):
                self.assertEqual(Order.objects.get(pk=order_id).payment_status,
                                 'REFUNDED' if store.slug == 'xinan' else 'PAID')
                expected_balance = 20000 if store.slug == 'xinan' else 14000
                self.assertEqual(Wallet.objects.get(account_id=boss_id).balance, expected_balance)
                self.assertEqual(PointFundingLot.objects.get(account_id=boss_id).remaining, expected_balance)
                self.assertEqual(OrderPointsRefund.objects.filter(redemption__order_id=order_id).count(),
                                 1 if store.slug == 'xinan' else 0)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False, ALLOWED_HOSTS=['*'],
    PLATFORM_IDENTITY_ENABLED=True, PLATFORM_PASS_REPAYMENT_ENABLED=True,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_REDEMPTION_PREVIEW_ENABLED=True,
    POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
    SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_SETTLEMENT_TESTING=True,
    SERVICE_POINTS_CONSUMPTION_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
    REQUIRE_ORDER_EVIDENCE_IMAGES=True)
class ReservationPointsFinanceTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True,
        SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True)
    def test_cancel_releases_verified_funding_and_receipt_can_be_reused(self):
        self._cancel_funding_case(usage=False)

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True,
        SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
        RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True, RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True,
        SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING=True)
    def test_cancel_release_is_recorded_in_shared_receipt_ledger(self):
        self._cancel_funding_case(usage=True)

    def _cancel_funding_case(self, *, usage):
        from orders.models import ReservationPointsCancelFundingEvidence, ReservationPointsCancelFundingRelease
        from stores import test_platform_pass_repayment as platform_tests
        from stores.db import transaction

        platform_tests.PlatformPassRepaymentTests.setUp(self)
        pass_refund = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)[0] if usage else None
        start = (timezone.now()+timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        with store_context(self.second):
            config()
            save_deadlines(30, 60, 0, lambda *_: None)
            save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                              max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
            boss_user = make_user()
            boss = get_or_create_account_for_legacy_user(boss_user)
            link_legacy_relations(boss_user, boss)
            wallet = get_wallet(user=boss_user, account=boss)
            seed_principal_balance(wallet, 40000)
            MemberProfile.objects.create(account=boss, points_balance=20000)
            provider_user = make_provider(deposit_mode='EARNINGS')
            provider = get_or_create_account_for_legacy_user(provider_user)
            link_legacy_relations(provider_user, provider)
            EscortProfile.objects.filter(user=provider_user).update(account=provider)
            service = make_service(price=10000, commission_rate=20)
            admin = make_console_user(['order:view', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            boss_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss)[0])
            player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(provider)[0])
            reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
            provider_id, service_id, boss_id = provider.pk, service.pk, boss.pk

        def booking(at, request_id):
            created = boss_client.post('/api/orders/reservations/', {'provider_account_id': provider_id,
                'service_id': service_id, 'start_time': at.isoformat(),
                'end_time': (at+timedelta(hours=1)).isoformat()}, format='json')
            self.assertEqual(created.data['code'], 0, created.data)
            path = f"/api/orders/reservations/{created.data['data']['id']}/"
            self.assertEqual(player.post(path+'confirm/', {}, format='json').data['code'], 0)
            quote = boss_client.get(path+'quote/', {'preview_points_hundredths': 10000}).data['data']
            paid = boss_client.post(path+'convert/', {'discount_selection_token': quote['discount_selection_token'],
                'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
                'expected_amount': 6000, 'client_request_id': request_id}, format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            return paid.data['data']['order']['id']

        first = booking(start, 'funded-reservation-1')
        registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan', {
            **self.data, 'amount': 2200 if usage else 2000, 'external_reference': 'RESERVATION-CANCEL-FUND-001'})
        self.assertEqual(registered.status_code, 200, registered.data)
        claim = registered.data['data']['claim_id']
        verified = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
        self.assertEqual(verified.status_code, 200, verified.data)
        if usage:
            from stores.models import PlatformPassRepaymentClaim
            from users.receipt_usage_ledger import initialize_empty_locked
            with store_context(self.second), transaction.atomic():
                receipt_id = PlatformPassRepaymentClaim.objects.using('default').get(pk=claim).tenant_receipt_id
                initialize_empty_locked(receipt_id)
            allocated_pass = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': pass_refund, 'amount': 200})
            self.assertEqual(allocated_pass.status_code, 200, allocated_pass.data)
        funding_url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
        def fund(order_id, amount):
            return self.client.post(funding_url, {'request_id': str(uuid4()), 'order_id': order_id, 'amount': amount},
                format='json', HTTP_AUTHORIZATION='Bearer '+self.token)
        self.assertEqual(fund(first, 2000).status_code, 200)
        preview = reviewer.get(f'/api/admin/orders/{first}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        self.assertEqual(preview.data['data']['released_funding_units'], 2000)
        payload = {'reason': '预约取消', 'client_request_id': 'funded-reservation-refund'}
        with patch('orders.reservation_cancel_funding_test.ReservationPointsCancelFundingRelease.objects.bulk_create',
                   side_effect=RuntimeError('injected evidence write failure')):
            with self.assertRaises(RuntimeError):
                reviewer.post(f'/api/admin/orders/{first}/refund/', payload, format='json')
        with store_context(self.second):
            self.assertEqual(Order.objects.get(pk=first).payment_status, 'PAID')
            self.assertEqual(Wallet.objects.get(account_id=boss_id).balance, 34000)
            self.assertEqual(MemberProfile.objects.get(account_id=boss_id).points_balance, 10000)
            self.assertFalse(ReservationPointsCancelFundingEvidence.objects.exists())
        if usage:
            from users.models import PassReceiptUsageEvent
            original_create = PassReceiptUsageEvent.objects.create
            def fail_release(**kwargs):
                if kwargs.get('kind') == 'RELEASE':
                    raise RuntimeError('release event failure')
                return original_create(**kwargs)
            with patch('users.receipt_usage_ledger.PassReceiptUsageEvent.objects.create', side_effect=fail_release):
                with self.assertRaisesMessage(RuntimeError, 'release event failure'):
                    reviewer.post(f'/api/admin/orders/{first}/refund/', payload, format='json')
            with store_context(self.second):
                self.assertFalse(ReservationPointsCancelFundingEvidence.objects.exists())
                self.assertFalse(PassReceiptUsageEvent.objects.filter(kind='RELEASE').exists())
                self.assertEqual(Order.objects.get(pk=first).payment_status, 'PAID')
        refunded = reviewer.post(f'/api/admin/orders/{first}/refund/', payload, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        replay = reviewer.post(f'/api/admin/orders/{first}/refund/', payload, format='json')
        self.assertEqual(replay.data['code'], 0, replay.data)
        with store_context(self.second):
            self.assertEqual(Order.objects.get(pk=first).payment_status, 'REFUNDED')
            self.assertEqual(Wallet.objects.get(account_id=boss_id).balance, 40000)
            self.assertEqual(MemberProfile.objects.get(account_id=boss_id).points_balance, 20000)
            self.assertEqual(ReservationPointsCancelFundingEvidence.objects.count(), 1)
            self.assertEqual(ReservationPointsCancelFundingRelease.objects.get().amount, 2000)
            if usage:
                from users.receipt_usage_sources import verify_allocations_locked
                from django.db import connections
                from django.test.utils import CaptureQueriesContext
                from stores.context import current_database
                with transaction.atomic(), CaptureQueriesContext(connections[current_database()]) as queries:
                    state = verify_allocations_locked(receipt_id)
                    self.assertEqual((state.pass_units, state.order_units, state.released_units), (200, 2000, 2000))
                self.assertFalse(any('\"orders_order\"' in row['sql'].lower() for row in queries.captured_queries))
        result = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
        self.assertEqual(result['status'], 'OK', result)
        self.assertEqual((result['claims'][0]['receipt']['allocated_amount'],
                          result['claims'][0]['receipt']['unallocated_amount']), (200 if usage else 0, 2000))
        second = booking(start+timedelta(hours=2), 'funded-reservation-2')
        self.assertEqual(fund(second, 2000).status_code, 200)
        with store_context(self.second), transaction.atomic():
            ReservationPointsCancelFundingEvidence.objects.update(evidence_hash='0'*64)
            broken = platform_tests.PlatformPassRepaymentTests.reconciliation(self).data['data']
            self.assertEqual(broken['status'], 'REVIEW', broken)
            self.assertIsNone(broken['claims'][0]['receipt']['unallocated_amount'])
            transaction.set_rollback(True)

    def test_platform_receipt_funds_booking_before_payout_and_vip_confirmation(self):
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsSettlement
        from orders.tests.test_release_flow import screenshot
        from stores import test_platform_pass_repayment as platform_tests
        from wallet.models import ProviderReport, Transaction

        platform_tests.PlatformPassRepaymentTests.setUp(self)
        platform_client = self.client
        start = (timezone.now()+timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
        with TemporaryDirectory(prefix='xa-reservation-finance-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                config()
                save_deadlines(30, 60, 0, lambda *_: None)
                save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                                  max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
                boss_user = make_user()
                boss = get_or_create_account_for_legacy_user(boss_user)
                link_legacy_relations(boss_user, boss)
                wallet = get_wallet(user=boss_user, account=boss)
                seed_principal_balance(wallet, 20000)
                MemberProfile.objects.create(account=boss, points_balance=10000)
                provider_user = make_provider(deposit_mode='EARNINGS')
                provider = get_or_create_account_for_legacy_user(provider_user)
                link_legacy_relations(provider_user, provider)
                EscortProfile.objects.filter(user=provider_user).update(account=provider)
                service = make_service(price=10000, commission_rate=20)
                boss_client = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss)[0])
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider)[0])
                response = boss_client.post('/api/orders/reservations/', {'provider_account_id': provider.pk,
                    'service_id': service.pk, 'start_time': start.isoformat(),
                    'end_time': (start+timedelta(hours=1)).isoformat()}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                path = f"/api/orders/reservations/{response.data['data']['id']}/"
                self.assertEqual(player.post(path+'confirm/', {}, format='json').data['code'], 0)
                quoted = boss_client.get(path+'quote/', {'preview_points_hundredths': 10000})
                self.assertEqual(quoted.data['code'], 0, quoted.data)
                quote = quoted.data['data']
                paid = boss_client.post(path+'convert/', {'discount_selection_token': quote['discount_selection_token'],
                    'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
                    'expected_amount': 6000, 'client_request_id': 'finance-reservation-pay'}, format='json')
                self.assertEqual(paid.data['code'], 0, paid.data)
                order = Order.objects.get(pk=paid.data['data']['order']['id'])
                for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                    result = player.post(f'/api/orders/orders/{order.pk}/{action}/', {field: screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                report = ProviderReport.objects.get(order=order)
                uploaded = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                       {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
                self.assertEqual(uploaded.data['code'], 0, uploaded.data)
                self.assertEqual(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json').data['code'], 0)
                admin = make_console_user(['report:view', 'report:audit'])
                staff = get_or_create_account_for_legacy_user(admin)
                admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
                reviewer = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                approved = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
                self.assertEqual(approved.data['code'], 0, approved.data)
                report.refresh_from_db()
                mature = report.submitted_at + timedelta(hours=73)
                with patch('django.utils.timezone.now', return_value=mature):
                    self.assertFalse(process_order(order.pk))
                self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())
            self.client = platform_client
            registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan', {
                **self.data, 'amount': 2000, 'external_reference': 'RESERVATION-FUND-001'})
            self.assertEqual(registered.status_code, 200, registered.data)
            claim = registered.data['data']['claim_id']
            verified = platform_tests.PlatformPassRepaymentTests.action(self, claim, 'verify', {'request_id': str(uuid4())})
            self.assertEqual(verified.status_code, 200, verified.data)
            funding_url = f'/api/admin/platform/pass-refund-repayments/xinan/{claim}/points-allocation'
            first = self.client.post(funding_url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1990},
                                     format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(first.status_code, 200, first.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=mature):
                self.assertFalse(process_order(order.pk))
                self.assertFalse(Transaction.objects.filter(order_id=order.pk, tx_type='INCOME').exists())
                self.assertFalse(ConsumptionConfirmation.objects.filter(order_id=order.pk).exists())
            final = self.client.post(funding_url, {'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 10},
                                     format='json', HTTP_AUTHORIZATION='Bearer ' + self.token)
            self.assertEqual(final.status_code, 200, final.data)
            with store_context(self.second), patch('django.utils.timezone.now', return_value=mature):
                self.assertTrue(process_order(order.pk))
                order = Order.objects.get(pk=order.pk)
                self.assertIsNotNone(order.settled_at)
                self.assertEqual(OrderPointsSettlement.objects.get(order=order).funding_units, 2000)
                self.assertEqual(ConsumptionConfirmation.objects.get(order=order).amount, 6000)
                self.assertEqual(list(Transaction.objects.filter(order=order, tx_type='INCOME').values_list('amount', flat=True)), [8000])
