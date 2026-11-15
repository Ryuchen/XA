"""Staff may record a customer's in-service request without executing a refund."""
from unittest import skipUnless
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.aftersale_scope import applies_to
from orders.models import AfterSale, Order, OrderProvider
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.models import Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class StaffNegotiationCaseTests(APITestCase):
    def account(self, user):
        account = get_or_create_account_for_legacy_user(user)
        link_legacy_relations(user, account)
        if hasattr(user, 'admin_membership'):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        if hasattr(user, 'escort_profile'):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
        return account

    def client_for(self, account):
        return APIClient(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def setUp(self):
        self.boss = make_user(); self.boss_account = self.account(self.boss)
        self.a = make_provider(); self.a_account = self.account(self.a)
        self.b = make_provider(); self.b_account = self.account(self.b)
        self.staff = self.account(make_console_user(['order:view', 'support:edit']))
        self.viewer = self.account(make_console_user(['order:view']))
        self.service = make_service(price=10000)
        self.order = make_order(self.boss, service=self.service, provider=self.a, status='IN_SERVICE',
                                customer_account=self.boss_account, provider_account=self.a_account,
                                escort_mode='DOUBLE', payment_status='PAID')
        for user, account in ((self.a, self.a_account), (self.b, self.b_account)):
            OrderProvider.objects.create(order=self.order, provider=user, provider_account=account,
                                         provider_name_snapshot=user.nickname, settlement_base=5000, provider_income=4000)
        self.client = self.client_for(self.staff)
        self.url = '/api/admin/aftersales/open-negotiation/'
        self.payload = {'order_id':self.order.pk, 'reason':'老板请求协商终止',
                        'affected_provider_account_ids':[self.a_account.pk], 'client_request_id':'case-test-1'}

    def test_staff_records_scoped_case_without_money_movement_and_can_retry(self):
        result = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        case = AfterSale.objects.get(order=self.order)
        self.assertEqual((case.account_id, case.operator_id, case.status), (self.boss_account.pk, self.staff.pk, 'OPEN'))
        self.assertTrue(applies_to(case, self.a_account.pk))
        self.assertFalse(applies_to(case, self.b_account.pk))
        for account, expected in ((self.a_account, 'OPEN'), (self.b_account, '')):
            detail = self.client_for(account).get(f'/api/orders/orders/{self.order.pk}/')
            self.assertEqual(detail.data['code'], 0, detail.data)
            self.assertEqual(detail.data['data']['aftersale_status'], expected)
        self.assertEqual(self.client.post(self.url, self.payload, format='json').data['data']['id'], case.pk)
        conflict = self.client.post(self.url, {**self.payload, 'reason':'不同说明'}, format='json')
        self.assertEqual(conflict.status_code, 409)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('IN_SERVICE', 'PAID'))
        self.assertFalse(Transaction.objects.filter(order=self.order).exists())
        self.assertFalse(self.order.refund_records.exists())
        from orders.refunds import RefundError, build_refund_plan
        with self.assertRaises(RefundError) as error:
            build_refund_plan(self.order)
        self.assertEqual(error.exception.code, 'REFUND_SCOPED_ALLOCATION_REQUIRED')

    def test_authority_order_state_and_scope_are_enforced(self):
        for account in (self.viewer, self.boss_account, self.a_account):
            self.assertEqual(self.client_for(account).post(self.url, self.payload, format='json').status_code, 403)
        for scope in ([], [self.staff.pk], [self.a_account.pk, self.a_account.pk]):
            self.assertEqual(self.client.post(self.url, {**self.payload, 'affected_provider_account_ids':scope}, format='json').status_code, 400)
        self.order.status = 'COMPLETED'; self.order.save(update_fields=['status'])
        self.assertEqual(self.client.post(self.url, self.payload, format='json').status_code, 400)
        self.assertFalse(AfterSale.objects.exists())

    def test_existing_open_case_is_not_overwritten(self):
        self.client.post(self.url, self.payload, format='json')
        result = self.client.post(self.url, {**self.payload, 'client_request_id':'different'}, format='json')
        self.assertEqual((result.status_code, result.data['errorCode']), (409, 'AFTERSALE_ALREADY_OPEN'))
        self.assertEqual(AfterSale.objects.count(), 1)

    def test_inconsistent_provider_mapping_cannot_create_case(self):
        self.a.escort_profile.account = None
        self.a.escort_profile.save(update_fields=['account'])
        result = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(result.status_code, 400)
        self.assertIn('归属待核对', str(result.data))
        self.assertFalse(AfterSale.objects.exists())

    def test_legacy_single_assignment_shows_affected_name(self):
        self.order.providers.all().delete()
        self.order.provider_name_snapshot = '历史陪玩名称'
        self.order.save(update_fields=['provider_name_snapshot'])
        result = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        self.assertEqual(result.data['data']['affected_provider_names'], ['历史陪玩名称'])

    def test_released_assignments_do_not_fall_back_to_old_primary_provider(self):
        from django.utils import timezone
        self.order.providers.update(released_at=timezone.now())
        result = self.client.post(self.url, self.payload, format='json')
        self.assertEqual(result.status_code, 400, result.data)
        self.assertFalse(AfterSale.objects.exists())

    def test_retry_does_not_reopen_a_closed_or_replaced_case(self):
        self.client.post(self.url, self.payload, format='json')
        case = AfterSale.objects.get(order=self.order)
        case.status = 'RESOLVED'; case.save(update_fields=['status'])
        self.assertEqual(self.client.post(self.url, self.payload, format='json').data['data']['status'], 'RESOLVED')
        renewed = {**self.payload, 'client_request_id':'case-test-2'}
        self.assertEqual(self.client.post(self.url, renewed, format='json').data['data']['status'], 'OPEN')
        replay = self.client.post(self.url, self.payload, format='json')
        self.assertEqual((replay.status_code, replay.data['errorCode']), (409, 'AFTERSALE_REQUEST_PREVIOUS'))
        case.refresh_from_db()
        self.assertEqual((case.status, case.client_request_id), ('OPEN', 'case-test-2'))


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
@skipUnless('store_b' in settings.DATABASES, 'Requires two isolated store databases')
class StaffNegotiationCaseStoreTests(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_order_id_remains_in_authenticated_store(self):
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        clients, ids = [], []
        for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
            with store_context(store):
                boss, player = make_user(), make_provider()
                boss_account = get_or_create_account_for_legacy_user(boss)
                player_account = get_or_create_account_for_legacy_user(player)
                link_legacy_relations(boss, boss_account); link_legacy_relations(player, player_account)
                player.escort_profile.account = player_account; player.escort_profile.save(update_fields=['account'])
                staff = make_console_user(['support:edit', 'order:view'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                order = make_order(boss, service=make_service(price=1000), provider=player, status='IN_SERVICE',
                                   customer_account=boss_account, provider_account=player_account, payment_status='PAID')
                clients.append(APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0]))
                ids.append((store, order.pk, player_account.pk))
        self.assertEqual(ids[0][1], ids[1][1])
        with store_context(ids[0][0]):
            result = clients[0].post('/api/admin/aftersales/open-negotiation/', {'order_id':ids[0][1],
                'affected_provider_account_ids':[ids[0][2]], 'reason':'本店客户协商', 'client_request_id':'local-case'}, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            self.assertEqual(AfterSale.objects.count(), 1)
        with store_context(ids[1][0]):
            self.assertEqual(AfterSale.objects.count(), 0)


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class StaffNegotiationActualOrderTests(APITransactionTestCase):
    from orders.tests.test_provider_aftersale_status import ProviderAfterSaleStatusTest as flow
    setUp = flow.setUp
    login = flow.login
    ok = flow.ok

    def test_case_follows_real_order_payment_and_service_start(self):
        from orders.tests.test_provider_aftersale_status import image
        from wallet.models import Wallet
        self.login(self.boss)
        order_id = self.ok(self.client.post('/api/orders/create/', {'service_id':self.service.pk,
            'provider_id':self.accounts[self.provider.pk].pk, 'client_request_id':'negotiation-real-order'}, format='json'))['id']
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image':image()}, format='multipart'))
        balance = Wallet.objects.get(user=self.boss).balance
        role = self.staff.admin_membership.roles.first()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        self.login(self.staff)
        result = self.ok(self.client.post('/api/admin/aftersales/open-negotiation/', {'order_id':order_id,
            'affected_provider_account_ids':[self.accounts[self.provider.pk].pk], 'reason':'老板联系后申请协商',
            'client_request_id':'negotiation-real-case'}, format='json'))
        self.assertEqual(result['status'], 'OPEN')
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, balance)
        self.assertFalse(Order.objects.get(pk=order_id).refund_records.exists())
        for user in (self.boss, self.provider):
            self.login(user)
            self.assertEqual(self.ok(self.client.get(f'/api/orders/orders/{order_id}/'))['aftersale_status'], 'OPEN')
