"""Customer service receives actual coupon claim updates over the operator socket."""
import os
from unittest import skipUnless

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount, LegacyAccountMap
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from coupons.models import Coupon, UserCoupon
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet


@skipUnless(os.environ.get('XA_ADMIN_COUPON_ROOT'), 'requires isolated admin build and Redis')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AdminCouponClaimRealtimeTest(LiveServerTestCase):
    def test_boss_claim_updates_staff_list_and_open_records(self):
        self.run_claim_flow(account_only=False)

    def test_account_only_boss_claim_updates_staff_records(self):
        self.run_claim_flow(account_only=True)

    def run_claim_flow(self, *, account_only):
        staff = make_console_user(['coupon:view'])
        editor = make_console_user(['coupon:view', 'coupon:edit', 'coupon:delete'])
        if account_only:
            boss_account = ClubAccount.objects.create_account(username='account-only-claim', account_type='BOSS', nickname='新账号领券老板')
            from wallet.services import get_wallet
            wallet = get_wallet(account=boss_account); wallet.balance = 50000; wallet.save(update_fields=['balance'])
        else:
            boss = make_user(nickname='实时领券老板')
            boss_account = get_or_create_account_for_legacy_user(boss)
            link_legacy_relations(boss, boss_account)
            make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        editor_account = get_or_create_account_for_legacy_user(editor)
        editor.admin_membership.account = editor_account
        editor.admin_membership.save(update_fields=['account'])
        coupon = Coupon.objects.create(name='客服实时领券', discount_type='DIRECT', amount=500, total_qty=3)
        boss_client, editor_client = APIClient(), APIClient()
        boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        editor_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(editor_account)[0])
        staff_token = issue_account_tokens(staff_account)[0]

        def ok(response):
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['code'], 0, response.data)
            return response.data.get('data')

        async def verify():
            staff_socket = WebsocketCommunicator(application, f'/ws/orders/?token={staff_token}')
            self.assertTrue((await staff_socket.connect())[0])

            async def claim_event(coupon_id):
                for _ in range(10):
                    event = await staff_socket.receive_json_from(timeout=5)
                    if event['type'] == 'coupon_claim_update':
                        self.assertEqual(event['data']['coupon_id'], coupon_id)
                        return
                self.fail('no coupon claim update received')

            try:
                await database_sync_to_async(lambda: ok(boss_client.post(f'/api/coupons/{coupon.pk}/claim/')))()
                await claim_event(coupon.pk)
                mine = await database_sync_to_async(lambda: ok(boss_client.get('/api/coupons/mine/')))()
                voucher = next(item for item in mine if item['name'] == '客服实时领券')
                order = await database_sync_to_async(lambda: ok(boss_client.post('/api/orders/create/',
                    {'service_id': service.pk, 'user_coupon_id': voucher['id'], 'dispatch_route': 'STAFF',
                     'client_request_id': 'admin-coupon-live-order'}, format='json')))()
                await claim_event(coupon.pk)
                await database_sync_to_async(lambda: ok(boss_client.post(f"/api/orders/orders/{order['id']}/cancel/",
                    {'reason': '隔离业务验收'}, format='json')))()
                await claim_event(coupon.pk)
                for active in (False, True):
                    await database_sync_to_async(lambda: ok(editor_client.patch(f'/api/admin/coupons/{coupon.pk}/',
                        {'is_active': active}, format='json')))()
                    await claim_event(coupon.pk)
                created = await database_sync_to_async(lambda: ok(editor_client.post('/api/admin/coupons/',
                    {'name': '另一客服新建券', 'discount_type': 'DIRECT', 'amount': 500}, format='json')))()
                await claim_event(created['id'])
                await database_sync_to_async(lambda: ok(editor_client.delete(f"/api/admin/coupons/{created['id']}/")))()
                await claim_event(created['id'])
            finally:
                await staff_socket.disconnect()

        async_to_sync(verify)()
        coupon.refresh_from_db()
        self.assertEqual(coupon.claimed_qty, 1)
        self.assertTrue(UserCoupon.objects.filter(account=boss_account, coupon=coupon).exists())
        self.assertEqual(UserCoupon.objects.get(account=boss_account, coupon=coupon).status, 'UNUSED')
        if account_only:
            self.assertFalse(LegacyAccountMap.objects.filter(account=boss_account).exists())
            self.assertIsNone(UserCoupon.objects.get(account=boss_account, coupon=coupon).user_id)