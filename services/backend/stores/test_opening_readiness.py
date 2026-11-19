import os
from unittest import skipUnless
from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from orders.models import GameCategory, GameCommissionProfile, OrderTimeoutConfig, ServiceCategory, ServiceItem
from stores.context import store_context
from stores.models import PlatformAccount, PlatformRoleGrant, Store, StoreLicenseCode, StoreLicenseContract
from stores.platform_auth import issue_tokens
from stores.tests_foundation import admin
from wallet.models import SystemConfig


@override_settings(PLATFORM_IDENTITY_ENABLED=True, ALLOWED_HOSTS=['*'],
                   STORE_HOST_BINDINGS={'xa.esports.club': 'xinan'},
                   GAME_COMMISSION_PAYMENT_ENABLED=True)
class OpeningReadinessTests(TransactionTestCase):
    databases = {'default', 'store_a', 'store_b'}

    def setUp(self):
        self.store = Store.objects.update_or_create(slug='xinan', defaults=dict( name='兴安电竞', entity_prefix='XA',
            provider_name_prefix='XA', currency_name='星辉', database_alias='store_b',
            status=Store.Status.CONFIGURING, initialized_at=timezone.now()))[0]
        self.other = Store.objects.update_or_create(slug='shenglang', defaults=dict( name='声浪电竞', entity_prefix='SL',
            provider_name_prefix='SL', currency_name='浪花', database_alias='store_a',
            status=Store.Status.CONFIGURING, initialized_at=timezone.now()))[0]
        Store.objects.update_or_create(slug='local-preview', defaults=dict(name='本地预览',
            database_alias='default', status=Store.Status.ACTIVE))
        self.actor = PlatformAccount.objects.create(username='readiness-owner', phone='13800001111', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=self.actor, role='SUPER_ADMIN', scope_slug='*')
        token, _ = issue_tokens(self.actor, 'SMS', self.actor.phone)
        self.client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.url = f'/api/admin/platform/store-management/{self.store.pk}/setup/readiness'

    def rows(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(response.data['data']['can_open'])
        self.assertFalse(response.data['data']['opening_implemented'])
        return {row['key']: row for row in response.data['data']['checks']}

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_diagnostics_are_read_only_and_cannot_be_used_to_open(self):
        before = self.store.updated_at
        rows = self.rows()
        self.assertEqual(rows['license']['details']['status'], 'UNLICENSED')
        for key in ('license', 'store_admin', 'services', 'order_timeout', 'capacity'):
            self.assertEqual(rows[key]['status'], 'BLOCKED', key)
        self.assertEqual(rows['store_domain']['status'], 'READY')
        with store_context(self.store):
            self.assertFalse(SystemConfig.objects.exists())
            self.assertFalse(OrderTimeoutConfig.objects.exists())
        self.store.refresh_from_db()
        self.assertEqual((self.store.status, self.store.updated_at), (Store.Status.CONFIGURING, before))
        self.assertEqual(self.client.post(self.url, {}, format='json').status_code, 503)

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                'License控制库故障边界/多店隔离/平台财务提现/门店上下文路由属正式契约深水区（18-盘点 License控制库/多店隔离/平台财务待联调）；最终联调启用')
    def test_license_and_service_checks_use_only_target_store(self):
        now = timezone.now()
        contract = StoreLicenseContract.objects.create(store=self.store, amount_due_fen=0,
                                                        created_by=self.actor, updated_by=self.actor)
        license_code = StoreLicenseCode.objects.create(store=self.store, contract=contract, code_digest='b' * 64,
            tier='PRO', kind='INITIAL', starts_at=now-timedelta(days=2), ends_at=now+timedelta(days=1),
            activated_at=now-timedelta(days=1), activated_by_id=1, created_by=self.actor)
        with store_context(self.other):
            ServiceItem.objects.create(name='其他门店服务', price=1000)
        with store_context(self.store):
            staff = admin('readiness-ordinary')
            staff.phone = '13800001234'
            staff.set_password('Synthetic-Store-Password-2026!')
            staff.save(update_fields=['phone', 'password'])
            game = GameCategory.objects.create(name='瓦罗兰特')
            category = ServiceCategory.objects.create(name='陪玩')
            service = ServiceItem.objects.create(name='目标服务', price=1010,
                game_category=game, service_category=category, provider_kind='RANK')
            GameCommissionProfile.objects.create(game_category=game, game='VALORANT',
                                                  rules={'companion': 20, 'grind': 30})
            OrderTimeoutConfig.objects.create(minutes=30, revision=1)
        rows = self.rows()
        self.assertEqual(rows['license']['status'], 'READY')
        self.assertEqual(rows['store_admin']['status'], 'BLOCKED')
        self.assertEqual(rows['order_timeout']['status'], 'READY')
        self.assertEqual(rows['services']['status'], 'READY')
        self.assertEqual([item['name'] for item in rows['service_configuration']['details']], ['目标服务'])
        with store_context(self.store):
            service.price = 1000
            service.save(update_fields=['price'])
            owner = admin('readiness-super', super_admin=True)
            owner.phone = '13800001234'
            owner.set_password('Synthetic-Store-Password-2026!')
            owner.save(update_fields=['phone', 'password'])
        rows = self.rows()
        self.assertEqual(rows['store_admin']['status'], 'READY')
        self.assertEqual(rows['service_configuration']['status'], 'READY')
        self.assertEqual(rows['game_commission_enforcement']['status'], 'READY')
        with override_settings(GAME_COMMISSION_PAYMENT_ENABLED=False):
            self.assertEqual(self.rows()['game_commission_enforcement']['status'], 'BLOCKED')
        license_code.ends_at = now - timedelta(minutes=1)
        license_code.save(update_fields=['ends_at'])
        grace = self.rows()['license']
        self.assertEqual((grace['details']['status'], grace['status']), ('GRACE', 'BLOCKED'))
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, Store.Status.CONFIGURING)

    def test_only_platform_super_admin_and_database_failure_is_not_approval(self):
        finance = PlatformAccount.objects.create(username='readiness-finance', phone='13800001112', is_phone_verified=True)
        PlatformRoleGrant.objects.create(account=finance, role='FINANCE', scope_slug='*')
        token, _ = issue_tokens(finance, 'SMS', finance.phone)
        client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + token)
        self.assertEqual(client.get(self.url).status_code, 403)
        with patch('stores.opening_readiness.status_for_store', side_effect=DatabaseError('offline')):
            self.assertEqual(self.client.get(self.url).status_code, 503)
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, Store.Status.CONFIGURING)
