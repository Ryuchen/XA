from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .engine import (catalog_data, draw, draw_batch, grant_entries,
                     participant_data, room_status)
from .models import Award, BonusAward, Draw, DrawBatch, Entry, EntryGrant, Participant, Pool, Room
from .tests import Fixtures


class RemainingOnlyDefaultTests(SimpleTestCase):
    def test_release_defaults_to_remaining_only_without_admin_toggle(self):
        self.assertIs(settings.LOTTERY_REMAINING_ONLY, True)


class RemainingOnlyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()
        self.grant(count=12)
        self.grant(pool='red', count=12, key='old-red-grant')
        self.grant(pool='grand', count=12, key='old-grand-grant')
        self.room.ends_at = timezone.now() - timedelta(hours=1)
        self.room.save(update_fields=['ends_at'])
        self.enterContext(override_settings(LOTTERY_REMAINING_ONLY=True))

    def login(self):
        api = APIClient()
        response = api.post('/api/login/', {'room': self.room.code, 'code': self.raw_code}, format='json')
        self.assertEqual(response.status_code, 200)
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['data']['token'],
                        HTTP_X_LOTTERY_ROOM=self.room.code)
        return api

    def facts(self):
        return {model._meta.label: list(model.objects.order_by('pk').values())
                for model in [Room, Participant, Pool, Entry, EntryGrant, Draw, DrawBatch, Award, BonusAward]}

    def test_expired_enabled_room_remains_active_without_changing_stored_dates(self):
        before = self.facts()
        self.assertEqual(room_status(self.room), 'active')
        catalog = catalog_data(self.room)
        self.assertTrue(catalog['remaining_only'])
        self.assertFalse(catalog['entry_grants_enabled'])
        self.assertEqual(catalog['ends_at'], self.room.ends_at)
        self.assertEqual(catalog['room']['ends_at'], self.room.ends_at)
        self.assertEqual(catalog['room']['status'], 'active')
        self.assertTrue(all(pool['status'] == 'active' for pool in catalog['campaigns']))
        self.assertTrue(any('历史剩余次数' in rule for rule in catalog['rules']))
        self.assertFalse(any('结束后未使用次数无法抽奖' in rule for rule in catalog['rules']))
        self.assertEqual(self.facts(), before)

    def test_existing_participation_code_can_login_and_single_draw_after_expiry(self):
        api = self.login()
        with patch('lottery.dice.secrets.randbelow', side_effect=[0, 1]):
            response = api.post('/api/draw/', {'campaign': 'small', 'version': 1,
                                'client_request_id': 'remaining-single'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['entries_remaining'], 11)
        self.assertEqual(response.data['data']['award']['data']['point_value'], 520)
        self.assertEqual(api.get('/api/history/').data['data']['pagination']['total'], 1)
        self.assertEqual(EntryGrant.objects.count(), 3)

    def test_expired_ten_draw_retains_refund_pity_and_unchanged_natural_probability_policy(self):
        Entry.objects.filter(participant=self.person, pool__slug='grand').update(total_rolls=145)
        api = self.login()
        # One coupon transferred to THANKS, one RETRY, eight original THANKS.
        with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 1, 0, 3, 1] + [0, 0] * 8):
            response = api.post('/api/draw-batch/', {'campaign': 'grand', 'version': 1,
                                'client_request_id': 'remaining-ten'}, format='json')
        self.assertEqual(response.status_code, 200)
        result = response.data['data']
        self.assertEqual((result['entries_consumed'], result['entries_remaining'], result['total_rolls']), (9, 3, 155))
        self.assertEqual(len(result['draws']), 10)
        self.assertEqual(sorted(b['data']['balance_amount'] for b in result['bonuses']), [2000, 4000])
        self.assertTrue(all(d.probability_snapshot['algorithm'] == 'targeted-quarter-v6' for d in Draw.objects.all()))
        self.assertFalse(Award.objects.exists())
        self.assertEqual(EntryGrant.objects.count(), 3)

    def test_zero_remaining_cannot_draw_or_overdraw_and_batch_under_ten_suggests_single(self):
        Entry.objects.filter(pool__slug='small').update(remaining=0, consumed=12)
        Entry.objects.filter(pool__slug='red').update(remaining=9, consumed=3)
        before = self.facts()
        with patch('lottery.dice.secrets.randbelow') as rng:
            with self.assertRaisesMessage(ValidationError, '不再添加次数'):
                draw(self.person, 'small', 'remaining-empty', 1)
            with self.assertRaisesMessage(ValidationError, '可使用单抽'):
                draw_batch(self.person, 'red', 'remaining-too-few', 1)
        rng.assert_not_called()
        self.assertEqual(self.facts(), before)

    def test_paused_room_future_start_disabled_participant_and_pool_still_block_draws(self):
        original_start = self.room.starts_at
        with patch('lottery.dice.secrets.randbelow') as rng:
            for condition in ['paused', 'future', 'participant', 'pool']:
                with self.subTest(condition=condition):
                    Room.objects.filter(pk=self.room.pk).update(enabled=condition != 'paused',
                        starts_at=timezone.now() + timedelta(days=1) if condition == 'future' else original_start,
                        ends_at=timezone.now() + timedelta(days=2) if condition == 'future' else self.room.ends_at)
                    Participant.objects.filter(pk=self.person.pk).update(enabled=condition != 'participant')
                    Pool.objects.filter(slug='small').update(enabled=condition != 'pool')
                    with self.assertRaises(ValidationError):
                        draw(self.person, 'small', 'remaining-block-' + condition, 1)
            rng.assert_not_called()
        self.assertFalse(Draw.objects.exists())

    def test_service_rejects_new_grant_including_creation_of_missing_entry(self):
        person = Participant.objects.create(room=self.room, nickname='历史零次数老板', code_digest='a' * 64)
        before = self.facts()
        with self.assertRaisesMessage(ValidationError, '已停止'):
            grant_entries(person, 'red', 1, 'new-entry-blocked', '禁止新增', self.admin)
        self.assertFalse(Entry.objects.filter(participant=person).exists())
        self.assertEqual(self.facts(), before)

    def test_service_grant_replay_returns_original_without_new_opportunities(self):
        before = self.facts()
        existing = grant_entries(self.person, 'small', 12, 'grant-key-0001', '活动赠送', self.admin)
        self.assertEqual(existing.count, 12)
        self.assertEqual(self.facts(), before)
        with self.assertRaises(ValidationError):
            grant_entries(self.person, 'small', 13, 'grant-key-0001', '活动赠送', self.admin)
        with self.assertRaisesMessage(ValidationError, '已停止'):
            grant_entries(self.person, 'small', 1, 'new-disallowed-grant', '禁止新增', self.admin)
        self.assertEqual(self.facts(), before)

    def test_control_direct_posts_and_stale_forms_cannot_add_even_zero_count_participants(self):
        self.client.force_login(self.admin)
        before = self.facts()
        payloads = [
            {'action': 'create_room', 'name': 'blocked-room'},
            {'action': 'add_participant', 'nickname': 'blocked-boss', 'small': 0, 'red': 0, 'grand': 0},
            {'action': 'add_participant', 'nickname': 'blocked-boss', 'small': 5, 'red': 3, 'grand': 1},
            {'action': 'grant', 'participant': self.person.pk, 'pool': 'small', 'count': 10, 'reason': 'blocked'},
        ]
        for index, payload in enumerate(payloads):
            response = self.client.post('/control/', {**payload, 'room': self.room.code,
                'request_id': f'blocked-admin-{index}'}, follow=True)
            self.assertContains(response, '已停止新建房间、添加老板和发放次数')
            self.assertEqual(self.facts(), before)

    def test_control_hides_admission_forms_and_preserves_management_actions(self):
        self.client.force_login(self.admin)
        response = self.client.get('/control/', {'room': self.room.code})
        self.assertContains(response, '历史剩余次数补抽')
        for action in ['create_room', 'add_participant', 'grant']:
            self.assertNotContains(response, 'name="action" value="' + action + '"')
        for action in ['reset_code', 'toggle_room', 'toggle_participant']:
            self.assertContains(response, 'name="action" value="' + action + '"')
        for model in [Room, Participant, Entry, EntryGrant]:
            model_admin = admin.site._registry[model]
            self.assertFalse(model_admin.has_add_permission(response.wsgi_request))
            self.assertFalse(model_admin.has_change_permission(response.wsgi_request))

    def test_original_participation_code_can_be_reset_without_adding_opportunities(self):
        self.client.force_login(self.admin)
        entries = list(Entry.objects.order_by('pk').values())
        response = self.client.post('/control/', {'room': self.room.code, 'action': 'reset_code',
            'participant': self.person.pk, 'request_id': 'remaining-reset-code'})
        self.assertEqual(response.status_code, 200)
        issued = response.context['issued']
        api = APIClient()
        self.assertEqual(api.post('/api/login/', {'room': self.room.code, 'code': self.raw_code}).status_code, 400)
        self.assertEqual(api.post('/api/login/', {'room': self.room.code, 'code': issued['code']}).status_code, 200)
        self.assertEqual(list(Entry.objects.order_by('pk').values()), entries)
        self.assertEqual(Participant.objects.count(), 1)

    def test_admin_can_resume_existing_room_and_participant_without_changing_entries(self):
        Room.objects.filter(pk=self.room.pk).update(enabled=False)
        Participant.objects.filter(pk=self.person.pk).update(enabled=False)
        self.client.force_login(self.admin)
        before = list(Entry.objects.order_by('pk').values())
        for action in ['toggle_room', 'toggle_participant']:
            response = self.client.post('/control/', {'room': self.room.code, 'action': action,
                'participant': self.person.pk, 'request_id': 'existing-' + action})
            self.assertEqual(response.status_code, 302)
        self.room.refresh_from_db()
        self.person.refresh_from_db()
        self.assertEqual(room_status(self.room), 'active')
        self.assertTrue(self.person.enabled)
        self.assertEqual(list(Entry.objects.order_by('pk').values()), before)

    def test_awards_and_bonus_can_be_fulfilled_during_remaining_only(self):
        Entry.objects.filter(pool__slug='small').update(total_rolls=29)
        result = self.pick(column=2, key='remaining-award-bonus')
        self.client.force_login(self.admin)
        before = list(Entry.objects.order_by('pk').values())
        for action, award in [('fulfill', result.award), ('fulfill_bonus', result.bonuses.get())]:
            response = self.client.post('/control/', {'room': self.room.code, 'action': action,
                'award': award.pk, 'note': '补抽权益已交付', 'request_id': 'remaining-' + action})
            self.assertEqual(response.status_code, 302)
            award.refresh_from_db()
            self.assertEqual(award.status, 'FULFILLED')
        self.assertEqual(list(Entry.objects.order_by('pk').values()), before)

    def test_committed_single_and_ten_replay_after_mode_switch_preserves_original_results(self):
        with override_settings(LOTTERY_REMAINING_ONLY=False):
            Room.objects.filter(pk=self.room.pk).update(ends_at=timezone.now() + timedelta(days=1))
            single = self.pick(key='prior-single-mode')
            with patch('lottery.dice.secrets.randbelow', return_value=0):
                batch = draw_batch(self.person, 'small', 'prior-batch-mode', 1)
        Room.objects.filter(pk=self.room.pk).update(ends_at=self.room.ends_at)
        before = self.facts()
        with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not roll again')):
            self.assertEqual(draw(self.person, 'small', 'prior-single-mode', 1).pk, single.pk)
            self.assertEqual(draw_batch(self.person, 'small', 'prior-batch-mode', 1).pk, batch.pk)
        self.assertEqual(self.facts(), before)

    @override_settings(STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    })
    def test_public_pages_explain_remaining_only_and_owner_history_stays_private(self):
        api = self.login()
        result = self.pick(key='remaining-owner-record')
        data = api.get('/api/history/').data['data']
        self.assertEqual([d['id'] for d in data['draws']], [result.pk])
        self.assertEqual(api.get('/api/me/').data['data']['entries']['small'], 11)
        for url in ['/', '/r/' + self.room.code + '/']:
            response = self.client.get(url)
            self.assertContains(response, '剩余次数补抽')
            self.assertNotContains(response, '需要增加次数，请联系房间管理员')
        api.credentials()
        self.assertEqual(api.get('/api/history/').status_code, 401)
