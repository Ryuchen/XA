from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot
from engagement.services import config, wallet_funding_parts
from orders.models import Order, Reservation
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase
from wallet.models import Wallet


@override_settings(BOSS_FUNDING_SELECTION_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORDINARY_SERVICE_FUNDING_ENABLED=True, ORIGINAL_ATTRIBUTION_ENABLED=True,
                   SEGMENT_ECONOMICS_CAPTURE_ENABLED=True, PLATFORM_IDENTITY_ENABLED=True,
                   PLATFORM_PASS_REPAYMENT_ENABLED=True,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ReservationFundingApiTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        config()
        self.boss_client = self.login(self.boss_account)
        self.provider_client = self.login(self.provider_account)
        self.staff_client = self.login(self.staff_account)
        self.wallet = Wallet.objects.get(account=self.boss_account)
        for kind, amount in [('PREPAID', 99000), ('GIFT', 600), ('COMPENSATION', 400)]:
            PointFundingLot.objects.create(account=self.boss_account, source_key=f'reserve-funding-{kind}',
                                           kind=kind, remaining=amount)

    def login(self, account):
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def confirmed(self):
        response = self.boss_client.post(LIST_URL, self.create_payload(), format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        path = f"{LIST_URL}{response.data['data']['id']}/"
        confirmed = self.provider_client.post(path + 'confirm/', {}, format='json')
        self.assertEqual(confirmed.data['code'], 0, confirmed.data)
        return path

    def test_boss_quote_and_conversion_use_selected_sources(self):
        path = self.confirmed()
        selected = {'gift_balance_amount': 200, 'compensation_balance_amount': 100}
        response = self.boss_client.get(path + 'quote/', selected)
        self.assertEqual(response.data['code'], 0, response.data)
        quote = response.data['data']
        self.assertTrue(quote['funding_selection_enabled'])
        self.assertTrue(quote['balance_sufficient'])
        converted = self.boss_client.post(path + 'convert/', {**selected, 'expected_amount': quote['amount'],
            'expected_service': quote['service'], 'client_request_id': 'reservation-funding-owner'}, format='json')
        self.assertEqual(converted.data['code'], 0, converted.data)
        order = Order.objects.get(pk=converted.data['data']['order']['id'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 1700, 'GIFT': 200, 'COMPENSATION': 100})
        self.assertIsNotNone(order.funding_terms)
        self.wallet.refresh_from_db()
        self.assertEqual(wallet_funding_parts(self.wallet),
                         {'prepaid_balance': 97300, 'gift_balance': 400, 'compensation_balance': 300})
        self.assertEqual(Reservation.objects.get(order=order).status, 'CONVERTED')

    def test_insufficient_selected_source_does_not_convert_or_debit(self):
        path = self.confirmed()
        quote = self.boss_client.get(path + 'quote/', {'gift_balance_amount': 700}).data
        self.assertEqual(quote['code'], 0, quote)
        self.assertFalse(quote['data']['balance_sufficient'])
        response = self.boss_client.post(path + 'convert/', {'gift_balance_amount': 700,
            'client_request_id': 'reservation-funding-insufficient'}, format='json')
        self.assertNotEqual(response.data['code'], 0)
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.boss_client.get(path).data['data']['status'], 'CONFIRMED')

    def test_huge_or_fractional_input_rejected_without_mutation(self):
        path = self.confirmed()
        for value in ('9' * 5000, '1.5', '-10'):
            response = self.boss_client.get(path + 'quote/', {'gift_balance_amount': value})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.exists())
        self.wallet.refresh_from_db(); self.assertEqual(self.wallet.balance, 100000)

    def test_staff_conversion_requires_and_records_boss_confirmation(self):
        path = self.confirmed()
        selected = {'gift_balance_amount': 200, 'compensation_balance_amount': 100,
                    'client_request_id': 'reservation-staff-funding'}
        denied = self.staff_client.post(path + 'convert/', selected, format='json')
        self.assertEqual(denied.data['code'], 400, denied.data)
        self.assertFalse(Order.objects.exists())
        converted = self.staff_client.post(path + 'convert/', {**selected,
            'boss_confirmation': '老板已确认赠送20、赔付10，余款本金支付'}, format='json')
        self.assertEqual(converted.data['code'], 0, converted.data)
        order = Order.objects.get(pk=converted.data['data']['order']['id'])
        self.assertEqual(order.funding_confirmation['actor_account_id'], self.staff_account.pk)
        self.assertIn('老板已确认', order.funding_confirmation['confirmation'])
        self.assertEqual(dict(order.funding_allocations.values_list('kind', 'amount')),
                         {'PREPAID': 1700, 'GIFT': 200, 'COMPENSATION': 100})
