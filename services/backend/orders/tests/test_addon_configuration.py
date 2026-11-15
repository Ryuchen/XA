from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import GameCategory, ServiceAddonConfiguration
from orders.tests.factories import make_console_user, make_service, make_service_category
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store


def staff_client(permissions):
    user = make_console_user(permissions)
    account = get_or_create_account_for_legacy_user(user)
    user.admin_membership.account = account
    user.admin_membership.save(update_fields=['account'])
    return APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonConfigurationTests(APITestCase):
    def setUp(self):
        self.client = staff_client(['service:view', 'service:edit'])
        self.reader = staff_client(['service:view'])
        self.denied = staff_client(['order:view'])
        self.dictionary_editor = staff_client(['dictionary:edit'])
        self.game = GameCategory.objects.create(name='瓦罗兰特')
        self.service = make_service(game_category=self.game)
        self.addon = make_service(name='1 陪 2', game_category=self.game, is_addon=True)
        self.url = f'/api/admin/config/service-addons/{self.service.pk}/{self.addon.pk}/'
        self.list_url = f'/api/admin/config/service-addons/{self.service.pk}/'
        self.data = {'version': 0, 'enabled': True, 'conditions': '需由老板选择小时数'}

    def test_permissions_versions_read_and_audit(self):
        self.assertEqual(self.denied.get(self.list_url).status_code, 403)
        self.assertEqual(self.reader.put(self.url, self.data, format='json').status_code, 403)
        self.assertEqual(self.dictionary_editor.put(self.url, self.data, format='json').status_code, 403)
        first = self.client.put(self.url, self.data, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data']['status'], 'CONFIGURATION_ONLY')
        self.assertEqual(self.client.put(self.url, self.data, format='json').status_code, 409)
        second = self.client.put(self.url, {**self.data, 'version': 1, 'enabled': False}, format='json')
        self.assertEqual(second.data['data']['configuration']['version'], 2)
        listing = self.reader.get(self.list_url).data['data']
        self.assertEqual((listing['items'][0]['enabled'], listing['items'][0]['price']), (False, self.addon.price))
        logs = list(AdminAuditLog.objects.filter(resource='service-addon-configuration').order_by('pk'))
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[1].request_body['before']['version'], 1)
        self.assertEqual(logs[1].request_body['after']['version'], 2)

    def test_validation_rejects_foreign_games_gifts_and_invalid_inputs(self):
        other = make_service(game_category=GameCategory.objects.create(name='其他'), is_addon=True)
        gift = make_service(game_category=self.game, service_category=make_service_category(is_gift=True))
        for url in (f'/api/admin/config/service-addons/{self.service.pk}/{other.pk}/',
                    f'/api/admin/config/service-addons/{gift.pk}/{self.addon.pk}/',
                    f'/api/admin/config/service-addons/{self.addon.pk}/{self.addon.pk}/'):
            self.assertEqual(self.client.put(url, self.data, format='json').status_code, 400)
        for data in ({**self.data, 'version': True}, {**self.data, 'enabled': 1},
                     {**self.data, 'conditions': 'x'*501}, {**self.data, 'price': 1}):
            self.assertEqual(self.client.put(self.url, data, format='json').status_code, 400)
        self.assertFalse(ServiceAddonConfiguration.objects.exists())

    def test_audit_failure_rolls_back_configuration(self):
        with patch('console.addon_configuration.AdminAuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.put(self.url, self.data, format='json')
        self.assertFalse(ServiceAddonConfiguration.objects.exists())

    def test_price_options_support_hour_and_round_with_a_shared_version(self):
        self.assertEqual(self.client.put(self.url, self.data, format='json').status_code, 200)
        url = self.url + 'price-options/'
        payload = {'version': 1, 'options': [
            {'unit': 'HOUR', 'price': 500, 'enabled': True},
            {'unit': 'ROUND', 'price': 400, 'enabled': True}]}
        self.assertEqual(self.reader.put(url, payload, format='json').status_code, 403)
        saved = self.client.put(url, payload, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['data']['version'], 2)
        self.assertEqual(saved.data['data']['options'], payload['options'])
        self.assertEqual(self.reader.get(url).data['data']['options'], payload['options'])
        self.assertEqual(self.client.put(self.url, {**self.data, 'version': 1}, format='json').status_code, 409)
        self.assertEqual(self.client.put(url, payload, format='json').status_code, 409)
        audit = AdminAuditLog.objects.get(resource='service-addon-price-options')
        self.assertEqual(audit.request_body['before']['options'], [])
        self.assertEqual(audit.request_body['after']['options'], payload['options'])
        with patch('console.addon_configuration.AdminAuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.put(url, {'version': 2, 'options': []}, format='json')
        self.assertEqual(self.reader.get(url).data['data']['options'], payload['options'])
        self.assertEqual(self.reader.get(url).data['data']['version'], 2)

    def test_price_options_validation_and_rollback(self):
        self.assertEqual(self.client.put(self.url, self.data, format='json').status_code, 200)
        url = self.url + 'price-options/'
        option = {'unit': 'HOUR', 'price': 500, 'enabled': True}
        invalid = ([option, option], [{**option, 'price': 501}], [{**option, 'price': 0}], [{**option, 'price': True}],
                   [{**option, 'unit': 'UNKNOWN'}], [{**option, 'enabled': 1}])
        for options in invalid:
            self.assertEqual(self.client.put(url, {'version': 1, 'options': options}, format='json').status_code, 400)
        with patch('console.addon_configuration.AdminAuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.put(url, {'version': 1, 'options': [option]}, format='json')
        self.assertEqual(self.client.get(url).data['data'], {'version': 1, 'options': [], 'status': 'CONFIGURATION_ONLY'})
        self.addon.is_addon = False
        self.addon.save(update_fields=['is_addon'])
        self.assertEqual(self.client.put(url, {'version': 1, 'options': [option]}, format='json').status_code, 400)

    def test_condition_review_mode_is_explicit_versioned_and_audited(self):
        created = self.client.put(self.url, self.data, format='json')
        self.assertEqual(created.data['data']['configuration']['condition_mode'], 'STAFF_REVIEW')
        rejected = self.client.put(self.url, {**self.data, 'version': 1, 'condition_mode': 'NONE'}, format='json')
        self.assertEqual(rejected.status_code, 400)
        changed = self.client.put(self.url, {**self.data, 'conditions': '', 'version': 1, 'condition_mode': 'NONE'}, format='json')
        self.assertEqual(changed.data['data']['configuration']['condition_mode'], 'NONE')
        self.assertEqual(AdminAuditLog.objects.filter(resource='service-addon-configuration').order_by('pk').last()
                         .request_body['after']['condition_mode'], 'NONE')
        invalid = self.client.put(self.url, {**self.data, 'version': 2, 'condition_mode': 'AUTOMATIC'}, format='json')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(ServiceAddonConfiguration.objects.get().condition_mode, 'NONE')
        with_conditions = self.client.put(self.url, {**self.data, 'version': 2}, format='json')
        self.assertEqual(with_conditions.status_code, 400)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'])
class AddonConfigurationStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_matching_ids_are_independent_under_store_bound_tokens(self):
        stores = (resolve_store(), xinan_store())
        rows = []
        for store in stores:
            with store_context(store):
                game = GameCategory.objects.create(name='本店游戏')
                service = make_service(game_category=game)
                addon = make_service(game_category=game, is_addon=True)
                rows.append((staff_client(['service:view', 'service:edit']), service.pk, addon.pk))
        self.assertEqual(rows[0][1:], rows[1][1:])
        url = f'/api/admin/config/service-addons/{rows[0][1]}/{rows[0][2]}/'
        payload = {'version': 0, 'enabled': True, 'conditions': '声浪配置'}
        result = rows[0][0].put(url, payload, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        other = rows[1][0].get(url)
        self.assertEqual(other.data['data']['items'], [])
        self.assertEqual(rows[1][0].put(url, {**payload, 'conditions': '兴安配置'}, format='json').status_code, 200)
        options_url = url + 'price-options/'
        for index, (client, _, _) in enumerate(rows):
            result = client.put(options_url, {'version': 1, 'options': [
                {'unit': 'HOUR', 'price': 500 + index * 100, 'enabled': True}]}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        for store, value in zip(stores, ('声浪配置', '兴安配置')):
            with store_context(store):
                self.assertEqual(ServiceAddonConfiguration.objects.get().conditions, value)
                self.assertEqual(AdminAuditLog.objects.filter(resource='service-addon-configuration').count(), 1)
        self.assertEqual(rows[0][0].get(options_url).data['data']['options'][0]['price'], 500)
        self.assertEqual(rows[1][0].get(options_url).data['data']['options'][0]['price'], 600)
