"""Real gift and claim actions invalidate only authorized staff queues."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.models import GiftBenefitRule, RewardItem
from orders.models import ServiceCategory
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from orders.tests.test_release_flow import screenshot


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ClaimQueueRealtimeTest(TransactionTestCase):
    def test_tag_review_queue_refreshes_after_suspend_and_review(self):
        boss = make_user()
        staff = make_console_user(['dashboard:view', 'engagement:fulfill', 'engagement:points'])
        viewer = make_console_user(['dashboard:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, staff, viewer)]
        for user, account in ((staff, accounts[1]), (viewer, accounts[2])):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        clients = []
        for account in accounts:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); clients.append(client)
        credit = clients[1].post('/api/admin/external-points/', {'account_id':accounts[0].pk, 'kind':'KOOK_CHECKIN',
            'day':timezone.localdate().isoformat(), 'source_id':'tag-live-checkin'}, format='json')
        self.assertEqual(credit.data['code'],0,credit.data)
        reward = RewardItem.objects.create(code='tag-live-queue',name='实时标签',kind='TAG',point_cost=1,in_shop=True,is_active=True)
        claim = clients[0].post('/api/club/redeem/', {'reward_id':reward.pk,'details':{'tag':'实时标签'},
            'client_request_id':'tag-live-redeem'}, format='json').data['data']
        completed = clients[1].post(f"/api/admin/club-claims/{claim['id']}/process/",
            {'status':'COMPLETED','note':'审核通过','tag_reviewed':True},format='json')
        self.assertEqual(completed.data['code'],0,completed.data)
        self.assertEqual(clients[1].get('/api/admin/dashboard/stats').data['data']['pending_tag_review_total'],0)
        self.assertIsNone(clients[2].get('/api/admin/dashboard/stats').data['data']['pending_tag_review_total'])

        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={issue_account_tokens(account)[0]}') for account in accounts[1:]]
            for socket in sockets: self.assertTrue((await socket.connect())[0])
            async def next_claim():
                while True:
                    event=await sockets[0].receive_json_from(timeout=3)
                    if event['type']=='claim_queue_update': return event
            try:
                paused=await database_sync_to_async(clients[1].post)(f"/api/admin/club-claims/{claim['id']}/suspend-tag/",
                    {'reason':'内容需复核','expected_tag':'实时标签'},format='json')
                self.assertEqual(paused.data['code'],0,paused.data)
                await next_claim()
                snapshot=await database_sync_to_async(clients[1].get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_tag_review_total'],1)
                approved=await database_sync_to_async(clients[1].post)(f"/api/admin/club-claims/{claim['id']}/review-tag/",
                    {'tag_reviewed':True,'expected_tag':'实时标签'},format='json')
                self.assertEqual(approved.data['code'],0,approved.data)
                await next_claim()
                snapshot=await database_sync_to_async(clients[1].get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_tag_review_total'],0)
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.2))
            finally:
                for socket in sockets: await socket.disconnect()
        async_to_sync(verify)()

    def test_real_gift_details_and_delivery_refresh_authorized_queue(self):
        boss, provider = make_user(), make_provider(is_verified=True, deposit_mode='CASH')
        retire_boss = make_user()
        staff = make_console_user(['wallet:recharge', 'dashboard:view', 'engagement:fulfill', 'engagement:points', 'order:view', 'order:refund', 'user:view', 'user:edit'])
        viewer = make_console_user(['engagement:view'])
        accounts = [get_or_create_account_for_legacy_user(user) for user in (boss, provider, staff, viewer)]
        retire_account = get_or_create_account_for_legacy_user(retire_boss)
        provider.escort_profile.account = accounts[1]
        provider.escort_profile.save(update_fields=['account'])
        for user, account in ((staff, accounts[2]), (viewer, accounts[3])):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='实时赠礼', is_gift=True)
        gift = make_service(name='实时礼物', price=2100, service_category=category)
        reward = RewardItem.objects.create(code='realtime-gift-claim', name='实时权益', kind='SERVICE', in_shop=False)
        redeem_reward = RewardItem.objects.create(code='realtime-retire-claim', name='退店权益', kind='SERVICE',
            point_cost=1, in_shop=True, is_active=True)
        GiftBenefitRule.objects.create(name='实时权益规则', reward=reward, category=category,
            threshold=2100, inclusive=True, enabled=True)
        clients = []
        for account in accounts:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            clients.append(client)
        retire_client = APIClient()
        retire_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(retire_account)[0])
        recharge = clients[2].post('/api/admin/wallets/recharge/', {'account_id': accounts[0].pk, 'amount': 10000,
            'gift_amount': 0, 'trade_no': 'claim-live-recharge', 'proof_image': screenshot(),
            'client_request_id': 'claim-live-recharge'}, format='multipart')
        self.assertEqual(recharge.data['code'], 0, recharge.data)
        async def verify():
            sockets = [WebsocketCommunicator(application, f'/ws/orders/?token={issue_account_tokens(account)[0]}') for account in accounts[2:4]]
            for socket in sockets:
                self.assertTrue((await socket.connect())[0])
            async def next_claim():
                while True:
                    event = await sockets[0].receive_json_from(timeout=2)
                    if event['type'] == 'claim_queue_update':
                        self.assertEqual(event['data'], {})
                        return
            try:
                gift_response = await database_sync_to_async(clients[0].post)('/api/orders/gifts/send/', {
                    'gift_service_id': gift.pk, 'quantity': 1, 'provider_id': accounts[1].pk,
                    'expected_amount': 2100, 'client_request_id': 'claim-live-gift'}, format='json')
                self.assertEqual(gift_response.data['code'], 0, gift_response.data)
                await next_claim()
                claims = await database_sync_to_async(clients[0].get)('/api/club/claims/')
                claim_id = claims.data['data'][0]['id']
                patch = await database_sync_to_async(clients[0].patch)(f'/api/club/claims/{claim_id}/',
                    {'details': {'game_uid': 'realtime-uid'}}, format='json')
                self.assertEqual(patch.data['code'], 0, patch.data)
                await next_claim()
                completed = await database_sync_to_async(clients[2].post)(f'/api/admin/club-claims/{claim_id}/process/',
                    {'status': 'COMPLETED', 'note': '隔离完成'}, format='json')
                self.assertEqual(completed.data['code'], 0, completed.data)
                await next_claim()
                another = await database_sync_to_async(clients[0].post)('/api/orders/gifts/send/', {
                    'gift_service_id': gift.pk, 'quantity': 1, 'provider_id': accounts[1].pk,
                    'expected_amount': 2100, 'client_request_id': 'claim-live-gift-refund'}, format='json')
                self.assertEqual(another.data['code'], 0, another.data)
                await next_claim()
                order_id = another.data['data']['id']
                preview = await database_sync_to_async(clients[2].get)(f'/api/admin/orders/{order_id}/refund-preview/')
                refund = await database_sync_to_async(clients[2].post)(f'/api/admin/orders/{order_id}/refund/', {
                    'reason': '隔离礼物退款', 'plan_token': preview.data['data']['plan_token'],
                    'client_request_id': 'claim-live-gift-refund-op'}, format='json')
                self.assertEqual(refund.data['code'], 0, refund.data)
                await next_claim()
                points = await database_sync_to_async(clients[2].post)('/api/admin/external-points/', {
                    'account_id': retire_account.pk, 'kind': 'KOOK_CHECKIN',
                    'day': timezone.localdate().isoformat(), 'source_id': '隔离已核对签到'}, format='json')
                self.assertEqual(points.data['code'], 0, points.data)
                redeemed = await database_sync_to_async(retire_client.post)('/api/club/redeem/', {
                    'reward_id': redeem_reward.pk, 'client_request_id': 'claim-live-retire-redeem'}, format='json')
                self.assertEqual(redeemed.data['code'], 0, redeemed.data)
                await next_claim()
                retired = await database_sync_to_async(clients[2].post)(f'/api/admin/users/{retire_account.pk}/retire/',
                    {'reason': '隔离退店验收'}, format='json')
                self.assertEqual(retired.data['code'], 0, retired.data)
                await next_claim()
                self.assertTrue(await sockets[1].receive_nothing(timeout=0.2))
                snapshot = await database_sync_to_async(clients[2].get)('/api/admin/dashboard/stats')
                self.assertEqual(snapshot.data['data']['pending_claim_total'], 0)
            finally:
                for socket in sockets:
                    await socket.disconnect()
        async_to_sync(verify)()
