"""Completed team service to an explicitly selected gift recipient."""
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TeamGiftWorkflowTest(LiveServerTestCase):
    def test_boss_selects_one_recipient_and_only_that_provider_receives_gift_income(self):
        media = TemporaryDirectory(prefix='xa-team-gift-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        providers = [make_provider(display_name=name, is_verified=True, deposit_mode='EARNINGS', pass_tier='BLACK',
                                   pass_expires_at=timezone.now() + timedelta(days=7))
                     for name in ('收礼陪玩甲', '收礼陪玩乙')]
        staff = make_console_user(['wallet:recharge', 'order:view', 'order:refund', 'dashboard:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, *providers, staff)]
        staff.admin_membership.account = accounts[-1]
        staff.admin_membership.save(update_fields=['account'])
        service = make_service(price=10000, billing_provider_count=2, fulfillment_provider_count=2)
        for user, account in zip(providers, accounts[1:3]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        category = ServiceCategory.objects.create(name='礼物', is_gift=True)
        gift = make_service(name='隔离礼物', price=2100, service_category=category, commission_rate=20)
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)

        def ok(response):
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data['data']

        ok(clients[-1].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk,
           'amount': 50000, 'gift_amount': 0, 'trade_no': 'team-gift-recharge',
           'proof_image': screenshot(), 'client_request_id': 'team-gift-recharge'}, format='multipart'))
        order_id = ok(clients[0].post('/api/orders/create/', {'service_id': service.pk,
            'provider_ids': [account.pk for account in accounts[1:3]], 'client_request_id': 'team-gift-source'}, format='json'))['id']
        for client in clients[1:3]:
            ok(client.post(f'/api/orders/orders/{order_id}/start/', {'entry_image': screenshot()}, format='multipart'))
        for client in clients[1:3]:
            ok(client.post(f'/api/orders/orders/{order_id}/complete/', {'completion_image': screenshot()}, format='multipart'))
        ok(clients[0].post(f'/api/orders/orders/{order_id}/tip/', {'gift_service_id': gift.pk, 'quantity': 1,
            'provider_id': accounts[2].pk, 'expected_amount': 2100, 'client_request_id': 'team-gift-source-send'}, format='json'))
        gift_order = Order.objects.get(source_order_id=order_id, is_gift_snapshot=True)
        self.assertEqual(gift_order.provider_account_id, accounts[2].pk)
        self.assertEqual(gift_order.amount, 2100)
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 27900)
        self.assertFalse(Wallet.objects.filter(account=accounts[1], balance__gt=0).exists())
        recipient_wallet = Wallet.objects.get(account=accounts[2])
        self.assertEqual((recipient_wallet.balance, recipient_wallet.frozen_amount), (0, 1680))
        inbox = ok(clients[2].get('/api/messages/'))['messages']
        notices = [row for row in inbox if row['title'] == '收到老板礼物' and row['related_order_id'] == gift_order.pk]
        self.assertEqual(len(notices), 1)
        self.assertIn('隔离礼物', notices[0]['preview'])
        self.assertFalse(any(row['related_order_id'] == gift_order.pk for row in ok(clients[1].get('/api/messages/'))['messages']))
        source_number = Order.objects.get(pk=order_id).order_no
        boss_detail = ok(clients[0].get(f'/api/orders/orders/{gift_order.pk}/'))
        self.assertEqual((boss_detail['source_order'], boss_detail['source_order_no']), (order_id, source_number))
        provider_records = ok(clients[2].get('/api/wallet/provider-benefits/'))['gift_records']
        self.assertEqual([(row['id'], row['source_order_no'], row['received_income']) for row in provider_records],
                         [(gift_order.pk, source_number, 1680)])
        self.assertEqual(ok(clients[1].get('/api/wallet/provider-benefits/'))['gift_records'], [])
        admin_detail = ok(clients[-1].get(f'/api/admin/orders/{gift_order.pk}/'))
        self.assertEqual((admin_detail['source_order'], admin_detail['source_order_no']), (order_id, source_number))
        ok(clients[0].post('/api/orders/gifts/send/', {'gift_service_id': gift.pk, 'quantity': 1,
            'provider_id': accounts[1].pk, 'expected_amount': 2100, 'client_request_id': 'team-gift-direct-send'}, format='json'))
        direct_order = Order.objects.get(customer_account=accounts[0], is_gift_snapshot=True, source_order__isnull=True)
        self.assertEqual(direct_order.provider_account_id, accounts[1].pk)
        direct_recipient_wallet = Wallet.objects.get(account=accounts[1])
        self.assertEqual((direct_recipient_wallet.balance, direct_recipient_wallet.frozen_amount), (0, 1680))
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 25800)
        self.assertEqual(len([row for row in ok(clients[1].get('/api/messages/'))['messages']
                              if row['title'] == '收到老板礼物' and row['related_order_id'] == direct_order.pk]), 1)
        self.assertEqual(ok(clients[0].get(f'/api/orders/orders/{direct_order.pk}/'))['source_order_no'], '')
        self.assertEqual(ok(clients[1].get('/api/wallet/provider-benefits/'))['gift_records'][0]['source_order_no'], '')
        dashboard = ok(clients[-1].get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['today_order_total'], dashboard['today_service_order_total'], dashboard['today_gift_order_total']), (3, 1, 2))
        self.assertEqual((dashboard['order_amount_total'], dashboard['gift_order_net_amount']), (24200, 4200))
        day = dashboard['daily_new_orders'][-1]['date']
        for kind, ids in [('SERVICE', {order_id}), ('GIFT', {gift_order.pk, direct_order.pk})]:
            rows = ok(clients[-1].get('/api/admin/orders/', {'created_day': day, 'order_kind': kind, 'exclude_direct_test': '1'}))
            self.assertEqual(rows['order_kind_applied'], kind)
            self.assertEqual({row['id'] for row in rows['list']}, ids)
        preview = ok(clients[-1].get(f'/api/admin/orders/{gift_order.pk}/refund-preview/', {'refund_amount': 500}))
        ok(clients[-1].post(f'/api/admin/orders/{gift_order.pk}/refund/',
            {'reason': '隔离验收礼物部分退款', 'refund_amount': 500, 'plan_token': preview['plan_token'], 'client_request_id': 'team-gift-partial-refund'}, format='json'))
        record = next(row for row in ok(clients[2].get('/api/wallet/provider-benefits/'))['gift_records'] if row['id'] == gift_order.pk)
        self.assertEqual((record['settlement_status'], record['received_income'], record['frozen_income']),
                         ('PARTIALLY_REFUNDED_FROZEN', 1280, 1280))
        dashboard = ok(clients[-1].get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['order_amount_total'], dashboard['gift_order_net_amount']), (23700, 3700))
        preview = ok(clients[-1].get(f'/api/admin/orders/{gift_order.pk}/refund-preview/'))
        ok(clients[-1].post(f'/api/admin/orders/{gift_order.pk}/refund/',
            {'reason': '隔离验收礼物退款', 'plan_token': preview['plan_token'], 'client_request_id': 'team-gift-final-refund'}, format='json'))
        record = next(row for row in ok(clients[2].get('/api/wallet/provider-benefits/'))['gift_records'] if row['id'] == gift_order.pk)
        self.assertEqual((record['settlement_status'], record['received_income']), ('REFUNDED', 0))
        self.assertEqual(ok(clients[-1].get(f'/api/admin/orders/{gift_order.pk}/'))['payment_status'], 'REFUNDED')
        self.assertEqual(Wallet.objects.get(account=accounts[0]).balance, 27900)
        for client in (clients[0], clients[2]):
            rows = [row for row in ok(client.get('/api/messages/'))['messages']
                    if row['related_order_id'] == gift_order.pk and row['title'] in ('订单部分退款', '订单已退款')]
            self.assertEqual({row['title'] for row in rows}, {'订单部分退款', '订单已退款'})
            self.assertEqual(len(rows), 2)
            for row in rows:
                detail = ok(client.get(f"/api/messages/{row['id']}/"))
                self.assertEqual(detail['related_order_id'], gift_order.pk)
                self.assertTrue(detail['is_read'])
            if client is clients[0]:
                self.assertIn('50 浪花', next(row['preview'] for row in rows if row['title'] == '订单部分退款'))
                self.assertIn('160 浪花', next(row['preview'] for row in rows if row['title'] == '订单已退款'))
        self.assertFalse(any(row['related_order_id'] == gift_order.pk
                             for row in ok(clients[1].get('/api/messages/'))['messages']))
        dashboard = ok(clients[-1].get('/api/admin/dashboard/stats'))
        self.assertEqual((dashboard['today_service_order_total'], dashboard['today_gift_order_total']), (1, 2))
        self.assertEqual((dashboard['order_amount_total'], dashboard['gift_order_net_amount']), (22100, 2100))
