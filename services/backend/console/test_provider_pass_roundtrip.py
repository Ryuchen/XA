"""A valid pass configuration round-trips; invalid QA fixtures remain rejected."""
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from wallet.models import (PASS_DAILY_PRICE_KEY_PREFIX, PASS_DELAY_SECONDS_KEY_PREFIX,
                           SystemConfig, Transaction, Wallet)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderPassConfigurationRoundtripTest(APITestCase):
    url = '/api/admin/config/provider-pass'

    def setUp(self):
        staff = make_console_user(['escort:view', 'escort:edit'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
        SystemConfig.objects.filter(key__startswith=PASS_DELAY_SECONDS_KEY_PREFIX).delete()
        SystemConfig.objects.filter(key__startswith=PASS_DAILY_PRICE_KEY_PREFIX).delete()

    def current(self):
        response = self.client.get(self.url)
        self.assertEqual((response.status_code, response.data['code']), (200, 0), response.data)
        return response.data['data']

    def funds(self):
        return (list(Wallet.objects.order_by('pk').values_list('pk', 'balance', 'frozen_amount')),
                Transaction.objects.count())

    def test_default_get_payload_can_be_saved_unchanged_without_moving_funds(self):
        snapshot = self.current()
        self.assertEqual([row['delay_seconds'] for row in snapshot['products']], [0, 30, 60, 120])
        self.assertEqual(snapshot['no_pass_delay_seconds'], 300)
        before = self.funds()
        saved = self.client.put(self.url, snapshot, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        self.assertEqual(self.current(), snapshot)
        self.assertEqual(self.funds(), before)

    def test_zero_delay_qa_fixture_sets_all_five_tiers_and_round_trips_without_changing_prices(self):
        original = self.current()
        for tier in ('BLACK', 'GOLD', 'SILVER', 'BRONZE', 'NONE'):
            SystemConfig.objects.create(key=PASS_DELAY_SECONDS_KEY_PREFIX + tier, value='0')
        snapshot = self.current()
        self.assertEqual([row['delay_seconds'] for row in snapshot['products']], [0, 0, 0, 0])
        self.assertEqual(snapshot['no_pass_delay_seconds'], 0)
        self.assertEqual([row['daily_price'] for row in snapshot['products']],
                         [row['daily_price'] for row in original['products']])
        saved = self.client.put(self.url, snapshot, format='json')
        self.assertEqual((saved.status_code, saved.data['code']), (200, 0), saved.data)
        self.assertEqual(self.current(), snapshot)

    def test_only_changing_no_pass_to_zero_is_not_a_valid_fixture_and_cannot_silently_rewrite_rules(self):
        SystemConfig.objects.create(key=PASS_DELAY_SECONDS_KEY_PREFIX + 'NONE', value='0')
        malformed = self.current()
        before = list(SystemConfig.objects.order_by('key').values_list('key', 'value'))
        denied = self.client.put(self.url, malformed, format='json')
        self.assertEqual((denied.status_code, denied.data['code']), (400, 400), denied.data)
        self.assertIn('可见延迟', denied.data['msg'])
        self.assertEqual(list(SystemConfig.objects.order_by('key').values_list('key', 'value')), before)
