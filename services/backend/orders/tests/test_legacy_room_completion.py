"""Legacy playground completion is an attested order fact, not a room link."""
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from orders.models import LegacyRoomCompletion, Order, OrderRoom
from orders.tests import test_negotiation_termination as fixture
from wallet.models import ProviderReport, Transaction, Wallet


@override_settings(NEGOTIATION_PROPOSAL_ENABLED=True, REFUND_DEPOSIT_DEFICIT_ENABLED=True,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False)
class LegacyRoomCompletionTests(APITestCase):
    account = fixture.NegotiationTerminationTests.account
    client_for = fixture.NegotiationTerminationTests.client_for

    def setUp(self):
        fixture.NegotiationTerminationTests.setUp(self)
        Order.objects.filter(pk=self.order.pk).update(room_required_snapshot=None)
        self.order.refresh_from_db()
        self.url = f'/api/admin/orders/{self.order.pk}/legacy-room-completion/'
        self.payload = {'customer_account_id': self.boss_account.pk,
            'provider_account_ids': [self.a_account.pk, self.b_account.pk],
            'confirmed_at': timezone.now().isoformat(), 'evidence': '客服核实旧站老板本人已确认结单',
            'client_request_id': 'legacy-room-test'}
        self.staff_client = self.client_for(self.staff)

    def test_staff_attests_once_without_creating_room_or_paying_early(self):
        before = list(Wallet.objects.order_by('pk').values_list('pk', 'balance'))
        response = self.staff_client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record = LegacyRoomCompletion.objects.get(order=self.order)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.completed_at, record.operator_id),
                         ('COMPLETED', record.confirmed_at, self.staff.pk))
        from engagement.confirmation import aftersale_deadline
        self.assertEqual(aftersale_deadline(self.order), record.confirmed_at + timedelta(hours=self.order.service_aftersale_hours))
        self.assertEqual(record.provider_accounts, sorted([self.a_account.pk, self.b_account.pk]))
        self.assertFalse(OrderRoom.objects.filter(order=self.order).exists())
        self.assertEqual(list(Wallet.objects.order_by('pk').values_list('pk', 'balance')), before)
        self.assertFalse(Transaction.objects.filter(order=self.order, tx_type='INCOME').exists())
        self.assertEqual(ProviderReport.objects.filter(order=self.order, status='DRAFT').count(), 2)
        replay = self.staff_client.post(self.url, self.payload, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        lookup = self.staff_client.get(self.url)
        self.assertEqual(lookup.data['data']['client_request_id'], self.payload['client_request_id'])
        self.assertEqual(self.client_for(self.boss_account).get(self.url).status_code, 403)
        self.assertEqual(LegacyRoomCompletion.objects.filter(order=self.order).count(), 1)
        conflict = self.staff_client.post(self.url, {**self.payload, 'evidence': '不同的核实依据'}, format='json')
        self.assertEqual(conflict.status_code, 409, conflict.data)
        from console.models import AdminAuditLog
        audit = AdminAuditLog.objects.get(resource='legacy-room-completion', object_id=str(self.order.pk))
        self.assertEqual(audit.request_body['customer_account_id'], self.boss_account.pk)

    def test_identity_time_permissions_and_existing_room_are_checked(self):
        unauthorized = self.client_for(self.boss_account).post(self.url, self.payload, format='json')
        self.assertEqual(unauthorized.status_code, 403)
        for changed in ({'customer_account_id': self.a_account.pk},
                        {'provider_account_ids': [self.a_account.pk]},
                        {'provider_account_ids': [self.a_account.pk, self.a_account.pk]},
                        {'confirmed_at': (timezone.now() + timedelta(days=1)).isoformat()},
                        {'confirmed_at': (self.order.created_at - timedelta(seconds=1)).isoformat()},
                        {'evidence': '无'}):
            with self.subTest(changed=changed):
                response = self.staff_client.post(self.url, {**self.payload, **changed}, format='json')
                self.assertGreaterEqual(response.status_code, 400, response.data)
        OrderRoom.objects.create(order=self.order)
        response = self.staff_client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertFalse(LegacyRoomCompletion.objects.exists())

    def test_unverified_clock_open_aftersale_and_settled_income_block_completion(self):
        for change in ({'service_clock_version': None}, {'service_freeze_hours': None},
                       {'settlement_mode': 'COMPLETION'}, {'payment_status': 'PARTIALLY_REFUNDED'}):
            with self.subTest(change=change):
                original = {name: getattr(self.order, name) for name in change}
                Order.objects.filter(pk=self.order.pk).update(**change)
                response = self.staff_client.post(self.url, self.payload, format='json')
                self.assertEqual(response.status_code, 409, response.data)
                Order.objects.filter(pk=self.order.pk).update(**original)
        from orders.models import OrderProvider
        assignment = OrderProvider.objects.filter(order=self.order).first()
        OrderProvider.objects.filter(pk=assignment.pk).update(settled_at=timezone.now())
        response = self.staff_client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        OrderProvider.objects.filter(pk=assignment.pk).update(settled_at=None)
        from stores.db import transaction
        with transaction.atomic():
            Wallet.objects.filter(account=self.boss_account).update(account=None)
            before = Wallet.objects.count()
            response = self.staff_client.post(self.url, self.payload, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(Wallet.objects.count(), before)
            transaction.set_rollback(True)
        from orders.models import AfterSale
        AfterSale.objects.create(order=self.order, account=self.boss_account, reason='售后处理中', status='OPEN')
        response = self.staff_client.post(self.url, self.payload, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(LegacyRoomCompletion.objects.exists())

    def test_failed_audit_rolls_back_order_reports_and_record(self):
        with patch('console.models.AdminAuditLog.objects.create', side_effect=RuntimeError('核实审计失败')):
            with self.assertRaisesMessage(RuntimeError, '核实审计失败'):
                self.staff_client.post(self.url, self.payload, format='json')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'IN_SERVICE')
        self.assertIsNone(self.order.completed_at)
        self.assertFalse(LegacyRoomCompletion.objects.exists())
        self.assertFalse(ProviderReport.objects.filter(order=self.order).exists())


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   ORIGINAL_ATTRIBUTION_ENABLED=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class LegacyRoomCompletionMoneyFlowTest(APITestCase):
    from orders.tests.test_release_flow import ReleaseFlowTest as _flow
    setUp = _flow.setUp
    login = _flow.login
    ok = _flow.ok
    recharge = _flow.recharge
    new_order = _flow.new_order
    settle_after_window = _flow.settle_after_window

    def test_verified_old_completion_waits_for_report_and_money_release(self):
        from orders.tests.test_release_flow import screenshot
        role = self.admin.admin_membership.roles.get()
        role.permissions = [*role.permissions, 'support:edit']
        role.save(update_fields=['permissions'])
        order = self.new_order()
        Order.objects.filter(pk=order.pk).update(room_required_snapshot=None)
        self.login(self.provider)
        self.ok(self.client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart'))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/orders/{order.pk}/legacy-room-completion/', {
            'customer_account_id': self.accounts[self.boss.pk].pk,
            'provider_account_ids': [self.accounts[self.provider.pk].pk],
            'confirmed_at': timezone.now().isoformat(), 'evidence': '已核对老板本人旧站确认事实',
            'client_request_id': 'legacy-money-flow'}, format='json'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        report = ProviderReport.objects.get(order=order, provider=self.provider)
        self.login(self.provider)
        for kind in ('COMPLETION', 'RESULT'):
            self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/images/',
                                     {'kind': kind, 'image': screenshot()}, format='multipart'))
        self.ok(self.client.post(f'/api/wallet/reports/{report.pk}/submit/', {}))
        self.login(self.admin)
        self.ok(self.client.post(f'/api/admin/reports/{report.pk}/approve/'))
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 0)
        self.settle_after_window()
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertEqual(Wallet.objects.get(user=self.provider).balance, 8000)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class LegacyRoomCompletionStoreTests(APITestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_staff_token_is_confined_to_its_store_with_matching_order_ids(self):
        from club_accounts.services import get_or_create_account_for_legacy_user
        from club_accounts.tokens import issue_account_tokens
        from orders.models import OrderProvider
        from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
        from stores.context import resolve_store, store_context
        from stores.tests_hosts import xinan_store
        from wallet.services import get_wallet
        rows = []
        for store in (resolve_store(), xinan_store()):
            with store_context(store):
                boss, player = make_user(), make_provider(deposit_mode='EARNINGS')
                boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
                get_wallet(user=boss, account=boss_account)
                player.escort_profile.account = player_account; player.escort_profile.save(update_fields=['account'])
                order = make_order(boss, service=make_service(), provider=player, status='IN_SERVICE',
                    customer_account=boss_account, provider_account=player_account, payment_status='PAID',
                    settlement_mode='REPORT_APPROVAL', in_service_at=timezone.now()-timedelta(hours=1))
                Order.objects.filter(pk=order.pk).update(room_required_snapshot=None)
                OrderProvider.objects.create(order=order, provider=player, provider_account=player_account,
                                             settlement_base=order.amount, provider_income=order.amount)
                user = make_console_user(['order:view', 'support:edit'])
                staff = get_or_create_account_for_legacy_user(user)
                user.admin_membership.account = staff; user.admin_membership.save(update_fields=['account'])
                client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff)[0])
                payload = {'customer_account_id': boss_account.pk, 'provider_account_ids': [player_account.pk],
                    'confirmed_at': timezone.now().isoformat(), 'evidence': '客服核实老板本人已确认旧站结单',
                    'client_request_id': 'same-store-key'}
                rows.append((store, order.pk, client, payload))
        self.assertEqual(rows[0][1], rows[1][1])
        response = rows[1][2].post(f'/api/admin/orders/{rows[1][1]}/legacy-room-completion/',
                                   rows[1][3], format='json', HTTP_X_STORE_SLUG='shenglang')
        if response.status_code != 200:
            response = rows[1][2].post(f'/api/admin/orders/{rows[1][1]}/legacy-room-completion/',
                                       rows[1][3], format='json')
        self.assertEqual(response.status_code, 200, response.data)
        for index, (store, order_id, _, _) in enumerate(rows):
            with store_context(store):
                self.assertEqual(Order.objects.get(pk=order_id).status, 'COMPLETED' if index else 'IN_SERVICE')
                self.assertEqual(LegacyRoomCompletion.objects.filter(order_id=order_id).count(), index)
