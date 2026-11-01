from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .engine import grant_entries, seed_catalog
from .models import Room, Participant, Entry, Draw, EntryGrant, Award, BonusAward
from .views import digest


@override_settings(LOTTERY_REMAINING_ONLY=False)
class RoomApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_catalog()
        cls.admin = get_user_model().objects.create_superuser('operator', password='test-only-secret-12345')
        cls.room = Room.objects.create(code='room-one', name='一号房', created_by=cls.admin,
            starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()+timedelta(days=1))
        cls.other = Room.objects.create(code='room-two', name='二号房', created_by=cls.admin,
            starts_at=cls.room.starts_at, ends_at=cls.room.ends_at)
        cls.raw = 'private-participant-code-123'
        cls.person = Participant.objects.create(room=cls.room, nickname='老板甲', code_digest=digest(cls.raw))
        cls.person2 = Participant.objects.create(room=cls.other, nickname='老板乙', code_digest=digest('other-private-code-123'))
        grant_entries(cls.person, 'small', 3, 'initial-entry-grant', '测试初始次数', cls.admin)

    def setUp(self):
        cache.clear()
        self.api = APIClient()

    def login(self):
        response = self.api.post('/api/login/', {'room': self.room.code, 'code': self.raw}, format='json')
        self.assertEqual(response.status_code, 200)
        token = response.json()['data']['token']
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+token,HTTP_X_LOTTERY_ROOM=self.room.code)
        return token

    def test_public_catalog_contains_108_cells_without_participant_data(self):
        response = self.api.get('/api/catalog/', {'room': self.room.code})
        self.assertEqual(response.status_code, 200)
        data = response.json()['data']
        self.assertEqual(sum(len(pool['cells']) for pool in data['campaigns']), 108)
        self.assertNotIn(self.raw, response.content.decode())
        self.assertNotIn('participants', data)
        self.assertNotIn('probability', response.content.decode())
        self.assertNotIn('weight', response.content.decode())

    def test_participation_code_bound_to_room_and_private_me(self):
        self.assertEqual(self.api.get('/api/me/').status_code, 401)
        self.assertEqual(self.api.post('/api/login/', {'room': self.other.code,'code': self.raw}).status_code,400)
        self.login()
        data = self.api.get('/api/me/').json()['data']
        self.assertEqual(data['nickname'], '老板甲')
        self.assertEqual(data['entries']['small'],3)
        self.assertNotIn('老板乙', str(data))

    def test_authentication_cannot_spend_other_participants_entries(self):
        self.login()
        with patch('lottery.dice.secrets.randbelow',return_value=0):
            response = self.api.post('/api/draw/', {'campaign':'small','version':1,
                'client_request_id':'same-retry-123','participant':self.person2.pk}, format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(Draw.objects.get().participant_id,self.person.pk)
        replay = self.api.post('/api/draw/', {'campaign':'small','version':1,
            'client_request_id':'same-retry-123'},format='json')
        self.assertEqual(response.json()['data']['id'],replay.json()['data']['id'])
        self.assertEqual(Entry.objects.get(participant=self.person,pool__slug='small').remaining,2)

    def test_reset_code_revokes_old_token_and_login(self):
        self.login()
        self.person.code_digest = digest('replacement-code-123456789')
        self.person.save(update_fields=['code_digest'])
        self.assertEqual(self.api.get('/api/me/').status_code,401)
        self.assertEqual(self.api.post('/api/login/',{'room':self.room.code,'code':self.raw}).status_code,400)

    def test_room_paused_stops_new_draw_and_returns_validation_envelope(self):
        self.login()
        self.room.enabled=False
        self.room.save(update_fields=['enabled'])
        response=self.api.post('/api/draw/',{'campaign':'small','version':1,'client_request_id':'room-stopped-123'})
        self.assertEqual(response.status_code,400)
        self.assertIn('message',response.json())
        self.assertEqual(Draw.objects.count(),0)
        self.room.enabled=True
        self.room.save(update_fields=['enabled'])

    def test_admin_form_grants_once_and_escapes_names(self):
        self.client.force_login(self.admin)
        payload={'action':'grant','room':self.room.code,'participant':self.person.pk,
            'request_id':'admin-grant-once','pool':'small','count':4,'reason':'活动赠送'}
        self.assertEqual(self.client.post('/control/',payload).status_code,302)
        self.assertEqual(self.client.post('/control/',payload).status_code,302)
        self.assertEqual(Entry.objects.get(participant=self.person,pool__slug='small').remaining,7)
        self.assertEqual(EntryGrant.objects.filter(event_key='grant:admin-grant-once').count(),1)
        response=self.client.get('/control/',{'room':self.room.code})
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,self.raw)

    def test_control_superadmin_and_csrf_required(self):
        self.assertEqual(self.client.get('/control/').status_code,302)
        staff=get_user_model().objects.create_user('staff',password='test-only-password',is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/control/').status_code,302)
        strict=Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        self.assertEqual(strict.post('/control/',{'action':'create_room','name':'blocked'}).status_code,403)
        self.assertEqual(Room.objects.count(),2)

    def test_admin_add_participant_issues_code_then_can_join_and_draw(self):
        self.client.force_login(self.admin)
        response=self.client.post('/control/',{'action':'add_participant','room':self.room.code,
            'request_id':'create-boss-once','nickname':'新老板','small':2,'red':1,'grand':0})
        self.assertEqual(response.status_code,200)
        issued=response.context['issued']
        self.assertNotIn('lottery_issued',self.client.session)
        self.assertEqual(len(issued['code']),24)
        response=self.api.post('/api/login/',{'room':self.room.code,'code':issued['code']})
        self.assertEqual(response.status_code,200)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+response.json()['data']['token'],HTTP_X_LOTTERY_ROOM=self.room.code)
        data=self.api.get('/api/me/').json()['data']
        self.assertEqual(data['entries'],{'small':2,'red':1,'grand':0})
        self.assertNotEqual(Participant.objects.get(nickname='新老板').code_digest,issued['code'])

    def test_login_rate_limit_and_security_headers(self):
        for _ in range(8):
            self.api.post('/api/login/',{'room':self.room.code,'code':'invalid-code-is-long-enough'})
        response=self.api.post('/api/login/',{'room':self.room.code,'code':'invalid-code-is-long-enough'})
        self.assertEqual(response.status_code,429)
        self.assertEqual(response['Cache-Control'],'no-store')

    def test_bonus_pity_is_visible_to_owner_and_admin_can_record_delivery(self):
        Entry.objects.filter(participant=self.person,pool__slug='small').update(total_rolls=29)
        self.login()
        with patch('lottery.dice.secrets.randbelow',side_effect=[0,0]):
            response=self.api.post('/api/draw/',{'campaign':'small','version':1,
                'client_request_id':'pity-thirtieth-roll'},format='json')
        self.assertEqual(response.status_code,200)
        data=response.json()['data']
        self.assertEqual(data['total_rolls'],30)
        self.assertEqual(len(data['bonuses']),1)
        bonus=BonusAward.objects.get()
        self.assertEqual(bonus.threshold,30)
        self.assertEqual(bonus.status,'PENDING')
        self.assertNotIn('row_weights',str(data))
        self.assertNotIn('row_ticket',str(data))
        self.client.force_login(self.admin)
        response=self.client.get('/control/',{'room':self.room.code})
        self.assertContains(response,'记录保底已交付')
        response=self.client.post('/control/',{'room':self.room.code,'request_id':'deliver-pity-bonus',
            'action':'fulfill_bonus','award':bonus.pk,'note':'已发放5元店铺预存'})
        self.assertEqual(response.status_code,302)
        bonus.refresh_from_db()
        self.assertEqual(bonus.status,'FULFILLED')
        self.assertEqual(bonus.fulfilled_by,self.admin)
        self.assertEqual(self.api.get('/api/me/').json()['data']['draws'][0]['bonuses'][0]['status'],'FULFILLED')

    def test_room_header_is_required_and_wrong_room_cannot_read_or_draw(self):
        token=self.login()
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(self.api.get('/api/me/').status_code,403)
        self.api.credentials(HTTP_AUTHORIZATION='Bearer '+token,HTTP_X_LOTTERY_ROOM=self.other.code)
        response=self.api.get('/api/me/')
        self.assertEqual(response.status_code,403)
        self.assertNotIn('老板甲',response.content.decode())
        response=self.api.post('/api/draw/',{'campaign':'small','version':1,'client_request_id':'cross-room-block'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(Draw.objects.count(),0)
        self.assertEqual(Entry.objects.get(participant=self.person,pool__slug='small').remaining,3)

    def test_same_room_bosses_only_receive_their_own_draws_and_awards(self):
        peer=Participant.objects.create(room=self.room,nickname='同房间另一位老板',code_digest=digest('same-room-peer-code-123'))
        grant_entries(peer,'small',2,'same-room-peer-grant','测试',self.admin)
        from .engine import draw
        with patch('lottery.dice.secrets.randbelow',side_effect=[0,1,0,0]):
            peer_draw=draw(peer,'small','same-room-peer-draw',1)
            my_draw=draw(self.person,'small','same-room-self-draw',1)
        Award.objects.filter(draw=peer_draw).update(delivery_note='同房间老板专属交付记录')
        self.login()
        data=self.api.get('/api/me/',{'participant_id':peer.pk,'room':self.other.code}).json()['data']
        self.assertEqual([item['id'] for item in data['draws']],[my_draw.pk])
        self.assertNotIn(str(peer_draw.event_key),str(data))
        self.assertNotIn('同房间老板专属交付记录',str(data))
        self.assertIsNone(data['draws'][0]['award'])
        for item in data['draws']:
            self.assertEqual(item['participant_id'],self.person.pk)
            self.assertEqual(item['room_code'],self.room.code)
        public=self.api.get('/api/catalog/',{'room':self.room.code}).json()['data']
        self.assertNotIn('draws',public)
        self.assertNotIn('participants',public)

    def test_moving_participant_to_another_room_revokes_previous_room_session(self):
        self.login()
        Participant.objects.filter(pk=self.person.pk).update(room=self.other)
        self.assertEqual(self.api.get('/api/me/').status_code,401)
