from unittest import skipUnless

from django.conf import settings
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.services import config
from orders.discount_recommendation import service_discount_options
from orders.discount_selection import preview_selected_discount, signed_option
from orders.tests.factories import make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class HourlyReservationStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_hourly_price_and_signed_quote_are_store_scoped(self):
        stores = (resolve_store(), xinan_store())
        rows = []
        for store, price in zip(stores, (10100, 20000)):
            with store_context(store):
                config()
                account = get_or_create_account_for_legacy_user(make_user())
                service = make_service(price=price, price_unit='小时')
                selected = service_discount_options(account, service, 1, 15)[0]
                token = signed_option(account, service, 1, selected, 15)
                rows.append((account.pk, service.pk, selected.amount, token))
        self.assertEqual(rows[0][:2], rows[1][:2])
        self.assertEqual([row[2] for row in rows], [2530, 5000])
        with store_context(stores[1]):
            from club_accounts.models import ClubAccount
            from orders.models import ServiceItem
            account = ClubAccount.objects.get(pk=rows[1][0])
            service = ServiceItem.objects.get(pk=rows[1][1])
            with self.assertRaises(ValidationError):
                preview_selected_discount(account, service, 1, rows[0][3], 15)
