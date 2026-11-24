"""Real reservation checkout with points, restricted to disposable test DBs."""
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.points_redemption_config import save_points_terms
from engagement.models import MemberProfile, PointFundingLot, PointLedger
from engagement.services import config
from orders.models import Order, OrderPointsRedemption, OrderSegmentFinancialSnapshot, Reservation
from orders.tests.factories import make_console_user, make_provider, make_service
from orders.tests.test_reservation_api import ReservationApiTestBase, slot
from stores.db import transaction
from users.models import EscortProfile
from wallet.models import IdempotencyKey, Transaction, Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, PROVIDER_WECHAT_GATE_ENABLED=False,
    DISCOUNT_COMBINATIONS_PAYMENT_ENABLED=True, POINTS_REDEMPTION_PREVIEW_ENABLED=True,
    POINTS_USAGE_SWITCHES_ENABLED=True, SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True,
    BOSS_FUNDING_SELECTION_ENABLED=True, SERVICE_POINTS_CHECKOUT_TESTING=True,
    SERVICE_POINTS_ASSIGN_TESTING=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
    SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, CELERY_TASK_ALWAYS_EAGER=False,
    KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class ReservationPointsTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        config()
        self.service.price = 10000
        self.service.commission_rate = 20
        self.service.save(update_fields=['price', 'commission_rate'])
        wallet = Wallet.objects.get(account=self.boss_account)
        self.prepaid_lot = PointFundingLot.objects.create(account=self.boss_account,
            source_key='isolated-reservation-points-principal', kind=PointFundingLot.Kind.PREPAID,
            remaining=wallet.balance)
        self.profile, _ = MemberProfile.objects.update_or_create(account=self.boss_account,
            defaults={'points_balance': 15000})
        save_points_terms(group_point_hundredths=10000, group_coin_units=4000,
                          max_ratio_basis_points=5000, version=0, audit=lambda *_: None)
        self.boss_client = self.login(self.boss_account)
        self.provider_client = self.login(self.provider_account)

    @staticmethod
    def login(account):
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def use_mixed_wallet_sources(self):
        other_sources = ((PointFundingLot.Kind.GIFT, 2000), (PointFundingLot.Kind.COMPENSATION, 1000))
        self.prepaid_lot.remaining -= sum(amount for _, amount in other_sources)
        self.prepaid_lot.save(update_fields=['remaining'])
        for kind, amount in other_sources:
            PointFundingLot.objects.create(account=self.boss_account,
                source_key=f'isolated-reservation-points-{kind}', kind=kind, remaining=amount)

    def confirmed(self, *, team=False, minute=0):
        start = slot(hour=10, minute=minute)
        payload = self.create_payload(start_time=start.isoformat(),
            end_time=(start + timedelta(hours=1)).isoformat(), game_rounds=1)
        accounts = [self.provider_account]
        if team:
            user = make_provider(deposit_mode='EARNINGS')
            account = get_or_create_account_for_legacy_user(user)
            link_legacy_relations(user, account)
            EscortProfile.objects.filter(user=user).update(account=account)
            service = make_service(price=10000, billing_provider_count=2, fulfillment_provider_count=2,
                                   commission_rate=20)
            payload.update(service_id=service.pk, provider_account_ids=[self.provider_account.pk, account.pk])
            accounts.append(account)
        result = self.boss_client.post('/api/orders/reservations/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        path = f"/api/orders/reservations/{result.data['data']['id']}/"
        for account in accounts:
            result = self.login(account).post(path + 'confirm/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
        return path, accounts

    def quote(self, path):
        response = self.boss_client.get(path + 'quote/', {'preview_points_hundredths': 10000})
        self.assertEqual(response.data['code'], 0, response.data)
        data = response.data['data']
        self.assertTrue(data['points_preview']['checkout_test_only'])
        self.assertEqual(data['payable_amount'], data['points_preview']['estimated_payable_units'])
        return {'discount_selection_token': data['discount_selection_token'],
                'points_checkout_test_token': data['points_preview']['checkout_test_token'],
                'expected_amount': data['points_preview']['estimated_payable_units'],
                'client_request_id': 'reservation-points-once'}

    def test_single_reservation_debits_once_and_records_pre_points_income(self):
        from orders.points_funding_plan import plan_verified_order
        path, accounts = self.confirmed()
        payload = self.quote(path)
        self.assertEqual(payload['expected_amount'], 6000)
        result = self.boss_client.post(path + 'convert/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        receipt = OrderPointsRedemption.objects.get(order=order)
        self.assertEqual((receipt.deal_units, receipt.discount_units, receipt.paid_units), (10000, 4000, 6000))
        self.assertEqual(OrderSegmentFinancialSnapshot.objects.get(segment__attribution__order=order).income_at_entry, 8000)
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.provider_income_units, plan.additional_store_funding_units), (8000, 2000))
        replay = self.boss_client.post(path + 'convert/', payload, format='json').data
        self.assertEqual((replay['code'], replay.get('errorCode')), (409, 'FUND_ALREADY_PROCESSED'), replay)
        self.assertEqual(replay['fundResult'], result.data)
        changed = self.boss_client.post(path + 'convert/', {**payload, 'gift_balance_amount': 1}, format='json').data
        self.assertEqual((changed['code'], changed.get('errorCode')), (409, 'REQUEST_ID_CONFLICT'))
        self.assertNotIn('fundResult', changed)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.points_balance, Wallet.objects.get(account=self.boss_account).balance), (5000, 94000))
        self.assertEqual((Transaction.objects.filter(order=order, tx_type='PAY').count(),
                          PointLedger.objects.filter(order=order, kind='ORDER_POINTS_REDEEM').count()), (1, 1))
        IdempotencyKey.objects.filter(account=self.boss_account, request_id=payload['client_request_id']).update(
            request_fingerprint='', response=None)
        legacy = self.boss_client.post(path + 'convert/', payload, format='json').data
        self.assertEqual((legacy['code'], legacy.get('errorCode')), (409, 'FUND_RESULT_UNAVAILABLE'))
        self.assertNotIn('fundResult', legacy)

    def test_team_uses_two_pre_points_shares(self):
        from orders.points_funding_plan import plan_verified_order
        path, accounts = self.confirmed(team=True)
        payload = self.quote(path)
        self.assertEqual(payload['expected_amount'], 16000)
        result = self.boss_client.post(path + 'convert/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual(list(order.providers.order_by('pk').values_list('provider_account_id', flat=True)),
                         [account.pk for account in accounts])
        self.assertEqual(list(OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)
                              .order_by('segment__slot').values_list('incoming_basis', 'income_at_entry')),
                         [(10000, 8000), (10000, 8000)])
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.provider_income_units, plan.additional_store_funding_units), (16000, 0))

    def test_team_points_shortfall_requires_whole_order_funding(self):
        from orders.points_funding_plan import plan_verified_order
        save_points_terms(group_point_hundredths=10000, group_coin_units=6000,
                          max_ratio_basis_points=5000, version=1, audit=lambda *_: None)
        path, _ = self.confirmed(team=True)
        payload = self.quote(path)
        self.assertEqual(payload['expected_amount'], 14000)
        result = self.boss_client.post(path + 'convert/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.customer_coin_units, plan.provider_income_units, plan.additional_store_funding_units),
                         (14000, 16000, 2000))
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_team_fixed_commission_uses_pre_points_deal_basis(self):
        from orders.points_funding_plan import plan_verified_order
        path, _ = self.confirmed(team=True)
        booking = Reservation.objects.get()
        booking.service.commission_override = True
        booking.service.commission_type = 'FIXED'
        booking.service.commission_fixed = 3000
        booking.service.save(update_fields=['commission_override', 'commission_type', 'commission_fixed'])
        paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment__slot')
        self.assertEqual(list(snapshots.values_list('incoming_basis', 'commission_fixed', 'income_at_entry')),
                         [(10000, 1500, 8500), (10000, 1500, 8500)])
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.provider_income_units, plan.additional_store_funding_units), (17000, 1000))

    def test_sources_are_bound_to_quote_and_store_funds_gift_and_compensation(self):
        from orders.points_funding_plan import plan_verified_order
        self.use_mixed_wallet_sources()
        path, _ = self.confirmed()
        response = self.boss_client.get(path+'quote/', {'preview_points_hundredths': 10000,
            'gift_balance_amount': 1000, 'compensation_balance_amount': 500})
        self.assertEqual(response.data['code'], 0, response.data)
        quote = response.data['data']
        payload = {'discount_selection_token': quote['discount_selection_token'],
            'points_checkout_test_token': quote['points_preview']['checkout_test_token'],
            'expected_amount': 6000, 'gift_balance_amount': 1000, 'compensation_balance_amount': 500,
            'client_request_id': 'reservation-points-sources'}
        denied = self.boss_client.post(path+'convert/', {**payload, 'gift_balance_amount': 0}, format='json')
        self.assertNotEqual(denied.data.get('code'), 0)
        self.assertFalse(Order.objects.exists())
        result = self.boss_client.post(path+'convert/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        order = Order.objects.get(pk=result.data['data']['order']['id'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 4500, 'GIFT': 1000, 'COMPENSATION': 500})
        with transaction.atomic():
            plan = plan_verified_order(order.pk)
        self.assertEqual((plan.principal_units, plan.gift_units, plan.compensation_units,
                          plan.provider_income_units, plan.additional_store_funding_units),
                         (4500, 1000, 500, 8000, 3500))

    def test_points_paths_require_disposable_database_gate(self):
        path, _ = self.confirmed()
        payload = self.quote(path)
        with self.settings(SERVICE_POINTS_CHECKOUT_TESTING=False):
            denied = self.boss_client.post(path+'convert/', payload, format='json')
        self.assertNotEqual(denied.data.get('code'), 0)
        with patch.dict('os.environ', {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            denied = self.boss_client.post(path+'convert/', payload, format='json')
        self.assertNotEqual(denied.data.get('code'), 0)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)

    def test_stale_or_cross_reservation_quote_never_debits(self):
        first, _ = self.confirmed()
        token = self.quote(first)
        # A different confirmed booking with the same service must not accept
        # a signed points quote for the original appointment.
        second_start = slot(hour=12)
        result = self.boss_client.post('/api/orders/reservations/', self.create_payload(
            start_time=second_start.isoformat(), end_time=(second_start+timedelta(hours=1)).isoformat(),
            game_rounds=1), format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        second = f"/api/orders/reservations/{result.data['data']['id']}/"
        self.assertEqual(self.provider_client.post(second+'confirm/', {}, format='json').data['code'], 0)
        denied = self.boss_client.post(second+'convert/', token, format='json')
        self.assertNotEqual(denied.data.get('code'), 0)
        self.profile.points_balance = 5000
        self.profile.save(update_fields=['points_balance'])
        denied = self.boss_client.post(first+'convert/', token, format='json')
        self.assertNotEqual(denied.data.get('code'), 0)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)
        self.assertFalse(PointLedger.objects.filter(kind='ORDER_POINTS_REDEEM').exists())

    def test_point_write_failure_rolls_back_wallet_order_and_assignment(self):
        path, _ = self.confirmed()
        payload = self.quote(path)
        with patch('orders.points_service_payment.change_points', side_effect=RuntimeError('points write failed')):
            with self.assertRaisesMessage(RuntimeError, 'points write failed'):
                self.boss_client.post(path+'convert/', payload, format='json')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.points_balance, 15000)
        self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())
        self.assertEqual(Reservation.objects.get().status, 'CONFIRMED')

    def test_receipt_failure_rolls_back_reservation_payment_and_can_retry(self):
        path, _ = self.confirmed()
        payload = self.quote(path)
        with patch('orders.reservation_views.fund_success', side_effect=RuntimeError('receipt write failed')):
            with self.assertRaisesMessage(RuntimeError, 'receipt write failed'):
                self.boss_client.post(path + 'convert/', payload, format='json')
        self.profile.refresh_from_db()
        self.prepaid_lot.refresh_from_db()
        self.assertEqual((Wallet.objects.get(account=self.boss_account).balance,
                          self.profile.points_balance, self.prepaid_lot.remaining), (100000, 15000, 100000))
        booking = Reservation.objects.get()
        self.assertEqual((booking.status, booking.order_id), (Reservation.Status.CONFIRMED, None))
        self.assertFalse(booking.status_logs.filter(action='CONVERT').exists())
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Transaction.objects.filter(tx_type='PAY').exists())
        self.assertFalse(PointLedger.objects.filter(kind='ORDER_POINTS_REDEEM').exists())
        self.assertFalse(IdempotencyKey.objects.filter(account=self.boss_account,
            request_id=payload['client_request_id']).exists())
        paid = self.boss_client.post(path + 'convert/', payload, format='json').data
        self.assertEqual(paid['code'], 0, paid)
        replay = self.boss_client.post(path + 'convert/', payload, format='json').data
        self.assertEqual((replay['errorCode'], replay['fundResult']), ('FUND_ALREADY_PROCESSED', paid))

    def test_payment_replay_rechecks_access_before_returning_receipt(self):
        from club_accounts.models import ClubAccount
        from orders.reservation_views import ReservationConvertView

        path, _ = self.confirmed()
        payload = self.quote(path)
        paid = self.boss_client.post(path + 'convert/', payload, format='json').data
        self.assertEqual(paid['code'], 0, paid)
        convert = ReservationConvertView._convert

        def revoke_then_replay(view, request, reservation):
            ClubAccount.objects.filter(pk=self.boss_account.pk).update(license_membership_active=False)
            return convert(view, request, reservation)

        with patch.object(ReservationConvertView, '_convert', new=revoke_then_replay):
            denied = self.boss_client.post(path + 'convert/', payload, format='json')
        self.assertEqual((denied.status_code, denied.data['code']), (403, 403), denied.data)
        self.assertNotIn('fundResult', denied.data)
        self.assertEqual(Transaction.objects.filter(order_id=paid['data']['order']['id'], tx_type='PAY').count(), 1)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
                       SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_CONSUMPTION_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_completed_reservation_waits_for_full_funding_before_income_or_vip(self):
        from django.utils import timezone
        from engagement.confirmation import process_order
        from engagement.models import ConsumptionConfirmation
        from orders.models import OrderPointsSettlement
        from orders.tests.test_release_flow import screenshot
        from stores.context import store_context
        from stores.models import Store
        from wallet.models import ProviderReport
        with TemporaryDirectory(prefix='xa-reservation-points-') as media, override_settings(MEDIA_ROOT=media):
            path, accounts = self.confirmed()
            result = self.boss_client.post(path+'convert/', self.quote(path), format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            order = Order.objects.get(pk=result.data['data']['order']['id'])
            player = self.login(accounts[0])
            for action, field in (('start', 'entry_image'), ('complete', 'completion_image')):
                response = player.post(f'/api/orders/orders/{order.pk}/{action}/', {field: screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            report = ProviderReport.objects.get(order=order, provider_account=accounts[0])
            result = player.post(f'/api/wallet/reports/{report.pk}/images/',
                                 {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(result.data['code'], 0, result.data)
            result = player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            admin = make_console_user(['report:view', 'report:audit'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff
            admin.admin_membership.save(update_fields=['account'])
            result = self.login(staff).post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            report.refresh_from_db()
            store, _ = Store.objects.get_or_create(slug='shenglang', defaults={
                'name': '声浪电竞', 'database_alias': 'default', 'status': Store.Status.ACTIVE})
            with store_context(store), patch('django.utils.timezone.now', return_value=report.submitted_at + timedelta(hours=73)):
                self.assertFalse(process_order(order.pk))
            order.refresh_from_db()
            self.assertIsNone(order.settled_at)
            self.assertIsNone(order.consumption_confirmed_at)
            self.assertFalse(OrderPointsSettlement.objects.filter(order=order).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            self.assertFalse(ConsumptionConfirmation.objects.filter(order=order).exists())

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
        SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
        SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
        NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
        REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_negotiated_refund_returns_the_original_points_and_coin_composition(self):
        from orders.models import OrderPointsRefund
        from orders.tests.test_release_flow import screenshot
        from stores.context import store_context
        from stores.models import Store
        store, _ = Store.objects.get_or_create(slug='shenglang', defaults={
            'name': '声浪电竞', 'database_alias': 'default', 'status': Store.Status.ACTIVE})
        with TemporaryDirectory(prefix='xa-reservation-points-refund-') as media, \
                override_settings(MEDIA_ROOT=media), store_context(store):
            path, accounts = self.confirmed()
            paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            order = Order.objects.get(pk=paid.data['data']['order']['id'])
            player = self.login(accounts[0])
            started = player.post(f'/api/orders/orders/{order.pk}/start/',
                                  {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(started.data['code'], 0, started.data)
            admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff
            admin.admin_membership.save(update_fields=['account'])
            client = self.login(staff)
            opened = client.post('/api/admin/aftersales/open-negotiation/', {
                'order_id': order.pk, 'reason': '预约服务协商终止',
                'affected_provider_account_ids': [accounts[0].pk],
                'client_request_id': 'reservation-points-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'reservation-points-proposal', 'expected_version': 0, 'refund_units': 2500,
                'provider_terms': [{'account_id': accounts[0].pk, 'retained_units': 5500}],
                'explanation': '退回部分费用并保留已履约收入'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            for actor in (self.boss_client, player):
                result = actor.post(f'/api/orders/orders/{order.pk}/negotiation-proposal/',
                                    {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(result.data['code'], 0, result.data)
            result = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'reservation-points-refund'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            refund = OrderPointsRefund.objects.get(redemption__order=order)
            self.assertEqual((refund.equivalent_units, refund.coin_units, refund.points_hundredths), (2500, 1500, 2500))
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.points_balance, 7500)
            self.assertEqual(Wallet.objects.get(account=self.boss_account).balance, 95500)
            from orders.points_funding_evidence import verify_order_funding
            with transaction.atomic():
                funding = verify_order_funding(order.pk)
            self.assertEqual((funding.required, funding.allocated, funding.remaining), (1000, 0, 1000))
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            order.refresh_from_db()
            self.assertIsNone(order.consumption_confirmed_at)
            replay = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'reservation-points-refund'}, format='json')
            self.assertEqual(replay.data['code'], 0, replay.data)
            self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)

    @override_settings(SERVICE_POINTS_REPORT_TESTING=True, SERVICE_POINTS_FUNDING_TESTING=True,
        SERVICE_POINTS_SETTLEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_AGREEMENT_TESTING=True, SERVICE_POINTS_NEGOTIATION_EXECUTION_TESTING=True,
        SERVICE_POINTS_NEGOTIATION_POSTING_TESTING=True, SERVICE_POINTS_NEGOTIATION_TERMINATION_TESTING=True,
        SERVICE_POINTS_FUNDING_RELEASE_TESTING=True, SERVICE_POINTS_REFUNDED_FUNDING_TESTING=True,
        NEGOTIATION_PROPOSAL_ENABLED=True, NEGOTIATION_REFUND_EXECUTION_ENABLED=True,
        REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_team_points_refund_ends_only_affected_seat_and_other_continues(self):
        from orders.models import AfterSale, OrderPointsRefund
        from orders.reservation_services import find_conflicting_reservations
        from orders.tests.test_release_flow import screenshot
        from stores.context import store_context
        from stores.models import Store
        store, _ = Store.objects.get_or_create(slug='shenglang', defaults={
            'name': '声浪电竞', 'database_alias': 'default', 'status': Store.Status.ACTIVE})
        with TemporaryDirectory(prefix='xa-reservation-team-refund-') as media, \
                override_settings(MEDIA_ROOT=media), store_context(store):
            path, accounts = self.confirmed(team=True)
            paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            order = Order.objects.get(pk=paid.data['data']['order']['id'])
            players = [self.login(account) for account in accounts]
            for player in players:
                response = player.post(f'/api/orders/orders/{order.pk}/start/',
                                       {'entry_image': screenshot()}, format='multipart')
                self.assertEqual(response.data['code'], 0, response.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'IN_SERVICE')
            admin = make_console_user(['order:view', 'support:edit', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            client = self.login(staff)
            opened = client.post('/api/admin/aftersales/open-negotiation/', {
                'order_id': order.pk, 'reason': '双陪预约中一位协商终止',
                'affected_provider_account_ids': [accounts[0].pk], 'client_request_id': 'reservation-team-case'}, format='json')
            self.assertEqual(opened.data['code'], 0, opened.data)
            case_id = opened.data['data']['id']
            proposal = client.post(f'/api/admin/aftersales/{case_id}/negotiation-proposal/', {
                'request_id': 'reservation-team-proposal', 'expected_version': 0, 'refund_units': 2500,
                'provider_terms': [{'account_id': accounts[0].pk, 'retained_units': 5500}],
                'explanation': '第一位陪玩终止并退还对应费用'}, format='json')
            self.assertEqual(proposal.data['code'], 0, proposal.data)
            proposal_id = proposal.data['data']['id']
            consent_url = f'/api/orders/orders/{order.pk}/negotiation-proposal/'
            self.assertEqual(players[1].post(consent_url, {'proposal_id': proposal_id, 'accepted': True},
                                             format='json').status_code, 404)
            for actor in (self.boss_client, players[0]):
                agreed = actor.post(consent_url, {'proposal_id': proposal_id, 'accepted': True}, format='json')
                self.assertEqual(agreed.data['code'], 0, agreed.data)
            execution = client.post(f'/api/admin/aftersales/{case_id}/negotiation-execution/', {
                'proposal_id': proposal_id, 'client_request_id': 'reservation-team-execute'}, format='json')
            self.assertEqual(execution.data['code'], 0, execution.data)
            refund = OrderPointsRefund.objects.get(redemption__order=order)
            self.assertEqual((refund.equivalent_units, refund.coin_units, refund.points_hundredths), (2500, 2000, 1250))
            order.refresh_from_db()
            self.assertEqual((order.status, order.payment_status, order.provider_account_id),
                             ('IN_SERVICE', 'PARTIALLY_REFUNDED', accounts[1].pk))
            assignments = {row.provider_account_id: row for row in order.providers.all()}
            self.assertIsNotNone(assignments[accounts[0].pk].released_at)
            self.assertIsNone(assignments[accounts[1].pk].released_at)
            booking = Reservation.objects.get(order=order)
            self.assertFalse(find_conflicting_reservations(accounts[0], booking.start_time, booking.end_time).exists())
            self.assertTrue(find_conflicting_reservations(accounts[1], booking.start_time, booking.end_time).exists())
            self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
            continued = players[1].post(f'/api/orders/orders/{order.pk}/complete/',
                                        {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(continued.data['code'], 0, continued.data)
            order.refresh_from_db()
            self.assertEqual(order.status, 'COMPLETED')
            reopened = self.boss_client.post(f'/api/orders/orders/{order.pk}/aftersale/', {
                'reason': '已确认范围不得重开', 'affected_provider_account_ids': [accounts[0].pk],
                'client_request_id': 'reservation-team-closed'}, format='json')
            self.assertNotEqual(reopened.data.get('code', 400), 0)
            low = self.boss_client.post('/api/orders/evaluate/', {'order_id': order.pk,
                'provider_account_id': accounts[1].pk, 'score': 1, 'skill_score': 5,
                'attitude_score': 5, 'communication_score': 5}, format='json')
            self.assertEqual(low.data['code'], 0, low.data)
            case = AfterSale.objects.get(pk=case_id)
            self.assertEqual((case.status, case.affected_provider_account_ids), ('OPEN', [accounts[1].pk]))

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True)
    def test_staff_refunds_unstarted_team_to_original_sources_once(self):
        from coupons.models import Coupon, UserCoupon
        from orders.models import OrderPointsRefund
        from orders.reservation_services import find_conflicting_reservations
        from orders.points_service_refund import verify_refund_history_locked
        coupon = Coupon.objects.create(name='预约金额券', discount_type='DIRECT', amount=1000)
        owned = UserCoupon.objects.create(account=self.boss_account, coupon=coupon)
        path, accounts = self.confirmed(team=True)
        payload = self.quote(path)
        self.assertEqual(payload['expected_amount'], 15000)
        paid = self.boss_client.post(path+'convert/', payload, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        booking = Reservation.objects.get(order=order)
        admin = make_console_user(['order:view', 'order:refund'])
        staff = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
        client = self.login(staff)
        url = f'/api/admin/orders/{order.pk}/refund/'
        from orders.models import OrderFinancialLock
        mutex_count = OrderFinancialLock.objects.count()
        preview = client.get(f'/api/admin/orders/{order.pk}/refund-preview/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        self.assertEqual(OrderFinancialLock.objects.count(), mutex_count)
        self.assertEqual((preview.data['data']['refund_equivalent_units'], preview.data['data']['refund_coin_units'],
                          preview.data['data']['refund_points_hundredths']), (19000, 15000, 10000))
        request = {'reason': '预约开始前协商取消', 'client_request_id': 'reservation-prestart-refund'}
        partial = client.post(url, {**request, 'refund_amount': 1000}, format='json')
        self.assertNotEqual(partial.data.get('code'), 0)
        response = client.post(url, request, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db(); self.profile.refresh_from_db(); owned.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual((Wallet.objects.get(account=self.boss_account).balance, self.profile.points_balance), (100000, 15000))
        self.assertEqual(owned.status, 'UNUSED')
        evidence = OrderPointsRefund.objects.get(redemption__order=order)
        self.assertEqual((evidence.equivalent_units, evidence.coin_units, evidence.points_hundredths, evidence.is_final),
                         (19000, 15000, 10000, True))
        with transaction.atomic():
            self.assertEqual(verify_refund_history_locked(evidence.refund_id).pk, evidence.pk)
        for account in accounts:
            self.assertFalse(find_conflicting_reservations(account, booking.start_time, booking.end_time).exists())
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())
        self.assertEqual(client.post(url, request, format='json').data['code'], 0)
        self.assertEqual(OrderPointsRefund.objects.filter(redemption__order=order).count(), 1)
        changed = client.post(url, {**request, 'reason': '不同原因'}, format='json')
        self.assertNotEqual(changed.data.get('code'), 0)

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True, SERVICE_POINTS_REPORT_TESTING=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_prestart_refund_rejects_team_with_one_member_already_entered(self):
        from orders.tests.test_release_flow import screenshot
        with TemporaryDirectory(prefix='xa-team-partial-entry-') as media, override_settings(MEDIA_ROOT=media):
            path, accounts = self.confirmed(team=True)
            paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
            self.assertEqual(paid.data['code'], 0, paid.data)
            order = Order.objects.get(pk=paid.data['data']['order']['id'])
            entered = self.login(accounts[0]).post(f'/api/orders/orders/{order.pk}/start/',
                {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(entered.data['code'], 0, entered.data)
            order.refresh_from_db(); self.assertEqual(order.status, 'GRABBED')
            admin = make_console_user(['order:view', 'order:refund'])
            staff = get_or_create_account_for_legacy_user(admin)
            admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
            denied = self.login(staff).post(f'/api/admin/orders/{order.pk}/refund/', {
                'reason': '应先协商已开始部分', 'client_request_id': 'already-entered'}, format='json')
            self.assertNotEqual(denied.data.get('code'), 0)
            order.refresh_from_db(); self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
            self.assertFalse(order.refund_records.exists())

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True)
    def test_prestart_refund_failure_rolls_back_assets_and_can_retry(self):
        path, _ = self.confirmed()
        paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        admin = make_console_user(['order:view', 'order:refund'])
        staff = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
        client = self.login(staff)
        url = f'/api/admin/orders/{order.pk}/refund/'
        request = {'reason': '预约未开始取消', 'client_request_id': 'rollback-prestart'}
        with patch('coupons.benefits.restore_order_coupons', side_effect=RuntimeError('coupon write failed')):
            with self.assertRaisesMessage(RuntimeError, 'coupon write failed'):
                client.post(url, request, format='json')
        self.profile.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.profile.points_balance, Wallet.objects.get(account=self.boss_account).balance), (5000, 94000))
        self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
        self.assertFalse(order.refund_records.exists())
        self.assertEqual(client.post(url, request, format='json').data['code'], 0)
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.points_balance, Wallet.objects.get(account=self.boss_account).balance), (15000, 100000))

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True)
    def test_prestart_refund_restores_each_wallet_source(self):
        self.use_mixed_wallet_sources()
        path, _ = self.confirmed()
        quote = self.boss_client.get(path+'quote/', {'preview_points_hundredths': 10000,
            'gift_balance_amount': 1000, 'compensation_balance_amount': 500}).data['data']
        paid = self.boss_client.post(path+'convert/', {'discount_selection_token': quote['discount_selection_token'],
            'points_checkout_test_token': quote['points_preview']['checkout_test_token'], 'expected_amount': 6000,
            'gift_balance_amount': 1000, 'compensation_balance_amount': 500,
            'client_request_id': 'refund-sources-checkout'}, format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        admin = make_console_user(['order:view', 'order:refund'])
        staff = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
        client = self.login(staff)
        preview = client.get(f'/api/admin/orders/{order.pk}/refund-preview/').data['data']
        self.assertEqual((preview['prepaid_units'], preview['gift_units'], preview['compensation_units']), (4500, 1000, 500))
        refunded = client.post(f'/api/admin/orders/{order.pk}/refund/', {
            'reason': '预约取消', 'client_request_id': 'refund-sources'}, format='json')
        self.assertEqual(refunded.data['code'], 0, refunded.data)
        self.assertEqual(dict(PointFundingLot.objects.values_list('kind', 'remaining')),
                         {'PREPAID': 97000, 'GIFT': 2000, 'COMPENSATION': 1000})
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'refunded_amount')),
                         {'PREPAID': 4500, 'GIFT': 1000, 'COMPENSATION': 500})

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True,
                       SERVICE_POINTS_RESERVATION_CANCEL_FUNDING_TESTING=True,
                       SERVICE_POINTS_FUNDING_TESTING=True, SERVICE_POINTS_FUNDING_RELEASE_TESTING=True)
    def test_prestart_refund_does_not_ignore_any_recorded_funding(self):
        from uuid import uuid4

        from django.utils import timezone
        from orders.models import OrderPointsFundingAllocation, ReservationPointsCancelFundingRelease
        from orders.points_funding_evidence import verified_order_allocations_locked
        from users.models import PassRefundRepaymentReceipt
        path, _ = self.confirmed()
        paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        receipt = PassRefundRepaymentReceipt.objects.create(request_key='test-receipt', external_reference='test-funds',
            amount=1000, received_at=timezone.now(), evidence='隔离测试工装，未发生线下到账', registered_by='test')
        allocation = OrderPointsFundingAllocation.objects.create(request_key=f'points:{uuid4().hex}', receipt=receipt,
            redemption=OrderPointsRedemption.objects.get(order=order), amount=1000, allocated_by='test')
        admin = make_console_user(['order:view', 'order:refund'])
        staff = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
        with patch('orders.points_funding_evidence.verified_order_allocations_locked',
                   wraps=verified_order_allocations_locked) as verify:
            denied = self.login(staff).post(f'/api/admin/orders/{order.pk}/refund/', {
                'reason': '已有补资须核对', 'client_request_id': 'funded-prestart'}, format='json')
        verify.assert_called_once()
        self.assertEqual((denied.data['errorCode'], denied.data['msg']),
                         ('REFUND_NOT_ALLOWED', '预约补资分配或释放凭证待核对'))
        order.refresh_from_db(); self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
        self.profile.refresh_from_db()
        self.prepaid_lot.refresh_from_db()
        self.assertEqual((Wallet.objects.get(account=self.boss_account).balance,
                          self.profile.points_balance, self.prepaid_lot.remaining), (94000, 5000, 94000))
        self.assertFalse(order.refund_records.exists())
        self.assertFalse(ReservationPointsCancelFundingRelease.objects.filter(allocation=allocation).exists())

    @override_settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=True)
    def test_prestart_refund_requires_refund_permission_and_isolated_gate(self):
        path, _ = self.confirmed()
        paid = self.boss_client.post(path+'convert/', self.quote(path), format='json')
        self.assertEqual(paid.data['code'], 0, paid.data)
        order = Order.objects.get(pk=paid.data['data']['order']['id'])
        admin = make_console_user(['order:view', 'order:refund'])
        staff = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff; admin.admin_membership.save(update_fields=['account'])
        readonly = make_console_user(['order:view'])
        viewer = get_or_create_account_for_legacy_user(readonly)
        readonly.admin_membership.account = viewer; readonly.admin_membership.save(update_fields=['account'])
        url = f'/api/admin/orders/{order.pk}/refund/'
        payload = {'reason': '核对权限与隔离', 'client_request_id': 'isolated-prestart'}
        self.assertNotEqual(self.login(viewer).post(url, payload, format='json').data.get('code'), 0)
        with self.settings(SERVICE_POINTS_RESERVATION_CANCEL_TESTING=False):
            self.assertNotEqual(self.login(staff).post(url, payload, format='json').data.get('code'), 0)
        with patch.dict('os.environ', {'DJANGO_SETTINGS_MODULE': 'config.settings'}):
            self.assertNotEqual(self.login(staff).post(url, payload, format='json').data.get('code'), 0)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('GRABBED', 'PAID'))
        self.assertFalse(order.refund_records.exists())
