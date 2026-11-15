from unittest import skipUnless
from unittest.mock import patch
from django.conf import settings
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from console.models import AdminAuditLog
from orders.models import GameCategory, Order, Reservation
from orders.tests.factories import make_console_user
from orders.tests.test_reservation_api import LIST_URL, ReservationApiTestBase


@override_settings(KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountFieldsWorkflowTest(ReservationApiTestBase):
    def setUp(self):
        super().setUp()
        editor = make_console_user(['dictionary:view', 'dictionary:edit', 'order:dispatch', 'order:view'])
        self.editor_account = get_or_create_account_for_legacy_user(editor)
        link_legacy_relations(editor, self.editor_account)
        editor.admin_membership.account = self.editor_account
        editor.admin_membership.save(update_fields=['account'])
        self.admin = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.editor_account)[0])
        self.buyer = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.boss_account)[0])

    def save(self, scope, pk, fields, version=0):
        result = self.admin.put(f'/api/admin/config/order-account-fields/{scope}/{pk}/',
                                {'fields': fields, 'version': version}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data['data']

    def test_game_rules_service_override_and_restore_inheritance(self):
        game = GameCategory.objects.create(name='隔离测试游戏')
        self.service.game_category = game; self.service.save(update_fields=['game_category'])
        self.save('game', game.pk, ['game_region'])
        detail = self.buyer.get(f'/api/orders/services/{self.service.pk}/').data['data']
        self.assertEqual(detail['account_fields'], {'fields': ['game_region'], 'source': 'game'})
        self.save('service', self.service.pk, ['game_uid'])
        detail = self.buyer.get(f'/api/orders/services/{self.service.pk}/').data['data']
        self.assertEqual(detail['account_fields'], {'fields': ['game_uid'], 'source': 'service'})
        self.save('service', self.service.pk, None, 1)
        self.assertEqual(self.buyer.get(f'/api/orders/services/{self.service.pk}/').data['data']['account_fields'],
                         {'fields': ['game_region'], 'source': 'game'})
        self.assertEqual(AdminAuditLog.objects.filter(resource='order-account-fields').count(), 3)
        stale = self.admin.put(f'/api/admin/config/order-account-fields/service/{self.service.pk}/',
                               {'fields': [], 'version': 1}, format='json')
        self.assertEqual((stale.status_code, stale.data['errorCode']), (409, 'CONFIG_CHANGED'))

    def test_boss_and_staff_orders_enforce_required_fields_before_debit(self):
        self.save('service', self.service.pk, ['game_uid'])
        before = self.boss_account.wallet.balance
        payload = {'service_id': self.service.pk, 'client_request_id': 'fields-boss-001'}
        denied = self.buyer.post('/api/orders/create/', payload, format='json')
        self.assertEqual(denied.data['code'], 400, denied.data)
        self.assertIn('游戏 UID', denied.data['msg'])
        self.assertFalse(Order.objects.exists())
        self.boss_account.wallet.refresh_from_db(); self.assertEqual(self.boss_account.wallet.balance, before)
        accepted = self.buyer.post('/api/orders/create/', {**payload, 'game_uid': ' player-1 '}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(Order.objects.get(pk=accepted.data['data']['id']).game_uid, 'player-1')
        staff_payload = {'customer_id': self.boss_account.pk, 'service_id': self.service.pk,
                         'client_request_id': 'fields-staff-001'}
        denied = self.admin.post('/api/admin/orders/dispatch/', staff_payload, format='json')
        self.assertEqual(denied.data['code'], 400, denied.data)
        accepted = self.admin.post('/api/admin/orders/dispatch/', {**staff_payload, 'game_uid': 'staff-uid'}, format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(Order.objects.get(pk=accepted.data['data']['id']).game_uid, 'staff-uid')

    def test_reservation_enforces_service_rule_before_creating(self):
        self.save('service', self.service.pk, ['game_nickname'])
        denied = self.buyer.post(LIST_URL, self.create_payload(), format='json')
        self.assertEqual(denied.data['code'], 400, denied.data)
        self.assertIn('游戏昵称', denied.data['msg'])
        self.assertFalse(Reservation.objects.exists())
        accepted = self.buyer.post(LIST_URL, self.create_payload(game_nickname='玩家昵称'), format='json')
        self.assertEqual(accepted.data['code'], 0, accepted.data)
        self.assertEqual(Reservation.objects.get(pk=accepted.data['data']['id']).game_nickname, '玩家昵称')

    def test_staff_preview_and_creation_follow_current_rules(self):
        self.save('service', self.service.pk, ['game_uid'])
        url = f'{LIST_URL}account-fields/{self.service.pk}/'
        staff = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff_account)[0])
        preview = staff.get(url)
        self.assertEqual(preview.data['data'], {'service_id': self.service.pk,
                         'account_fields': {'fields': ['game_uid'], 'source': 'service'}})
        self.assertEqual(self.buyer.get(url).status_code, 403)
        viewer = make_console_user(['order:view'])
        viewer_account = get_or_create_account_for_legacy_user(viewer)
        link_legacy_relations(viewer, viewer_account)
        viewer.admin_membership.account = viewer_account
        viewer.admin_membership.save(update_fields=['account'])
        viewer_client = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(viewer_account)[0])
        self.assertEqual(viewer_client.get(url).status_code, 403)
        self.assertEqual(staff.get(f'{LIST_URL}account-fields/999999/').status_code, 404)
        # A changed rule is checked again by the creation transaction.
        self.save('service', self.service.pk, ['game_nickname'], 1)
        payload = self.create_payload(customer_account_id=self.boss_account.pk, game_uid='uid')
        result = staff.post(LIST_URL, payload, format='json')
        self.assertEqual(result.data['code'], 400, result.data)
        self.assertIn('游戏昵称', result.data['msg'])
        self.assertFalse(Reservation.objects.exists())
        result = staff.post(LIST_URL, {**payload, 'game_nickname': '代约玩家'}, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(Reservation.objects.get(pk=result.data['data']['id']).game_nickname, '代约玩家')

    def test_staff_preview_rejects_corrupt_rules(self):
        from wallet.models import SystemConfig
        SystemConfig.objects.create(key=f'order_account_fields_service_{self.service.pk}', value='not-json')
        response = self.admin.get(f'{LIST_URL}account-fields/{self.service.pk}/')
        self.assertEqual(response.status_code, 409)

    def test_dictionary_permission_and_invalid_values_are_enforced(self):
        url = f'/api/admin/config/order-account-fields/service/{self.service.pk}/'
        no_permission = APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.staff_account)[0])
        self.assertEqual(no_permission.put(url, {'version': 0, 'fields': []}, format='json').status_code, 403)
        for fields in (['not_a_field'], ['game_uid', 'game_uid'], 'game_uid'):
            with self.subTest(fields=fields):
                self.assertEqual(self.admin.put(url, {'version': 0, 'fields': fields}, format='json').status_code, 400)

    def test_configuration_target_list_is_minimal_and_available_during_setup(self):
        from stores.setup_access import setup_request_allowed
        response = self.admin.get('/api/admin/config/order-account-fields/targets/', {'scope': 'service'})
        self.assertEqual(response.data['data']['list'], [{'id': self.service.pk, 'name': self.service.name}])
        self.assertTrue(setup_request_allowed('/api/admin/config/order-account-fields/targets/', 'GET'))
        self.assertTrue(setup_request_allowed(f'/api/admin/config/order-account-fields/service/{self.service.pk}/', 'PUT'))
        self.assertFalse(setup_request_allowed('/api/admin/config/order-account-fields/service/0/', 'PUT'))
        self.assertFalse(setup_request_allowed('/api/admin/config/order-account-fields/targets/', 'POST'))

    def test_audit_failure_rolls_back_rule(self):
        from orders.account_fields import current
        with patch('console.account_fields.AdminAuditLog.objects.create', side_effect=DatabaseError('test')):
            with self.assertRaises(DatabaseError):
                self.admin.put(f'/api/admin/config/order-account-fields/service/{self.service.pk}/',
                               {'fields': ['game_uid'], 'version': 0}, format='json')
        self.assertEqual(current('service', self.service.pk), {'fields': None, 'version': 0})


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class AccountFieldsStoreIsolationTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_service_id_has_independent_store_rules(self):
        from orders.tests.factories import make_service
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        stores = (resolve_store(), xinan_store())
        results = []
        for store, host, field in zip(stores, ('sl.esports.club', 'xa.esports.club'), ('game_region', 'game_uid')):
            with store_context(store):
                service = make_service(price=1000)
                editor = make_console_user(['dictionary:view', 'dictionary:edit', 'order:dispatch'])
                account = get_or_create_account_for_legacy_user(editor)
                editor.admin_membership.account = account; editor.admin_membership.save(update_fields=['account'])
                admin = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                saved = admin.put(f'/api/admin/config/order-account-fields/service/{service.pk}/',
                                  {'version': 0, 'fields': [field]}, format='json')
                self.assertEqual(saved.status_code, 200, saved.data)
                detail = APIClient(HTTP_HOST=host).get(f'/api/orders/services/{service.pk}/')
                self.assertEqual(detail.data['code'], 0, detail.data)
                preview = admin.get(f'{LIST_URL}account-fields/{service.pk}/')
                self.assertEqual(preview.data['data']['account_fields']['fields'], [field])
                results.append((service.pk, detail.data['data']['account_fields']['fields']))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual([row[1] for row in results], [['game_region'], ['game_uid']])

    def test_configuration_evidence_remains_store_local(self):
        from console.config_readiness import _account_fields_evidence
        from orders.tests.factories import make_service
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        original, branch = resolve_store(), xinan_store()
        for store in (original, branch):
            with store_context(store):
                service = make_service(name='本店资料核对服务', price=1000)
                if store == original:
                    service_id = service.pk
                    editor = make_console_user(['dictionary:view', 'dictionary:edit'])
                    account = get_or_create_account_for_legacy_user(editor)
                    editor.admin_membership.account = account; editor.admin_membership.save(update_fields=['account'])
                    client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
                else:
                    self.assertEqual(service.pk, service_id)
        with store_context(original):
            before_original = _account_fields_evidence()
        with store_context(branch):
            before_branch = _account_fields_evidence()
        with store_context(original):
            result = client.put(f'/api/admin/config/order-account-fields/service/{service_id}/',
                                {'version': 0, 'fields': []}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
            self.assertNotEqual(_account_fields_evidence(), before_original)
        with store_context(branch):
            self.assertEqual(_account_fields_evidence(), before_branch)
