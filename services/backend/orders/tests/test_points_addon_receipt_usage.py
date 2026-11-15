from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from orders.models import (GameCategory, Order, OrderAddonFundingRelease, OrderAddonFundingReleaseRun,
                           OrderProvider, ServiceAddonConfiguration, ServiceAddonPriceOption)
from orders.tests import test_points_recharge_closure as base
from orders.tests.factories import make_console_user, make_service
from orders.tests.test_release_flow import screenshot
from stores import test_platform_pass_repayment as platform_tests
from stores.context import store_context
from stores.db import transaction
from users.models import PassReceiptUsageEvent
from users.receipt_usage_ledger import initialize_empty_locked
from users.receipt_usage_sources import verify_allocations_locked
from wallet.services import get_wallet


@override_settings(**base.PointsRealRechargeClosureTest._overridden_settings)
@override_settings(ADDON_PAYMENT_INTERNAL_TESTING=True, ADDON_ORDER_FUNDING_INTERNAL_TESTING=True,
    ADDON_REFUND_EXECUTION_INTERNAL_TESTING=True, RECEIPT_USAGE_LEDGER_INTERNAL_TESTING=True,
    RECEIPT_USAGE_ALLOCATIONS_INTERNAL_TESTING=True, SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING=True,
    SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING=True, SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING=True,
    SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True, SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TESTING=True, SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True, SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True,
    SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True,
    SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True, NEGOTIATION_PROPOSAL_ENABLED=True,
    NEGOTIATION_REFUND_EXECUTION_ENABLED=True)
@skipUnless('store_b' in settings.DATABASES, 'requires two isolated stores')
class PointsAddonReceiptUsageTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    setUp = base.PointsRealRechargeClosureTest.setUp
    payload = base.PointsRealRechargeClosureTest.payload
    quoted_payload = base.PointsRealRechargeClosureTest.quoted_payload
    fund = base.PointsRealRechargeClosureTest.fund
    verify = base.PointsRealRechargeClosureTest.verify

    def test_first_addon_refund_release_rolls_back_retries_and_detects_tampering(self):
        with TemporaryDirectory(prefix='xa-addon-receipt-') as media, override_settings(MEDIA_ROOT=media):
            with store_context(self.second):
                order = Order.objects.get(pk=self.order.pk)
                assignment = OrderProvider.objects.get(order=order)
                player = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(assignment.provider_account)[0])
                boss = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(order.customer_account)[0])
                self.assertEqual(player.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()},
                                             format='multipart').data['code'], 0)
                game = GameCategory.objects.create(name='加购共享到账')
                self.service.game_category = game; self.service.save(update_fields=['game_category'])
                addon = make_service(name='加购共享到账', game_category=game, is_addon=True)
                config = ServiceAddonConfiguration.objects.create(service=self.service, addon=addon, enabled=True,
                    condition_mode='NONE', conditions='', version=1)
                ServiceAddonPriceOption.objects.create(configuration=config, unit='HOUR', price=1000)
                # The gift balance is an isolated fixture; the add-on itself is paid through its real API.
                wallet = get_wallet(account=order.customer_account, user_id=order.customer_id)
                wallet.balance += 1000; wallet.save(update_fields=['balance'])
                PointFundingLot.objects.create(account=order.customer_account, kind='GIFT',
                    source_key='addon-receipt-gift', remaining=1000)
                path = f'/api/orders/orders/{order.pk}/addons/intents/'
                intent = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': assignment.provider_account_id,
                    'request_id': 'addon-usage', 'expected_amount': 1000, 'gift_balance_amount': 1000,
                    'compensation_balance_amount': 0}, format='json')
                self.assertEqual(intent.status_code, 200, intent.data)
                paid = boss.post(path + f"{intent.data['data']['id']}/pay/", {}, format='json')
                self.assertEqual(paid.status_code, 200, paid.data)
                payment_id = paid.data['data']['payment_id']
            registered = platform_tests.PlatformPassRepaymentTests.post(self, 'xinan', {
                **self.data, 'request_id': str(uuid4()), 'external_reference': 'ADDON-USAGE-SHARED', 'amount': 2800})
            self.assertEqual(registered.status_code, 200, registered.data)
            self.claim = registered.data['data']['claim_id']
            self.receipt_id = registered.data['data']['receipt_id']
            self.url = f'/api/admin/platform/pass-refund-repayments/xinan/{self.claim}/points-allocation'
            pass_refund = platform_tests.PlatformPassRepaymentTests.setup_refunds(self)[0]
            self.assertEqual(self.verify().status_code, 200)
            with store_context(self.second), transaction.atomic():
                initialize_empty_locked(self.receipt_id)
            allocated = platform_tests.PlatformPassRepaymentTests.action(self, self.claim, 'allocate',
                {'request_id': str(uuid4()), 'refund_id': pass_refund, 'amount': 100})
            self.assertEqual(allocated.status_code, 200, allocated.data)
            funded = self.fund({'request_id': str(uuid4()), 'order_id': order.pk, 'amount': 1600})
            self.assertEqual(funded.status_code, 200, funded.data)
            with store_context(self.second):
                staff_user = make_console_user(['order:view', 'support:edit', 'order:refund', 'order:settle'])
                staff = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff; staff_user.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff)[0])
                opened = admin.post('/api/admin/aftersales/open-negotiation/', {'order_id': order.pk,
                    'reason': '加购部分退款', 'affected_provider_account_ids': [assignment.provider_account_id],
                    'client_request_id': 'addon-usage-case'}, format='json')
                self.assertEqual(opened.status_code, 200, opened.data)
                staff_url = f'/api/admin/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/'
                proposal = admin.post(staff_url, {'request_id': 'addon-usage-refund', 'amount_raw': 100,
                    'case_id': opened.data['data']['id'], 'explanation': '加购部分未履行'}, format='json')
                self.assertEqual(proposal.status_code, 200, proposal.data)
                proposal_id = proposal.data['data']['id']
                user_url = f'/api/orders/orders/{order.pk}/addons/payments/{payment_id}/refund-proposals/{proposal_id}/'
                for actor in (boss, player):
                    self.assertEqual(actor.post(user_url, {'accepted': True}, format='json').status_code, 200)
                execute = staff_url + f'{proposal_id}/execute/'
                original = PassReceiptUsageEvent.objects.create
                def fail_release(**kwargs):
                    if kwargs.get('kind') == 'RELEASE':
                        raise RuntimeError('injected addon release failure')
                    return original(**kwargs)
                with patch('users.receipt_usage_ledger.PassReceiptUsageEvent.objects.create', side_effect=fail_release):
                    with self.assertRaisesMessage(RuntimeError, 'injected addon release failure'):
                        admin.post(execute, {}, format='json')
                self.assertFalse(order.addon_refunds.exists())
                self.assertFalse(OrderAddonFundingReleaseRun.objects.filter(refund__isnull=False).exists())
                result = admin.post(execute, {}, format='json')
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual(admin.post(execute, {}, format='json').status_code, 200)
                run = OrderAddonFundingReleaseRun.objects.get(refund__proposal_id=proposal_id)
                self.assertTrue(run.points_addon_hash)
                resolved = admin.post(f"/api/admin/aftersales/{opened.data['data']['id']}/resolve/",
                                      {'resolution': '加购退款完成'}, format='json')
                self.assertEqual(resolved.status_code, 200, resolved.data)
                later = boss.post(path, {'addon_id': addon.pk, 'unit': 'HOUR', 'quantity': 1,
                    'configuration_version': 1, 'provider_account_id': assignment.provider_account_id,
                    'request_id': 'addon-usage-later-unpaid', 'expected_amount': 1000,
                    'gift_balance_amount': 0, 'compensation_balance_amount': 0}, format='json')
                self.assertEqual(later.status_code, 200, later.data)
                blocked = boss.post(path + f"{later.data['data']['id']}/pay/", {}, format='json')
                self.assertEqual(blocked.status_code, 400, blocked.data)
                # An unpaid later intent cannot rewrite the earlier release.
                self.assertEqual(admin.post(execute, {}, format='json').status_code, 200)
                with transaction.atomic():
                    state = verify_allocations_locked(self.receipt_id)
                    self.assertEqual((state.pass_units, state.order_units, state.released_units), (100, 1600, run.released_amount))
                    self.assertGreater(run.released_amount, 0)
                    from django.core.exceptions import ValidationError
                    with override_settings(SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING=False):
                        with self.assertRaises(ValidationError):
                            verify_allocations_locked(self.receipt_id)
                    row = OrderAddonFundingRelease.objects.get(run=run)
                    with transaction.atomic():
                        OrderAddonFundingRelease.objects.filter(pk=row.pk).update(amount=row.amount - 1)
                        with self.assertRaises(ValidationError):
                            verify_allocations_locked(self.receipt_id)
                        transaction.set_rollback(True)
                    with transaction.atomic():
                        type(run.refund.proposal).objects.filter(pk=proposal_id).update(explanation='篡改后的退款说明')
                        with self.assertRaises(ValidationError):
                            verify_allocations_locked(self.receipt_id)
                        self.assertEqual(admin.post(execute, {}, format='json').status_code, 400)
                        transaction.set_rollback(True)
            # A distinct buyer's reservation cancels and later another points
            # order negotiates a refund against this very same receipt.
            from datetime import timedelta
            from django.utils import timezone
            from console.reservation_deadlines import save_deadlines
            from engagement.models import MemberProfile
            from orders.tests.factories import make_user, make_provider
            with store_context(self.second):
                save_deadlines(30, 60, 0, lambda *_: None)
                user = make_user(); account = get_or_create_account_for_legacy_user(user)
                MemberProfile.objects.create(account=account, points_balance=10000)
                provider_user = make_provider(deposit_mode='EARNINGS')
                provider = get_or_create_account_for_legacy_user(provider_user)
                provider_user.escort_profile.account = provider; provider_user.escort_profile.save(update_fields=['account'])
                worker = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider)[0])
                buyer = APIClient(HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                staff_user = make_console_user(['wallet:recharge', 'order:view', 'order:refund', 'support:edit'])
                staff_account = get_or_create_account_for_legacy_user(staff_user)
                staff_user.admin_membership.account = staff_account; staff_user.admin_membership.save(update_fields=['account'])
                staff_api = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
                recharge = staff_api.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': 10000,
                    'gift_amount': 0, 'trade_no': 'addon-usage-booking', 'client_request_id': 'addon-usage-recharge'}, format='json')
                self.assertEqual(recharge.data['code'], 0, recharge.data)
                service = make_service(price=10000, commission_rate=10)
                start = (timezone.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
                def reservation(request_id):
                    created = buyer.post('/api/orders/reservations/', {'provider_account_id': provider.pk,
                        'service_id': service.pk, 'start_time': start.isoformat(),
                        'end_time': (start + timedelta(hours=1)).isoformat()}, format='json')
                    self.assertEqual(created.data['code'], 0, created.data)
                    path = f"/api/orders/reservations/{created.data['data']['id']}/"
                    self.assertEqual(worker.post(path + 'confirm/', {}, format='json').data['code'], 0)
                    quote = buyer.get(path + 'quote/', {'preview_points_hundredths': 10000}).data['data']
                    converted = buyer.post(path + 'convert/', {'discount_selection_token': quote['discount_selection_token'],
                        'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
                        'expected_amount': quote['points_preview']['estimated_payable_units'],
                        'client_request_id': request_id}, format='json')
                    self.assertEqual(converted.data['code'], 0, converted.data)
                    return converted.data['data']['order']['id']
                cancelled_id = reservation('addon-usage-reservation-cancel')
            response = self.fund({'request_id': str(uuid4()), 'order_id': cancelled_id, 'amount': 100})
            self.assertEqual(response.status_code, 200, response.data)
            cancelled = staff_api.post(f'/api/admin/orders/{cancelled_id}/refund/', {
                'reason': '预约取消', 'client_request_id': 'addon-usage-cancel'}, format='json')
            self.assertEqual(cancelled.data['code'], 0, cancelled.data)
            with store_context(self.second):
                second_id = reservation('addon-usage-second-order')
            response = self.fund({'request_id': str(uuid4()), 'order_id': second_id, 'amount': 1000})
            self.assertEqual(response.status_code, 200, response.data)
            with store_context(self.second):
                started = worker.post(f'/api/orders/orders/{second_id}/start/', {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(started.data['code'], 0, started.data)
                case = staff_api.post('/api/admin/aftersales/open-negotiation/', {'order_id': second_id,
                    'reason': '主单部分退款', 'affected_provider_account_ids': [provider.pk],
                    'client_request_id': 'addon-usage-main-case'}, format='json')
                self.assertEqual(case.data['code'], 0, case.data)
                case_id = case.data['data']['id']
                proposal = staff_api.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                    'request_id': 'addon-usage-main-proposal', 'expected_version': 0, 'refund_units': 1000,
                    'provider_terms': [{'account_id': provider.pk, 'retained_units': 8000}],
                    'explanation': '双方确认主单退款'}, format='json')
                self.assertEqual(proposal.data['code'], 0, proposal.data)
                main_proposal_id = proposal.data['data']['id']
                for actor in (buyer, worker):
                    accepted = actor.post(f'/api/orders/orders/{second_id}/negotiation-proposal/',
                        {'proposal_id': main_proposal_id, 'accepted': True}, format='json')
                    self.assertEqual(accepted.data['code'], 0, accepted.data)
                result = staff_api.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                    'proposal_id': main_proposal_id, 'client_request_id': 'addon-usage-main-refund'}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
                with transaction.atomic():
                    state = verify_allocations_locked(self.receipt_id)
                    self.assertEqual((state.pass_units, state.order_units), (100, 2700))
                    self.assertGreater(state.released_units, run.released_amount + 100)
