import os
from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Store
from .serializers import StoreUpdateSerializer, public_store


@override_settings(ALLOWED_HOSTS=['*'])
class ProviderNamePrefixTest(TestCase):
    def setUp(self):
        self.shenglang, _ = Store.objects.update_or_create(slug='shenglang', defaults={
            'name': '声浪电竞', 'database_alias': 'default', 'provider_name_prefix': 'SL',
            'status': Store.Status.ACTIVE, 'initialized_at': timezone.now(),
        })
        self.xinan = Store.objects.create(slug='xinan', name='兴安电竞', database_alias='store_b',
            entity_prefix='XI', provider_name_prefix='XA', status=Store.Status.ACTIVE, initialized_at=timezone.now())

    def test_public_value_is_separate_from_business_identifier_prefix(self):
        row = public_store(self.xinan)
        self.assertEqual((row['entity_prefix'], row['provider_name_prefix']), ('XI', 'XA'))
        client = APIClient(HTTP_HOST='sl.esports.club')
        response = client.get('/api/stores/current/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['provider_name_prefix'], 'SL')

    def test_update_validates_explicit_display_prefix_without_changing_identifier(self):
        serializer = StoreUpdateSerializer(self.xinan, data={'provider_name_prefix': ' xz '}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.xinan.refresh_from_db()
        self.assertEqual((self.xinan.provider_name_prefix, self.xinan.entity_prefix), ('XZ', 'XI'))
        for value in ('', 'X', 'XA1', '星星', 'X' * 9):
            with self.subTest(value=value):
                invalid = StoreUpdateSerializer(self.xinan, data={'provider_name_prefix': value}, partial=True)
                self.assertFalse(invalid.is_valid())

    def test_seed_only_confirmed_store_slugs_and_never_overwrites_existing_choice(self):
        extra = Store.objects.create(slug='third-store', name='第三店', database_alias='store_c')
        Store.objects.filter(pk__in=[self.shenglang.pk, self.xinan.pk]).update(provider_name_prefix='')
        seed = import_module('stores.migrations.0011_store_provider_name_prefix').seed_known_store_names
        schema = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        seed(apps, schema)
        self.shenglang.refresh_from_db(); self.xinan.refresh_from_db(); extra.refresh_from_db()
        self.assertEqual((self.shenglang.provider_name_prefix, self.xinan.provider_name_prefix, extra.provider_name_prefix), ('SL', 'XA', ''))
        self.xinan.provider_name_prefix = 'ZZ'; self.xinan.save(update_fields=['provider_name_prefix'])
        seed(apps, schema)
        self.xinan.refresh_from_db()
        self.assertEqual(self.xinan.provider_name_prefix, 'ZZ')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_server_message_label_is_scoped_and_does_not_rewrite_name(self):
        from .context import store_context
        from .display import provider_display_name
        original = 'SL-sl-小星'
        with store_context(self.shenglang):
            self.assertEqual(provider_display_name(original), 'SL-小星')
        with store_context(self.xinan):
            self.assertEqual(provider_display_name('小星'), 'XA-小星')
        self.assertEqual(provider_display_name(original), original)


@skipUnless('store_b' in settings.DATABASES, 'requires two isolated databases')
@override_settings(ALLOWED_HOSTS=['*'], PLATFORM_IDENTITY_ENABLED=True, LEGACY_FORCE_AUTH_COMPAT=False)
class ProviderNamePrefixStoreIsolationTest(TransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}
    reset_sequences = True

    def test_platform_edit_and_public_directory_keep_store_names_and_profiles_separate(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from orders.tests.factories import make_provider
        from .context import resolve_store, store_context
        from .models import PlatformAccount, PlatformRoleGrant
        from .platform_auth import issue_tokens
        from .tests_hosts import xinan_store

        first = resolve_store()
        first.provider_name_prefix = 'SL'; first.save(update_fields=['provider_name_prefix'])
        second = xinan_store()
        second.entity_prefix = 'XI'; second.provider_name_prefix = 'XA'
        second.save(update_fields=['entity_prefix', 'provider_name_prefix'])
        provider_ids = []
        for store in (first, second):
            with store_context(store):
                user = make_provider(display_name='小星')
                account = get_or_create_account_for_legacy_user(user)
                profile = user.escort_profile; profile.account = account; profile.save(update_fields=['account'])
                provider_ids.append(account.pk)
        self.assertEqual(provider_ids[0], provider_ids[1])
        client = APIClient()
        for host, expected in [('sl.esports.club', 'SL'), ('xa.esports.club', 'XA')]:
            self.assertEqual(client.get('/api/stores/current/', HTTP_HOST=host).data['data']['provider_name_prefix'], expected)
            listing = client.get('/api/users/escorts/?directory=1', HTTP_HOST=host)
            self.assertEqual(listing.status_code, 200, listing.data)
            self.assertEqual([row['nickname'] for row in listing.data['data']], ['小星'])
            row = listing.data['data'][0]
            self.assertEqual((row['display_name'], row['store_prefix']), (expected + '-小星', expected))
            detail = client.get(f'/api/users/escorts/{row["id"]}/', HTTP_HOST=host)
            self.assertEqual(detail.status_code, 200, detail.data)
            self.assertEqual((detail.data['data']['nickname'], detail.data['data']['display_name'],
                              detail.data['data']['store_prefix']), ('小星', expected + '-小星', expected))
        owner = PlatformAccount(username='prefix-owner', phone='13800001111', is_phone_verified=True)
        owner.set_password('Synthetic-Platform-Prefix-2026!'); owner.save()
        PlatformRoleGrant.objects.create(account=owner, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(owner, 'SMS', owner.phone)
        platform = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        old_version = second.updated_at.isoformat()
        result = platform.patch(f'/api/admin/platform/store-management/{second.pk}',
            {'provider_name_prefix': 'xy', 'expected_updated_at': old_version}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['data']['provider_name_prefix'], 'XY')
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.provider_name_prefix, second.provider_name_prefix, second.entity_prefix), ('SL', 'XY', 'XI'))
        self.assertEqual(client.get('/api/stores/current/', HTTP_HOST='xa.esports.club').data['data']['provider_name_prefix'], 'XY')
        changed = client.get('/api/users/escorts/?directory=1', HTTP_HOST='xa.esports.club').data['data'][0]
        self.assertEqual((changed['nickname'], changed['display_name'], changed['store_prefix']), ('小星', 'XY-小星', 'XY'))
        unchanged = client.get('/api/users/escorts/?directory=1', HTTP_HOST='sl.esports.club').data['data'][0]
        self.assertEqual((unchanged['display_name'], unchanged['store_prefix']), ('SL-小星', 'SL'))
        stale = platform.patch(f'/api/admin/platform/store-management/{second.pk}',
            {'provider_name_prefix': 'ZZ', 'expected_updated_at': old_version}, format='json')
        self.assertEqual(stale.status_code, 409)
        finance = PlatformAccount(username='prefix-finance', phone='13800002222', is_phone_verified=True)
        finance.set_password('Synthetic-Platform-Prefix-2026!'); finance.save()
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        finance_token, _ = issue_tokens(finance, 'SMS', finance.phone)
        finance_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + finance_token)
        denied = finance_client.patch(f'/api/admin/platform/store-management/{second.pk}',
            {'provider_name_prefix': 'ZZ', 'expected_updated_at': second.updated_at.isoformat()}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(client.get('/api/stores/current/?store=xinan', HTTP_HOST='sl.esports.club').status_code, 400)
        with store_context(second):
            from users.models import EscortProfile
            self.assertEqual(EscortProfile.objects.get().display_name, '小星')
