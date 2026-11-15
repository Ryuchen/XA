"""Staff catalog edits invalidate both real member storefronts."""
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.models import RewardItem
from orders.tests.factories import make_console_user, make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CHANNEL_LAYERS={'default':{'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class RewardCatalogLiveTest(TransactionTestCase):
    def test_price_confirmation_rejects_change_and_retries_existing_success(self):
        boss,staff=make_user(),make_console_user(['engagement:edit','engagement:points'])
        accounts=[get_or_create_account_for_legacy_user(user) for user in (boss,staff)]
        staff.admin_membership.account=accounts[1]; staff.admin_membership.save(update_fields=['account'])
        client,admin=APIClient(),APIClient()
        for api,account in zip((client,admin),accounts): api.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        credit=admin.post('/api/admin/external-points/',{'account_id':accounts[0].pk,'kind':'KOOK_CHECKIN',
            'day':timezone.localdate().isoformat(),'source_id':'confirm-price'},format='json')
        self.assertEqual(credit.data['code'],0,credit.data)
        reward=RewardItem.objects.create(code='price-confirm',name='积分权益',kind='SERVICE',audience='ALL',point_cost=1,stock=1,in_shop=True,is_active=True)
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{reward.pk}/',{'point_cost':2},format='json').data['code'],0)
        terms={'name':reward.name,'kind':reward.kind,'description':'','balance_amount':0,'point_value':0,
            'duration_days':0,'duration_months':0,'highlighted':False,'service_id':None}
        payload={'reward_id':reward.pk,'client_request_id':'confirmed-price','expected_point_cost':1,'expected_terms':terms}
        rejected=client.post('/api/club/redeem/',payload,format='json')
        self.assertEqual((rejected.status_code,rejected.data['errorCode']),(409,'REWARD_PRICE_CHANGED'))
        self.assertEqual(client.get('/api/club/points/').data['data']['points'],5)
        reward.refresh_from_db(); self.assertEqual(reward.stock,1)
        payload['expected_point_cost']=2
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{reward.pk}/',{'description':'更新后的权益'},format='json').data['code'],0)
        changed=client.post('/api/club/redeem/',payload,format='json')
        self.assertEqual((changed.status_code,changed.data['errorCode']),(409,'REWARD_TERMS_CHANGED'))
        self.assertEqual(client.get('/api/club/points/').data['data']['points'],5)
        terms['description']='更新后的权益'
        for change in ({'is_active':False},{'is_active':True,'in_shop':False},{'in_shop':True,'stock':0,'redeemed_count':0}):
            self.assertEqual(admin.patch(f'/api/admin/club-rewards/{reward.pk}/',change,format='json').data['code'],0)
            unavailable=client.post('/api/club/redeem/',payload,format='json')
            self.assertEqual((unavailable.status_code,unavailable.data['errorCode']),(400,'REWARD_UNAVAILABLE'))
            self.assertEqual(client.get('/api/club/points/').data['data']['points'],5)
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{reward.pk}/',{'stock':1,'redeemed_count':0},format='json').data['code'],0)
        first=client.post('/api/club/redeem/',payload,format='json'); self.assertEqual(first.data['code'],0,first.data)
        self.assertEqual(admin.patch(f'/api/admin/club-rewards/{reward.pk}/',{'point_cost':3,'in_shop':False},format='json').data['code'],0)
        repeated=client.post('/api/club/redeem/',payload,format='json'); self.assertEqual(repeated.data['data']['id'],first.data['data']['id'])
        self.assertEqual(client.get('/api/club/points/').data['data']['points'],3)

    def test_staff_disables_and_reenables_item_for_both_roles(self):
        boss,provider,staff=make_user(),make_provider(),make_console_user(['engagement:edit'])
        accounts=[get_or_create_account_for_legacy_user(user) for user in (boss,provider,staff)]
        provider.escort_profile.account=accounts[1]; provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account=accounts[2]; staff.admin_membership.save(update_fields=['account'])
        clients=[APIClient() for _ in accounts]
        for client,account in zip(clients,accounts): client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
        reward=RewardItem.objects.create(code='catalog-live',name='实时库存',kind='SERVICE',audience='ALL',point_cost=1,stock=1,in_shop=True,is_active=True)
        async def verify():
            sockets=[WebsocketCommunicator(application,f'/ws/orders/?token={issue_account_tokens(account)[0]}') for account in accounts[:2]]
            for socket in sockets: self.assertTrue((await socket.connect())[0])
            try:
                for active in (False,True):
                    response=await database_sync_to_async(clients[2].patch)(f'/api/admin/club-rewards/{reward.pk}/',{'is_active':active},format='json')
                    self.assertEqual(response.data['code'],0,response.data)
                    for socket in sockets: self.assertEqual((await socket.receive_json_from(timeout=2))['type'],'reward_catalog_update')
                    for client in clients[:2]:
                        response=await database_sync_to_async(client.get)('/api/club/rewards/')
                        self.assertEqual(any(row['id']==reward.pk for row in response.data['data']),active)
            finally:
                for socket in sockets: await socket.disconnect()
        async_to_sync(verify)()
