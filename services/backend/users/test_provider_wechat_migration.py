from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connections
from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.models import Order
from orders.tests.factories import make_order, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortProfile


class LegacyWechatMigrationIsolationTests(TestCase):
    databases = {'default', 'store_b'}

    def test_migration_only_marks_accepted_providers_in_target_database(self):
        first, second = resolve_store(), xinan_store()
        ids = {}
        for store in (first, second):
            with store_context(store):
                provider = make_provider(deposit_mode='EARNINGS')
                account = get_or_create_account_for_legacy_user(provider)
                account.openid, account.is_openid_bound = 'same-wechat-openid', True
                account.save(update_fields=['openid', 'is_openid_bound'])
                profile = provider.escort_profile
                profile.account = account
                profile.save(update_fields=['account'])
                make_order(make_user(), service=make_service(), provider=provider, provider_account=account, status=Order.Status.COMPLETED)
                ids[store.slug] = profile.pk
        migrate = import_module('users.migrations.0039_legacy_wechat_avatar_exemption').preserve_verified_legacy_providers
        with store_context(first):
            migrate(apps, SimpleNamespace(connection=connections['default']))
            self.assertTrue(EscortProfile.objects.get(pk=ids[first.slug]).wechat_avatar_legacy_exempt)
        with store_context(second):
            self.assertFalse(EscortProfile.objects.get(pk=ids[second.slug]).wechat_avatar_legacy_exempt)
            migrate(apps, SimpleNamespace(connection=connections['store_b']))
            self.assertTrue(EscortProfile.objects.get(pk=ids[second.slug]).wechat_avatar_legacy_exempt)
