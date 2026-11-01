from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from django.db import close_old_connections, transaction
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import ConsumptionConfirmation, PointLedger
from orders.direct_clearing import DirectClearingError, post_test_clearing
from orders.direct_clearing_models import DirectClearingBatch, DirectClearingLine
from orders.direct_payment import VerifiedPayment, confirm_verified_payment, prepare_direct_order
from orders.direct_payment_quote import quote_direct_payment
from orders.models import AfterSale, Order, OrderProvider
from orders.settlement import aggregate_order_split, build_provider_shares
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import IncomeHold, ProviderReport, ProviderReportImage, Transaction, Wallet


FEE = {'policy_id': 'test-rate', 'rate_numerator': 6, 'rate_denominator': 1000,
       'fee_basis': 'PAYMENT_TOTAL', 'rounding': 'UP'}


class ClearingFixture:
    def make_completed(self, *, count=2, price=5001, fee_policy=FEE, conversion=None,
                       inviter=False, rates=None, request_id='clear-req-1'):
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss)
        account.openid, account.wechat_appid, account.is_openid_bound = f'openid-{boss.pk}', 'wx-test', True
        account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        make_wallet(boss, balance=50000)
        service = make_service(price=price, billing_provider_count=count)
        participants = [make_provider() for _ in range(count)]
        participants_accounts = [get_or_create_account_for_legacy_user(user) for user in participants]
        for user, provider_account in zip(participants, participants_accounts):
            EscortProfile.objects.filter(user=user).update(account=provider_account)
            make_wallet(user, balance=0)
        staff = make_console_user(['report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        invite_user = make_user() if inviter else None
        invite_account = get_or_create_account_for_legacy_user(invite_user) if inviter else None
        base_time = timezone.now() - timedelta(hours=80)
        settings = {'WECHAT_DIRECT_TEST_FEE_POLICY': fee_policy, 'ORDER_SETTLEMENT_MODE': 'REPORT_APPROVAL'}
        if conversion is not None:
            settings['WECHAT_DIRECT_TEST_UNIT_CONVERSION'] = conversion
        with override_settings(**settings), patch('django.utils.timezone.now', return_value=base_time):
            quote = quote_direct_payment(service_units=price * count, appid='wx-test', mchid='mch-test')
            data = {'service_id': service.pk, 'expected_amount': price * count, 'client_request_id': request_id,
                    'expected_payment_total': quote.total_fen, 'expected_fee_quote_hash': quote.quote_hash}
            intent = prepare_direct_order(SimpleNamespace(account=account, legacy_user=boss, data=data),
                                          appid='wx-test', mchid='mch-test')
            evidence = VerifiedPayment(intent.merchant_order_no, f'tx-{request_id}', intent.appid,
                                       intent.mchid, intent.openid, intent.amount_fen, 'CNY', base_time,
                                       payer_total_fen=intent.amount_fen, payer_currency='CNY')
            with patch('orders.direct_payment._notify_paid'):
                confirm_verified_payment(intent.order_id, evidence)
        order = Order.objects.get(pk=intent.order_id)
        specs = [{'commission_type': 'PERCENT', 'commission_rate': rate}
                 for rate in (rates if rates is not None else [20] * count)]
        shares = build_provider_shares(order.amount, specs)
        split = aggregate_order_split(order.amount, shares, inviter_commission_rate=10 if inviter else 0)
        completed = base_time + timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.COMPLETED, grabbed_at=base_time + timedelta(minutes=10),
            in_service_at=base_time + timedelta(minutes=30), completed_at=completed,
            provider_id=participants[0].pk, provider_account_id=participants_accounts[0].pk,
            escort_mode=Order.EscortMode.DOUBLE if count == 2 else Order.EscortMode.SINGLE,
            provider_income=split.provider_income, inviter_id=invite_user.pk if invite_user else None,
            inviter_account_id=invite_account.pk if invite_account else None,
            inviter_commission=split.inviter_commission, inviter_commission_rate_snapshot=10 if inviter else 0,
            shop_income=split.shop_income)
        for user, provider_account, share in zip(participants, participants_accounts, shares):
            OrderProvider.objects.create(order=order, provider=user, provider_account=provider_account,
                settlement_base=share.settlement_base, provider_income=share.provider_income,
                commission_type=share.commission_type, commission_rate=share.commission_rate,
                commission_fixed=share.commission_fixed)
            report = ProviderReport.objects.create(order=order, provider=user, provider_account=provider_account,
                game_name='模拟报单', amount=share.settlement_base, payout_amount=share.provider_income,
                commission_rate=share.commission_rate, status='APPROVED', auditor=staff, auditor_account=staff_account,
                submitted_at=completed + timedelta(hours=1), audited_at=completed + timedelta(hours=2),
                entry_image='test/entry.png', completion_image='test/finish.png')
            ProviderReportImage.objects.create(report=report, image='test/result.png')
        last_submitted = completed + timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(report_ready_at=last_submitted,
                                                  consumption_ready_at=last_submitted + timedelta(hours=72))
        return Order.objects.get(pk=order.pk), intent, participants


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectClearingTest(ClearingFixture, TestCase):
    def test_two_books_balance_fee_is_separate_and_replay_is_idempotent(self):
        order, intent, participants = self.make_completed(inviter=True, rates=[20, 35])
        before_wallets = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        before_counts = (Transaction.objects.count(), IncomeHold.objects.count(), PointLedger.objects.count(),
                         ConsumptionConfirmation.objects.count())
        batch = post_test_clearing(order.pk)
        self.assertEqual(post_test_clearing(order.pk).pk, batch.pk)
        self.assertEqual(DirectClearingBatch.objects.count(), 1)
        quote = intent.fee_quote
        self.assertEqual((batch.service_units, batch.service_fen, batch.fee_fen, batch.total_fen),
                         (order.amount, quote.service_fen, quote.fee_fen, quote.total_fen))
        self.assertGreater(batch.fee_fen, 0)
        lines = list(batch.lines.all())
        for book, expected in (('FEN', batch.total_fen), ('UNIT', batch.service_units)):
            self.assertEqual(sum(line.amount for line in lines if line.book == book and line.side == 'DEBIT'), expected)
            self.assertEqual(sum(line.amount for line in lines if line.book == book and line.side == 'CREDIT'), expected)
        provider_lines = [line for line in lines if line.account_code == 'PROVIDER_DUE']
        self.assertEqual({line.recipient_user_id for line in provider_lines}, {user.pk for user in participants})
        self.assertEqual(sum(line.amount for line in provider_lines), order.provider_income)
        self.assertEqual(next(line.amount for line in lines if line.account_code == 'FEE_HELD'), batch.fee_fen)
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before_wallets)
        self.assertEqual((Transaction.objects.count(), IncomeHold.objects.count(), PointLedger.objects.count(),
                          ConsumptionConfirmation.objects.count()), before_counts)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status, order.settled_at, order.consumption_confirmed_at),
                         ('COMPLETED', 'PAID', None, None))

    def test_two_half_fen_shares_remain_in_internal_unit_book(self):
        order, intent, _ = self.make_completed(count=2, price=1, fee_policy=None, rates=[0, 0],
                                               conversion={'version': 'test-200', 'units_per_yuan': 200})
        batch = post_test_clearing(order.pk)
        self.assertEqual((batch.service_units, batch.service_fen, batch.fee_fen, batch.total_fen), (2, 1, 0, 1))
        self.assertEqual(list(batch.lines.filter(account_code='PROVIDER_DUE').order_by('pk')
                              .values_list('book', 'amount')), [('UNIT', 1), ('UNIT', 1)])
        self.assertFalse(batch.lines.filter(account_code='FEE_HELD').exists())

    def test_one_internal_unit_double_order_keeps_zero_share_member(self):
        # Fixture creates two equal-priced units; use a single-unit order by
        # changing the prepared service quantity only in a dedicated snapshot.
        order, intent, _ = self.make_completed(count=1, price=1, fee_policy=None, rates=[0])
        second = make_provider()
        second_account = get_or_create_account_for_legacy_user(second)
        staff_report = order.provider_reports.get()
        Order.objects.filter(pk=order.pk).update(required_provider_count=2, escort_mode='DOUBLE')
        OrderProvider.objects.create(order=order, provider=second, provider_account=second_account,
                                     settlement_base=0, provider_income=0, commission_rate=0)
        report = ProviderReport.objects.create(order=order, provider=second, provider_account=second_account,
            game_name='模拟报单', amount=0, payout_amount=0, commission_rate=0, status='APPROVED',
            auditor_id=staff_report.auditor_id, auditor_account_id=staff_report.auditor_account_id,
            submitted_at=staff_report.submitted_at, audited_at=staff_report.audited_at,
            entry_image='test/entry.png', completion_image='test/finish.png')
        ProviderReportImage.objects.create(report=report, image='test/result.png')
        batch = post_test_clearing(order.pk)
        self.assertEqual(len(batch.snapshot['members']), 2)
        self.assertEqual(sum(member['income'] for member in batch.snapshot['members']), 1)
        self.assertEqual(batch.lines.filter(account_code='PROVIDER_DUE').count(), 1)
        self.assertFalse(batch.lines.filter(recipient_account_id=second_account.pk).exists())

    def test_completion_aged_late_report_clears_without_confirming_vip(self):
        order, _, _ = self.make_completed(count=1)
        submitted = timezone.now() - timedelta(hours=1)
        order.provider_reports.update(submitted_at=submitted, audited_at=submitted + timedelta(minutes=10))
        Order.objects.filter(pk=order.pk).update(report_ready_at=submitted,
                                                consumption_ready_at=submitted + timedelta(hours=72))
        before = (PointLedger.objects.count(), ConsumptionConfirmation.objects.count(),
                  Transaction.objects.count(), IncomeHold.objects.count())
        batch = post_test_clearing(order.pk)
        self.assertEqual(batch.snapshot['vip_consumption_ready_at'], (submitted + timedelta(hours=72)).isoformat())
        self.assertLess(batch.created_at, submitted + timedelta(hours=72))
        self.assertEqual(before, (PointLedger.objects.count(), ConsumptionConfirmation.objects.count(),
                                  Transaction.objects.count(), IncomeHold.objects.count()))
        order.refresh_from_db()
        self.assertIsNone(order.consumption_confirmed_at)
        self.assertIsNone(order.settled_at)
        self.assertEqual(post_test_clearing(order.pk).pk, batch.pk)
        with patch('django.utils.timezone.now', return_value=submitted + timedelta(hours=73)):
            self.assertEqual(post_test_clearing(order.pk).pk, batch.pk)

    def test_inconsistent_vip_clock_still_rejects_clearing(self):
        order, _, _ = self.make_completed(count=1)
        Order.objects.filter(pk=order.pk).update(consumption_ready_at=timezone.now())
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)
        self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    def test_percentage_commission_with_fixed_amount_is_ambiguous(self):
        order, _, _ = self.make_completed(count=1)
        order.providers.update(commission_fixed=1)
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)
        self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    def test_channel_time_outside_test_skew_is_rejected(self):
        for case in ('future', 'after_pool', 'before_creation'):
            with self.subTest(case=case):
                order, intent, _ = self.make_completed(count=1, request_id='clock-' + case)
                when = {'future': timezone.now() + timedelta(days=3),
                        'after_pool': order.pool_entered_at + timedelta(minutes=5, microseconds=1),
                        'before_creation': order.created_at - timedelta(minutes=5, microseconds=1)}[case]
                type(intent).objects.filter(pk=intent.pk).update(paid_at=when)
                with self.assertRaises(DirectClearingError):
                    post_test_clearing(order.pk)
                self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    def test_channel_time_at_test_skew_boundary_is_allowed(self):
        for case in ('before_creation', 'after_pool'):
            with self.subTest(case=case):
                order, intent, _ = self.make_completed(count=1, request_id='boundary-' + case)
                when = (order.created_at - timedelta(minutes=5) if case == 'before_creation'
                        else order.pool_entered_at + timedelta(minutes=5))
                type(intent).objects.filter(pk=intent.pk).update(paid_at=when)
                self.assertEqual(post_test_clearing(order.pk).snapshot['paid_at'], when.isoformat())

    def test_zero_valued_rejected_provider_history_is_retained_without_a_payout(self):
        order, _, _ = self.make_completed(count=1)
        rejected = make_provider()
        rejected_account = get_or_create_account_for_legacy_user(rejected)
        old = OrderProvider.objects.create(order=order, provider=rejected, provider_account=rejected_account,
            settlement_base=0, provider_income=0, commission_fixed=0,
            released_at=order.grabbed_at + timedelta(minutes=1))
        batch = post_test_clearing(order.pk)
        self.assertEqual(batch.snapshot['released_members'][0]['assignment_id'], old.pk)
        self.assertFalse(batch.lines.filter(recipient_account_id=rejected_account.pk).exists())

    def test_resolved_aftersale_without_refund_is_snapshotted_without_text(self):
        from orders.aftersale import append_history
        order, intent, _ = self.make_completed(count=1)
        staff = make_console_user(['order:settle'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        with patch('django.utils.timezone.now', return_value=order.completed_at + timedelta(hours=1)):
            ticket = AfterSale.objects.create(order=order, account_id=order.customer_account_id, reason='private reason')
            append_history(ticket)
            ticket.save()
        with patch('django.utils.timezone.now', return_value=order.completed_at + timedelta(hours=2)):
            ticket.status, ticket.resolution = 'RESOLVED', 'private decision'
            ticket.operator, ticket.resolved_at = staff_account, timezone.now()
            append_history(ticket)
            ticket.save()
        batch = post_test_clearing(order.pk)
        self.assertEqual(batch.snapshot['resolved_aftersales'][0]['id'], ticket.pk)
        self.assertNotIn('private', str(batch.snapshot['resolved_aftersales']))
        self.assertEqual(post_test_clearing(order.pk).pk, batch.pk)
        intent.channel_refunds.create(request_id='refund-after-resolve', merchant_refund_no='rf-resolve',
                                      amount_fen=intent.amount_fen, total_fen=intent.amount_fen, reason='test')
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)

    def test_resolved_aftersale_without_verifiable_history_is_rejected(self):
        order, _, _ = self.make_completed(count=1)
        ticket = AfterSale.objects.create(order=order, account_id=order.customer_account_id,
                                          reason='test', status='RESOLVED', resolution='done')
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)
        self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    def test_ineligible_evidence_and_state_never_write_simulation(self):
        cases = ('unpaid', 'risky', 'coupon', 'refund', 'aftersale', 'discount', 'report', 'unfinished',
                 'early', 'legacy_mode', 'released', 'quote', 'mixed_wallet', 'business_cycle')
        for case in cases:
            with self.subTest(case=case):
                order, intent, _ = self.make_completed(count=1, request_id='req-' + case)
                if case == 'unpaid':
                    Order.objects.filter(pk=order.pk).update(payment_status='UNPAID')
                elif case == 'risky':
                    type(intent).objects.filter(pk=intent.pk).update(evidence_risk_at=timezone.now())
                elif case == 'coupon':
                    type(intent).objects.filter(pk=intent.pk).update(promotion_detail=[{'amount': 1}])
                elif case == 'refund':
                    intent.channel_refunds.create(request_id='refund-req', merchant_refund_no='refund-' + case,
                        amount_fen=intent.amount_fen, total_fen=intent.amount_fen, reason='test')
                elif case == 'aftersale':
                    AfterSale.objects.create(order=order, account_id=order.customer_account_id, reason='test')
                elif case == 'discount':
                    Order.objects.filter(pk=order.pk).update(boss_discount=1)
                elif case == 'report':
                    order.provider_reports.update(status='PENDING')
                elif case == 'unfinished':
                    Order.objects.filter(pk=order.pk).update(status='IN_SERVICE')
                elif case == 'early':
                    Order.objects.filter(pk=order.pk).update(completed_at=timezone.now()-timedelta(hours=1))
                elif case == 'legacy_mode':
                    Order.objects.filter(pk=order.pk).update(settlement_mode='COMPLETION')
                elif case == 'released':
                    order.providers.update(released_at=timezone.now())
                elif case == 'quote':
                    type(intent.fee_quote).objects.filter(intent=intent).update(quote_hash='0' * 64)
                elif case == 'mixed_wallet':
                    wallet = Wallet.objects.get(user_id=order.customer_id)
                    Transaction.objects.create(wallet=wallet, order=order, amount=-1, tx_type='PAY')
                elif case == 'business_cycle':
                    type(order.customer_account).objects.filter(pk=order.customer_account_id).update(business_cycle=2)
                with self.assertRaises(DirectClearingError):
                    post_test_clearing(order.pk)
                self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    def test_snapshots_and_lines_cannot_silently_change_on_replay(self):
        order, _, _ = self.make_completed(count=1)
        batch = post_test_clearing(order.pk)
        line = batch.lines.filter(account_code='PROVIDER_DUE').get()
        DirectClearingLine.objects.filter(pk=line.pk).update(amount=line.amount+1)
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)
        self.assertEqual(DirectClearingBatch.objects.count(), 1)

    def test_failure_in_line_write_rolls_back_batch(self):
        order, _, _ = self.make_completed(count=1)
        with patch.object(DirectClearingLine.objects, 'bulk_create', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                post_test_clearing(order.pk)
        self.assertFalse(DirectClearingBatch.objects.exists())
        self.assertFalse(DirectClearingLine.objects.exists())

    def test_disabled_test_domain_and_non_integer_id_fail(self):
        order, _, _ = self.make_completed(count=1)
        with override_settings(WECHAT_DIRECT_DOMAIN_TESTING=False):
            with self.assertRaises(ValueError):
                post_test_clearing(order.pk)
        with self.assertRaises(DirectClearingError):
            post_test_clearing(True)
        self.assertFalse(DirectClearingBatch.objects.exists())


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True, WECHAT_DIRECT_TEST_FEE_POLICY=FEE,
                   LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class DirectClearingApiFlowTest(TestCase):
    def screenshot(self):
        output = BytesIO()
        Image.new('RGB', (4, 4), 'white').save(output, format='PNG')
        return SimpleUploadedFile('test.png', output.getvalue(), content_type='image/png')

    def test_staff_assign_provider_service_report_and_simulated_clearing(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch', 'report:view', 'report:audit'])
        accounts = {user.pk: get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff)}
        boss_account, provider_account, staff_account = (accounts[user.pk] for user in (boss, provider, staff))
        boss_account.openid, boss_account.wechat_appid, boss_account.is_openid_bound = 'openid-api', 'wx-test', True
        boss_account.save(update_fields=['openid', 'wechat_appid', 'is_openid_bound'])
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        make_wallet(boss, balance=50000)
        make_wallet(provider, balance=0)
        service = make_service(price=10000)
        staff_token, _ = issue_account_tokens(staff_account)
        provider_token, _ = issue_account_tokens(provider_account)
        client = APIClient()
        base = timezone.now() - timedelta(hours=80)
        with patch('django.utils.timezone.now', return_value=base), \
             patch('orders.direct_payment._notify_paid'), \
             patch('console.views.notify_order_update'), patch('console.views._push_message_safe'), \
             patch('orders.views.notify_order_update'):
            quote = quote_direct_payment(service_units=10000, appid='wx-test', mchid='mch-test')
            payload = {'service_id': service.pk, 'expected_amount': 10000, 'client_request_id': 'api-clear-1',
                       'expected_payment_total': quote.total_fen, 'expected_fee_quote_hash': quote.quote_hash,
                       'dispatch_route': 'STAFF'}
            intent = prepare_direct_order(SimpleNamespace(account=boss_account, legacy_user=boss, data=payload),
                                          appid='wx-test', mchid='mch-test')
            evidence = VerifiedPayment(intent.merchant_order_no, 'tx-api-clear', intent.appid, intent.mchid,
                intent.openid, intent.amount_fen, 'CNY', base, payer_total_fen=intent.amount_fen, payer_currency='CNY')
            confirm_verified_payment(intent.order_id, evidence)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
            response = client.post(f'/api/admin/orders/{intent.order_id}/assign/', {'provider_id': provider_account.pk}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + provider_token)
            response = client.post(f'/api/orders/orders/{intent.order_id}/start/', {'entry_image': self.screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            response = client.post(f'/api/orders/orders/{intent.order_id}/complete/', {'completion_image': self.screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order_id=intent.order_id, provider=provider)
            response = client.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': self.screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            response = client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        # First approval happens after the order's 72-hour window, a distinct
        # path from an early approval followed by later simulated clearing.
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        with patch('console.views.notify_order_update'), patch('console.views._push_message_safe'):
            response = client.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        before = list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount'))
        batch = post_test_clearing(intent.order_id)
        self.assertEqual(batch.service_units, 10000)
        self.assertEqual(len(batch.snapshot['members']), 1)
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')), before)
        self.assertIsNone(Order.objects.get(pk=intent.order_id).settled_at)
        self.assertFalse(Transaction.objects.filter(order_id=intent.order_id).exists())


@override_settings(WECHAT_DIRECT_DOMAIN_TESTING=True)
class DirectClearingConcurrencyTest(ClearingFixture, TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_aftersale_resolution_wins_order_lock_before_simulated_clearing(self):
        from orders.aftersale import append_history
        order, _, _ = self.make_completed(count=1)
        staff = get_or_create_account_for_legacy_user(make_console_user(['order:settle']))
        with patch('django.utils.timezone.now', return_value=order.completed_at + timedelta(hours=1)):
            ticket = AfterSale.objects.create(order=order, account_id=order.customer_account_id, reason='test')
            append_history(ticket)
            ticket.save()
        attempted = Event()
        def worker():
            close_old_connections()
            try:
                attempted.set()
                return post_test_clearing(order.pk).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                current = AfterSale.objects.select_for_update().get(pk=ticket.pk)
                future = executor.submit(worker)
                self.assertTrue(attempted.wait(10))
                current.status, current.resolution, current.operator = 'RESOLVED', 'done', staff
                current.resolved_at = timezone.now()
                append_history(current)
                current.save()
            batch = DirectClearingBatch.objects.get(pk=future.result(timeout=20))
        self.assertEqual(batch.snapshot['resolved_aftersales'][0]['id'], ticket.pk)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_workers_post_exactly_one_batch(self):
        order, _, _ = self.make_completed(count=2)
        barrier = Barrier(2, timeout=10)
        def worker():
            close_old_connections()
            try:
                barrier.wait()
                return post_test_clearing(order.pk).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            a, b = executor.submit(worker), executor.submit(worker)
            self.assertEqual(a.result(timeout=20), b.result(timeout=20))
        self.assertEqual(DirectClearingBatch.objects.filter(order=order).count(), 1)
        self.assertFalse(Transaction.objects.filter(order=order).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_evidence_risk_wins_order_lock_blocks_batch(self):
        order, intent, _ = self.make_completed(count=1)
        attempted = Event()
        def worker():
            close_old_connections()
            try:
                attempted.set()
                try:
                    post_test_clearing(order.pk)
                except DirectClearingError:
                    return 'blocked'
                return 'created'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with transaction.atomic():
                Order.objects.select_for_update().get(pk=order.pk)
                current = type(intent).objects.select_for_update().get(pk=intent.pk)
                future = executor.submit(worker)
                self.assertTrue(attempted.wait(10))
                current.evidence_risk_at = timezone.now()
                current.status = 'REVIEW'
                current.save(update_fields=['evidence_risk_at', 'status', 'updated_at'])
            self.assertEqual(future.result(timeout=20), 'blocked')
        self.assertFalse(DirectClearingBatch.objects.filter(order=order).exists())

    @skipUnlessDBFeature('has_select_for_update')
    def test_completed_simulation_then_evidence_conflict_blocks_replay_without_wallet_credit(self):
        order, intent, _ = self.make_completed(count=1)
        holding, attempted, release = Event(), Event(), Event()
        from orders.direct_clearing import _read_source as original_read
        from orders.direct_payment_evidence_compare import flag_payment_evidence_risk

        def read(*args, **kwargs):
            holding.set()
            if not release.wait(10):
                raise RuntimeError('test barrier timed out')
            return original_read(*args, **kwargs)

        def clear_worker():
            close_old_connections()
            try:
                return post_test_clearing(order.pk).pk
            finally:
                close_old_connections()

        def conflict_worker():
            close_old_connections()
            try:
                attempted.set()
                with transaction.atomic():
                    current_order = Order.objects.select_for_update().get(pk=order.pk)
                    current_intent = type(intent).objects.select_for_update().get(order=current_order)
                    flag_payment_evidence_risk(current_intent)
            finally:
                close_old_connections()

        with patch('orders.direct_clearing._read_source', side_effect=read), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(clear_worker)
            self.assertTrue(holding.wait(10))
            second = executor.submit(conflict_worker)
            self.assertTrue(attempted.wait(10))
            release.set()
            batch_id = first.result(timeout=20)
            second.result(timeout=20)
        with self.assertRaises(DirectClearingError):
            post_test_clearing(order.pk)
        self.assertTrue(DirectClearingBatch.objects.filter(pk=batch_id, order=order).exists())
        self.assertFalse(Transaction.objects.filter(order=order).exists())
        self.assertFalse(IncomeHold.objects.filter(income_transaction__order=order).exists())
