from unittest import skipUnless

from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_completed_order, make_console_user, make_provider, make_service, make_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RefundDepositRecoveryPermit, get_platform_wallet
from wallet.services import get_wallet


@skipUnless('store_b' in settings.DATABASES, '需要两套隔离测试数据库')
@override_settings(REFUND_DEPOSIT_DEFICIT_ENABLED=True, REFUND_DEPOSIT_RECOVERY_GRAB_ENABLED=True,
                   PRESTART_HANDOFF_ENABLED=True, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class RefundRecoveryHandoffMultiStoreTest(TestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_identical_ids_and_cross_store_token_do_not_move_other_store_permits(self):
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        stores = ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club'))
        facts = []
        for store, host in stores:
            with store_context(store):
                self.boss = make_user()
                boss_account = get_or_create_account_for_legacy_user(self.boss)
                staff = make_console_user(['order:view', 'order:dispatch'])
                staff_account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = staff_account
                staff.admin_membership.save(update_fields=['account'])
                self.platform = get_platform_wallet()
                self.platform.balance = 20000; self.platform.save(update_fields=['balance'])
                accounts = []
                for _ in range(2):
                    self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
                    self.account = get_or_create_account_for_legacy_user(self.provider)
                    self.profile = self.provider.escort_profile
                    self.profile.account = self.account; self.profile.save(update_fields=['account'])
                    self.wallet = get_wallet(user=self.provider, account=self.account)
                    self.order = make_completed_order(self.boss, self.provider, amount=10000,
                        provider_account=self.account, payment_status='PAID')
                    RefundDepositDeficitTest.deficit(self, 200)
                    accounts.append(self.account)
                service = make_service(price=10000, commission_rate=20)
                wallet = get_wallet(user=self.boss, account=boss_account)
                wallet.balance = 20000; wallet.save(update_fields=['balance'])
                from engagement.models import ClubOperationConfig, PointFundingLot
                ClubOperationConfig.objects.get_or_create(pk=1)
                PointFundingLot.objects.create(account=boss_account, source_key='recovery-handoff-prepaid',
                    kind=PointFundingLot.Kind.PREPAID, remaining=20000)
                boss_token = issue_account_tokens(boss_account)[0]
                staff_token = issue_account_tokens(staff_account)[0]
                boss = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer ' + boss_token)
                response = boss.post('/api/orders/create/', {'service_id':service.pk,
                    'provider_id':accounts[0].pk, 'client_request_id':'recovery-store-order'}, format='json')
                self.assertEqual(response.data['code'], 0, response.data)
                order_id = response.data['data']['id']
                facts.append((order_id, accounts[0].pk, accounts[1].pk, staff_token))
        self.assertEqual(facts[0][:3], facts[1][:3])
        order_id, old_id, new_id, staff_token = facts[0]
        payload = {'reason':'隔离测试门店改派', 'transfers':[{'old_provider_id':old_id,
            'new_provider_id':new_id, 'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0}]}
        foreign = APIClient(HTTP_HOST=stores[1][1], HTTP_AUTHORIZATION='Bearer '+staff_token)
        self.assertNotEqual(foreign.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json').data.get('code'), 0)
        own = APIClient(HTTP_HOST=stores[0][1], HTTP_AUTHORIZATION='Bearer '+staff_token)
        result = own.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json')
        self.assertEqual(result.data['code'], 0, result.data)
        with store_context(stores[0][0]):
            self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, new_id)
            self.assertEqual(RefundDepositRecoveryPermit.objects.get(order_id=order_id, account_id=old_id).close_reason,
                             'TRANSFERRED_UNSTARTED')
            self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order_id=order_id, account_id=new_id,
                                                                        closed_at__isnull=True).exists())
        with store_context(stores[1][0]):
            self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, old_id)
            self.assertTrue(RefundDepositRecoveryPermit.objects.filter(order_id=order_id, account_id=old_id,
                                                                        closed_at__isnull=True).exists())
            self.assertFalse(RefundDepositRecoveryPermit.objects.filter(order_id=order_id, account_id=new_id).exists())

    @override_settings(ORIGINAL_ATTRIBUTION_ENABLED=True, SEGMENT_ECONOMICS_CAPTURE_ENABLED=True,
                       SEGMENT_REPORT_ENABLED=True, SEGMENT_SETTLEMENT_ENABLED=True,
                       SEGMENT_STANDARD_TRANSFER_ENABLED=True, PRESTART_SCOPE_SNAPSHOT_ENABLED=True,
                       REQUIRE_ORDER_EVIDENCE_IMAGES=True)
    def test_service_handoff_with_equal_ids_is_confined_to_own_store(self):
        from tempfile import TemporaryDirectory
        from orders.tests.test_release_flow import screenshot
        from orders.models import OrderOriginalShareTransfer
        from wallet.test_refund_deposit_deficit import RefundDepositDeficitTest
        stores = ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club'))
        facts = []
        with TemporaryDirectory(prefix='xa-recovery-service-') as media, override_settings(MEDIA_ROOT=media):
            for store, host in stores:
                with store_context(store):
                    self.boss = make_user()
                    boss_account = get_or_create_account_for_legacy_user(self.boss)
                    staff = make_console_user(['order:view', 'order:dispatch'])
                    staff_account = get_or_create_account_for_legacy_user(staff)
                    staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
                    self.platform = get_platform_wallet()
                    self.platform.balance = 20000; self.platform.save(update_fields=['balance'])
                    accounts = []
                    for _ in range(2):
                        self.provider = make_provider(deposit_mode='EARNINGS', deposit_paid=10000)
                        self.account = get_or_create_account_for_legacy_user(self.provider)
                        self.profile = self.provider.escort_profile
                        self.profile.account = self.account; self.profile.save(update_fields=['account'])
                        self.wallet = get_wallet(user=self.provider, account=self.account)
                        self.order = make_completed_order(self.boss, self.provider, amount=10000,
                            provider_account=self.account, payment_status='PAID')
                        RefundDepositDeficitTest.deficit(self, 200)
                        accounts.append(self.account)
                    service = make_service(price=10000, commission_rate=20)
                    wallet = get_wallet(user=self.boss, account=boss_account)
                    wallet.balance = 20000; wallet.save(update_fields=['balance'])
                    from engagement.models import ClubOperationConfig, PointFundingLot
                    ClubOperationConfig.objects.get_or_create(pk=1)
                    PointFundingLot.objects.create(account=boss_account, source_key='recovery-service-prepaid',
                        kind=PointFundingLot.Kind.PREPAID, remaining=20000)
                    def client(account):
                        return APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
                    result = client(boss_account).post('/api/orders/create/', {'service_id':service.pk,
                        'provider_id':accounts[0].pk, 'client_request_id':'recovery-service-store'}, format='json')
                    self.assertEqual(result.data['code'], 0, result.data)
                    order_id = result.data['data']['id']
                    result = client(accounts[0]).post(f'/api/orders/orders/{order_id}/start/',
                        {'entry_image':screenshot()}, format='multipart')
                    self.assertEqual(result.data['code'], 0, result.data)
                    facts.append((order_id, accounts[0].pk, accounts[1].pk, issue_account_tokens(staff_account)[0]))
            self.assertEqual(facts[0][:3], facts[1][:3])
            order_id, old_id, new_id, token = facts[0]
            payload = {'reason':'隔离测试服务中转单', 'transfers':[{'old_provider_id':old_id,
                'new_provider_id':new_id, 'split_type':'PERCENT', 'split_value':0, 'penalty_amount':0,
                'original_share':{'retained':4000, 'transferred':6000, 'agreement':'双方确认服务份额'}}]}
            foreign = APIClient(HTTP_HOST=stores[1][1], HTTP_AUTHORIZATION='Bearer '+token)
            self.assertNotEqual(foreign.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json').data.get('code'), 0)
            own = APIClient(HTTP_HOST=stores[0][1], HTTP_AUTHORIZATION='Bearer '+token)
            result = own.post(f'/api/admin/orders/{order_id}/transfer/', payload, format='json')
            self.assertEqual(result.data['code'], 0, result.data)
            with store_context(stores[0][0]):
                self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, new_id)
                self.assertEqual(RefundDepositRecoveryPermit.objects.filter(order_id=order_id, closed_at__isnull=True).count(), 2)
                self.assertEqual(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=order_id).count(), 1)
            with store_context(stores[1][0]):
                self.assertEqual(Order.objects.get(pk=order_id).provider_account_id, old_id)
                self.assertEqual(RefundDepositRecoveryPermit.objects.filter(order_id=order_id, closed_at__isnull=True).count(), 1)
                self.assertFalse(OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=order_id).exists())
