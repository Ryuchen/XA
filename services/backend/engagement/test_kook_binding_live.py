"""KOOK identity and check-in invalidate only the bound member's page."""
from datetime import timedelta
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from config.asgi import application
from engagement.kook_points import process_event
from engagement.models import KookPointsConfig
from orders.tests.factories import make_provider, make_user


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CHANNEL_LAYERS={'default':{'BACKEND':'channels.layers.InMemoryChannelLayer'}})
class KookBindingLiveTest(TransactionTestCase):
    def test_binding_and_checkin_refresh_only_owner_and_duplicate_is_quiet(self):
        owner, other = make_provider(), make_user()
        owner_account = get_or_create_account_for_legacy_user(owner)
        other_account = get_or_create_account_for_legacy_user(other)
        owner.escort_profile.account=owner_account; owner.escort_profile.save(update_fields=['account'])
        client=APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(owner_account)[0])
        now=timezone.now(); KookPointsConfig.objects.create(pk=1,enabled=True,guild_id='live-guild',
            checkin_channel_id='hall',voice_channel_ids=[],started_at=now-timedelta(days=1))
        response=client.post('/api/club/kook-binding/',{},format='json')
        self.assertEqual(response.data['code'],0,response.data)
        code=response.data['data']['code']
        def event(content, ident):
            # External KOOK transport is represented by its received event; the handler and persistence are real.
            return {'type':1,'channel_type':'GROUP','target_id':'hall','author_id':'live-provider',
                'msg_id':ident,'msg_timestamp':int(now.timestamp()*1000),'content':content,
                'extra':{'guild_id':'live-guild','author':{'username':'陪玩','bot':False}}}
        async def verify():
            owner_socket=WebsocketCommunicator(application,f'/ws/orders/?token={issue_account_tokens(owner_account)[0]}')
            other_socket=WebsocketCommunicator(application,f'/ws/orders/?token={issue_account_tokens(other_account)[0]}')
            self.assertTrue((await owner_socket.connect())[0]); self.assertTrue((await other_socket.connect())[0])
            try:
                self.assertTrue(await database_sync_to_async(process_event)(event('绑定 '+code,'bind-event'),now))
                self.assertEqual((await owner_socket.receive_json_from(timeout=2))['type'],'member_points_update')
                binding=await database_sync_to_async(client.get)('/api/club/kook-binding/')
                self.assertTrue(binding.data['data']['bound'])
                self.assertTrue(await database_sync_to_async(process_event)(event('签到','checkin-event'),now))
                self.assertEqual((await owner_socket.receive_json_from(timeout=2))['type'],'member_points_update')
                points=await database_sync_to_async(client.get)('/api/club/points/')
                self.assertEqual(points.data['data']['points'],5)
                self.assertTrue(await database_sync_to_async(process_event)(event('签到','checkin-repeat'),now))
                self.assertTrue(await owner_socket.receive_nothing(timeout=.2))
                self.assertTrue(await other_socket.receive_nothing(timeout=.2))
            finally:
                await owner_socket.disconnect(); await other_socket.disconnect()
        async_to_sync(verify)()
