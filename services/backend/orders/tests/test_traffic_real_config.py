"""隔离数据库中的限流配置真实鉴权与版本冲突验收。"""
from copy import deepcopy
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user
from orders.traffic_models import OrderTrafficConfig


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class TrafficRealConfigTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.editor = self.make_account(['order:view', 'order:traffic'])
        self.viewer = self.make_account(['order:view'])
        self.login(self.editor)
        response = self.client.get('/api/admin/order-traffic/')
        self.assertEqual(response.data['code'], 0, response.data)
        self.payload = {key: deepcopy(response.data['data'][key]) for key in (
            'revision', 'enabled', 'self_order_enabled', 'peak_start', 'peak_end', 'valley_start', 'valley_end', 'limits')}

    def make_account(self, permissions):
        user = make_console_user(permissions)
        account = get_or_create_account_for_legacy_user(user)
        user.admin_membership.account = account
        user.admin_membership.save(update_fields=['account'])
        return account

    def login(self, account):
        token, _ = issue_account_tokens(account)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def test_change_and_stale_revision_do_not_overwrite(self):
        changed = deepcopy(self.payload)
        changed['limits']['normal']['grab']['account_per_minute'] += 1
        changed['self_order_enabled'] = False
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.put('/api/admin/order-traffic/', changed, format='json')
        self.assertEqual(first.data['code'], 0, first.data)
        self.assertEqual(first.data['data']['revision'], self.payload['revision'] + 1)
        stale = self.client.put('/api/admin/order-traffic/', self.payload, format='json')
        self.assertEqual(stale.status_code, 409)
        actual = self.client.get('/api/admin/order-traffic/').data['data']
        self.assertFalse(actual['self_order_enabled'])
        self.assertEqual(actual['limits']['normal']['grab']['account_per_minute'], changed['limits']['normal']['grab']['account_per_minute'])
        self.assertEqual(OrderTrafficConfig.objects.get(pk=1).revision, first.data['data']['revision'])

    def test_overlap_is_rejected_without_a_write(self):
        invalid = deepcopy(self.payload)
        invalid.update(peak_start=18, peak_end=24, valley_start=20, valley_end=23)
        result = self.client.put('/api/admin/order-traffic/', invalid, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.get('/api/admin/order-traffic/').data['data']['revision'], self.payload['revision'])

    def test_view_only_cannot_change_settings(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get('/api/admin/order-traffic/').data['code'], 0)
        self.assertEqual(self.client.put('/api/admin/order-traffic/', self.payload, format='json').status_code, 403)

    @override_settings(ORDER_TRAFFIC_ENABLED=False)
    def test_environment_disablement_is_visible_to_operator(self):
        response = self.client.get('/api/admin/order-traffic/')
        self.assertEqual(response.data['code'], 0, response.data)
        self.assertFalse(response.data['data']['runtime_enabled'])
