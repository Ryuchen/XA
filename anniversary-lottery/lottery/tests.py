import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from .catalog import DENOMINATOR, MATRICES, NAMES
from .engine import (catalog_data, draw, draw_data, fulfill_award, grant_entries,
                     participant_data, seed_catalog)
from .dice import DENOMINATOR as DICE_DENOMINATOR, REDUCED_LABELS, compute_weights
from .models import Award, BonusAward, Cell, Draw, Entry, EntryGrant, Participant, Pool, Room


class Fixtures:
    def setup_lottery(self):
        # Existing regression scenarios exercise the original admission phase.
        self.enterContext(override_settings(LOTTERY_REMAINING_ONLY=False))
        cache.clear()
        seed_catalog()
        self.admin = get_user_model().objects.create_superuser('lottery-admin', 'admin@example.invalid', 'test-secret-only')
        self.room = Room.objects.create(code='test-room', name='测试房间', created_by=self.admin,
            starts_at=timezone.now() - timedelta(days=1), ends_at=timezone.now() + timedelta(days=5))
        self.raw_code = 'participant-access-secret'
        self.person = Participant.objects.create(room=self.room, nickname='老板甲',
            code_digest=hashlib.sha256(self.raw_code.encode()).hexdigest())

    def grant(self, pool='small', count=1, key='grant-key-0001'):
        return grant_entries(self.person, pool, count, key, '活动赠送', self.admin)

    def ticket(self, pool, row, column):
        entry = Entry.objects.filter(participant=self.person, pool__slug=pool).first()
        rows, columns = compute_weights(pool, {'total_rolls': entry.total_rolls if entry else 0,
                                               'ordinary_dry_streak': entry.ordinary_dry_streak if entry else 0})
        tickets = [sum(rows[:row - 1]), sum(columns[:column - 1])]
        if MATRICES[pool][row - 1][column - 1] in REDUCED_LABELS.get(pool, ()):
            tickets.append(0)  # Retain a targeted prize for tests of its award data.
        return tickets

    def pick(self, pool='small', row=1, column=1, key='draw-key-0001'):
        with patch('lottery.dice.secrets.randbelow', side_effect=self.ticket(pool, row, column)) as random:
            result = draw(self.person, pool, key, 1)
        expected = [call(DICE_DENOMINATOR), call(DICE_DENOMINATOR)]
        if MATRICES[pool][row - 1][column - 1] in REDUCED_LABELS.get(pool, ()):
            expected.append(call(4))
        self.assertEqual(random.call_args_list, expected)
        return result


class LotteryEngineTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()

    def test_seed_matches_all_108_poster_cells_without_changing_legacy_records(self):
        self.assertEqual(Pool.objects.count(), 3)
        self.assertEqual(Cell.objects.count(), 108)
        # Independent transcription fixture makes wrong row/column placement visible.
        fixtures = {
            'small': '感谢参与|520积分|感谢参与|120积分|感谢参与|冠名5天\n240积分|感谢参与|高亮TAG*1|感谢参与|360积分|5元店铺余额\n感谢参与|冠名7天|再来一次|感谢参与|240积分|95折优惠券\n10元店铺余额|感谢参与|120积分|冠名3天|感谢参与|冠名7天\n感谢参与|360积分|感谢参与|240积分|感谢参与|冠名5天\n95折优惠券|感谢参与|感谢参与|360积分|冠名3天|感谢参与',
            'red': '冠名7天|感谢参与|360积分|感谢参与|520积分|感谢参与\n9折优惠券|感谢参与|再来一次|360积分|感谢参与|冠名14天\n感谢参与|360积分|高亮TAG*2|感谢参与|666积分|感谢参与\n冠名3天|感谢参与|20元店铺余额|感谢参与|10元店铺余额|360积分\n感谢参与|520积分|感谢参与|冠名7天|感谢参与|520积分\n感谢参与|9折优惠券|感谢参与|360积分|感谢参与|冠名7天',
            'grand': '感谢参与|520积分|感谢参与|冠名14天|感谢参与|感谢参与\n85折优惠券|感谢参与|3344积分|感谢参与|520积分|感谢参与\n40元店铺余额|感谢参与|冠名7天|1314积分|感谢参与|520积分\n感谢参与|再来一次|感谢参与|冠名31天|高亮TAG*3|感谢参与\n20元店铺余额|感谢参与|520积分|感谢参与|冠名14天|感谢参与\n感谢参与|1314积分|感谢参与|85折优惠券|感谢参与|20元店铺余额',
        }
        for slug, expected in fixtures.items():
            cells = list(Cell.objects.filter(pool__slug=slug).order_by('row', 'column'))
            self.assertEqual([cell.label for cell in cells], expected.replace('\n', '|').split('|'))
            self.assertEqual(sum(cell.probability for cell in cells), 1_000_000)
            self.assertTrue(all(cell.probability > 0 for cell in cells))
        self.assertLess(Cell.objects.get(pool__slug='grand', row=2, column=3).probability,
                        Cell.objects.get(pool__slug='grand', row=1, column=2).probability)

    def test_seed_is_idempotent_and_preserves_grants_and_disabled_pool(self):
        self.grant(count=3)
        pool = Pool.objects.get(slug='red')
        pool.enabled = False
        pool.save()
        call_command('seed_lottery', stdout=StringIO())
        self.assertEqual(Cell.objects.count(), 108)
        self.assertEqual(Entry.objects.get(participant=self.person).remaining, 3)
        self.assertEqual(EntryGrant.objects.count(), 1)
        self.assertEqual(set(Pool.objects.values_list('version', flat=True)), {1})
        pool.refresh_from_db()
        self.assertFalse(pool.enabled)

    def test_grant_retry_exact_payload_only_and_zero_other_pools(self):
        first = self.grant(count=5)
        second = self.grant(count=5)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Entry.objects.get().remaining, 5)
        self.assertEqual(participant_data(self.person)['entries'], {'small': 5, 'red': 0, 'grand': 0})
        with self.assertRaises(ValidationError):
            self.grant(count=6)
        with self.assertRaises(ValidationError):
            self.grant(pool='red', count=5)
        self.assertEqual(EntryGrant.objects.count(), 1)

    def test_invalid_or_non_superuser_grant_has_no_writes(self):
        ordinary = get_user_model().objects.create_user('ordinary', password='test-secret')
        with self.assertRaises(PermissionDenied):
            grant_entries(self.person, 'small', 1, 'grant-key-1234', 'reason', ordinary)
        for count in [0, -1, True, 1.5, 10001]:
            with self.assertRaises(ValidationError):
                self.grant(count=count)
        self.assertFalse(Entry.objects.exists())
        self.assertFalse(EntryGrant.objects.exists())

    def test_thanks_consumes_one_entry_and_never_creates_award(self):
        self.grant(count=2)
        result = self.pick()
        self.assertEqual(result.result_snapshot['kind'], 'THANKS')
        self.assertEqual(result.entries_remaining, 1)
        self.assertEqual(Entry.objects.get().consumed, 1)
        self.assertFalse(Award.objects.exists())
        self.assertIsNone(draw_data(result)['award'])

    def test_retry_refunds_entry_and_is_idempotent(self):
        self.grant()
        result = self.pick(row=3, column=3)
        self.assertEqual(result.result_snapshot['kind'], 'RETRY')
        self.assertEqual(result.entries_remaining, 1)
        entry = Entry.objects.get()
        self.assertEqual((entry.remaining, entry.granted, entry.consumed), (1, 1, 0))
        self.assertFalse(Award.objects.exists())
        self.assertEqual(draw(self.person, 'small', 'draw-key-0001', 1).pk, result.pk)
        self.assertEqual(Draw.objects.count(), 1)

    def test_reward_records_frozen_points_amount_without_wallet_integration(self):
        self.grant()
        result = self.pick(column=2)
        award = result.award
        self.assertEqual((award.label, award.kind, award.data['point_value']), ('520积分', 'POINTS', 520))
        self.assertEqual(award.status, 'PENDING')
        self.assertEqual(result.entries_remaining, 0)
        self.assertEqual(Entry.objects.get().consumed, 1)
        self.assertEqual(len(result.probability_snapshot['cells']), 36)
        self.assertEqual(result.probability_snapshot['algorithm'], 'independent-fair-v4')
        self.assertEqual(result.random_ticket, result.probability_snapshot['row_ticket'])
        self.assertEqual(result.second_random_ticket, result.probability_snapshot['column_ticket'])

    def test_balance_tag_naming_coupon_keep_original_values_and_quantities(self):
        self.grant(pool='grand', count=4)
        checks = [(3, 1, 'BALANCE', 'balance_amount', 4000),
                  (4, 5, 'TAG', 'quantity', 3), (4, 4, 'NAMING', 'duration_days', 31),
                  (2, 1, 'COUPON', 'coupon_rate', 85)]
        for number, (row, column, kind, field, value) in enumerate(checks):
            award = self.pick('grand', row, column, f'draw-value-{number:04}').award
            self.assertEqual(award.kind, kind)
            self.assertEqual(award.data[field], value)
            self.assertEqual(award.status, 'PENDING')
        self.assertEqual(Award.objects.count(), 4)
        self.assertEqual(Entry.objects.get().remaining, 0)

    def test_ticket_upper_boundary_moves_to_next_cell_and_last_ticket_valid(self):
        self.grant(count=2)
        boundary = self.ticket('small', 1, 2)
        with patch('lottery.dice.secrets.randbelow', side_effect=boundary):
            result = draw(self.person, 'small', 'draw-boundary-01', 1)
        self.assertEqual((result.cell.row, result.cell.column), (1, 2))
        with patch('lottery.dice.secrets.randbelow', side_effect=[5, 5]):
            result = draw(self.person, 'small', 'draw-boundary-02', 1)
        self.assertEqual((result.cell.row, result.cell.column), (6, 6))

    def test_replay_after_close_pause_and_configuration_change_returns_original(self):
        self.grant()
        result = self.pick(column=2)
        snapshot = result.result_snapshot.copy()
        Pool.objects.filter(slug='small').update(version=2, enabled=False, name='changed')
        Cell.objects.filter(pk=result.cell_id).update(label='changed', probability=123)
        Room.objects.filter(pk=self.room.pk).update(ends_at=timezone.now() - timedelta(hours=1))
        with patch('lottery.engine.secrets.randbelow', side_effect=AssertionError('must not reroll')):
            replay = draw(self.person, 'small', 'draw-key-0001', 1)
        self.assertEqual(replay.pk, result.pk)
        self.assertEqual(replay.result_snapshot, snapshot)
        self.assertEqual(Award.objects.count(), 1)
        self.assertEqual(Entry.objects.get().remaining, 0)

    def test_same_key_different_pool_or_version_rejects(self):
        self.grant()
        self.pick()
        with self.assertRaises(ValidationError):
            draw(self.person, 'red', 'draw-key-0001', 1)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'draw-key-0001', 2)
        self.assertEqual(Draw.objects.count(), 1)

    def test_code_reset_between_authentication_and_draw_rejects_stale_session(self):
        self.grant()
        Participant.objects.filter(pk=self.person.pk).update(code_digest='f' * 64)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'draw-stale-auth1', 1)
        self.assertEqual(Entry.objects.get().remaining, 1)
        self.assertFalse(Draw.objects.exists())

    def test_no_entry_or_stale_version_never_draws(self):
        with patch('lottery.engine.secrets.randbelow') as random:
            with self.assertRaises(ValidationError):
                draw(self.person, 'small', 'draw-noentry-01', 1)
            self.grant()
            with self.assertRaises(ValidationError):
                draw(self.person, 'small', 'draw-version-01', 2)
        random.assert_not_called()
        self.assertEqual(Entry.objects.get().remaining, 1)
        self.assertFalse(Draw.objects.exists())

    def test_start_inclusive_end_exclusive_and_disabled_participant(self):
        self.grant(count=3)
        with patch('lottery.engine.timezone.now', return_value=self.room.starts_at):
            self.pick(key='draw-start-0001')
        with patch('lottery.engine.timezone.now', return_value=self.room.ends_at):
            with self.assertRaises(ValidationError):
                draw(self.person, 'small', 'draw-end-00001', 1)
        Participant.objects.filter(pk=self.person.pk).update(enabled=False)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'draw-paused-001', 1)
        self.assertEqual(Draw.objects.count(), 1)

    def test_failed_award_write_rolls_back_entry_and_result(self):
        self.grant()
        with patch('lottery.engine.Award.objects.create', side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):
                self.pick(column=2)
        self.assertEqual(Entry.objects.get().remaining, 1)
        self.assertFalse(Draw.objects.exists())
        self.assertFalse(Award.objects.exists())

    def test_legacy_joint_weight_is_unused_and_changed_prize_fails_without_consumption(self):
        self.grant(count=2)
        first = Cell.objects.get(pool__slug='small', row=1, column=1)
        first.probability += 1
        first.save()
        self.pick(key='draw-legacy-001')
        first.probability -= 1
        first.save()
        Cell.objects.filter(pool__slug='small', row=1, column=2).update(reward_data={'kind': 'POINTS', 'point_value': 999999})
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'draw-bad-prize1', 1)
        self.assertEqual(Draw.objects.count(), 1)
        self.assertEqual(Entry.objects.get().remaining, 1)

    def test_seed_refuses_to_overwrite_drawn_pool(self):
        self.grant()
        result = self.pick()
        original = result.result_snapshot.copy()
        Cell.objects.filter(pk=result.cell_id).update(label='错误改奖')
        with self.assertRaises(ValidationError):
            seed_catalog()
        result.refresh_from_db()
        self.assertEqual(result.result_snapshot, original)
        self.assertEqual(Entry.objects.get().remaining, 0)

    def test_history_is_participant_scoped(self):
        self.grant()
        self.pick()
        other = Participant.objects.create(room=self.room, nickname='老板乙', code_digest='b' * 64)
        grant_entries(other, 'grand', 2, 'grant-other-001', '活动赠送', self.admin)
        data = participant_data(other)
        self.assertEqual(data['draws'], [])
        self.assertEqual(data['entries'], {'small': 0, 'red': 0, 'grand': 2})

    def test_award_fulfillment_requires_superadmin_and_records_delivery_once(self):
        self.grant()
        result = self.pick(column=2)
        ordinary = get_user_model().objects.create_user('helper')
        with self.assertRaises(PermissionDenied):
            fulfill_award(result.award.pk, ordinary, '已发放')
        with self.assertRaises(ValidationError):
            fulfill_award(result.award.pk, self.admin, '')
        first = fulfill_award(result.award.pk, self.admin, '已为老板发放520积分')
        second = fulfill_award(result.award.pk, self.admin, '重复请求')
        self.assertEqual(first.fulfilled_at, second.fulfilled_at)
        self.assertEqual(second.delivery_note, '已为老板发放520积分')
        self.assertEqual(second.fulfilled_by_id, self.admin.pk)

    def test_persisted_minute_limit_blocks_retry_loops_but_replay_survives(self):
        self.grant()
        for number in range(60):
            self.pick(row=3, column=3, key=f'draw-rate-{number:04}')
        with self.assertRaises(ValidationError):
            self.pick(row=3, column=3, key='draw-rate-0060')
        replay = draw(self.person, 'small', 'draw-rate-0000', 1)
        self.assertEqual(replay.pk, Draw.objects.order_by('id').first().pk)
        self.assertEqual(Entry.objects.get().remaining, 1)
        self.assertEqual(Draw.objects.count(), 60)


class LotteryApiTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()
        self.client = APIClient()

    def login(self):
        response = self.client.post('/api/login/', {'room': self.room.code, 'code': self.raw_code}, format='json')
        self.assertEqual(response.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + response.data['data']['token'],
                                HTTP_X_LOTTERY_ROOM=self.room.code)

    def test_public_catalog_shows_108_prizes_without_internal_odds(self):
        response = self.client.get('/api/catalog/', {'room': self.room.code})
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(sum(len(pool['cells']) for pool in data['campaigns']), 108)
        for word in ['probability', 'probabilities', 'weight', '600000', '370000', '60%']:
            self.assertNotIn(word, response.content.decode())

    def test_draw_and_history_require_valid_room_token(self):
        self.assertEqual(self.client.get('/api/me/').status_code, 401)
        self.assertEqual(self.client.post('/api/draw/', {}, format='json').status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer bad-token')
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_wrong_room_code_cannot_login_and_rotated_code_revokes_session(self):
        response = self.client.post('/api/login/', {'room': 'another-room', 'code': self.raw_code}, format='json')
        self.assertEqual(response.status_code, 400)
        self.login()
        self.assertEqual(self.client.get('/api/me/').status_code, 200)
        Participant.objects.filter(pk=self.person.pk).update(code_digest='c' * 64)
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_draw_uses_token_owner_and_returns_pending_award(self):
        self.grant()
        other = Participant.objects.create(room=self.room, nickname='老板乙', code_digest='a' * 64)
        self.login()
        with patch('lottery.dice.secrets.randbelow', side_effect=self.ticket('small', 1, 2)):
            response = self.client.post('/api/draw/', {'campaign': 'small', 'version': 1,
                'client_request_id': 'api-draw-key-01', 'participant_id': other.pk}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Draw.objects.get().participant_id, self.person.pk)
        self.assertEqual(response.data['data']['award']['status'], 'PENDING')
        for word in ['probability', 'probabilities', 'weight']:
            self.assertNotIn(word, response.content.decode())
        self.assertEqual(self.client.get('/api/me/').data['data']['entries']['small'], 0)
        for word in ['probability', 'probabilities', 'weight']:
            self.assertNotIn(word, self.client.get('/api/me/').content.decode())

    def test_superadmin_controls_are_not_open_to_participant(self):
        self.login()
        response = self.client.get('/control/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)


class LotteryControlReplayTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()
        self.client.force_login(self.admin)

    def test_new_participant_code_is_response_only_and_duplicate_post_has_no_new_grant(self):
        payload = {'action': 'add_participant', 'room': self.room.code,
                   'request_id': 'create-participant-01', 'nickname': '新增老板',
                   'small': 2, 'red': 1, 'grand': 0}
        response = self.client.post('/control/', payload)
        self.assertEqual(response.status_code, 200)
        issued = response.context['issued']
        person = Participant.objects.get(nickname='新增老板')
        self.assertEqual(person.code_digest, hashlib.sha256(issued['code'].encode()).hexdigest())
        self.assertNotIn('lottery_issued', self.client.session)
        self.assertNotIn(issued['code'], str(dict(self.client.session)))
        response = self.client.post('/control/', payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Participant.objects.filter(nickname='新增老板').count(), 1)
        self.assertEqual(Entry.objects.get(participant=person, pool__slug='small').remaining, 2)
        self.assertEqual(EntryGrant.objects.filter(entry__participant=person).count(), 2)
        person.refresh_from_db()
        self.assertEqual(person.code_digest, hashlib.sha256(issued['code'].encode()).hexdigest())

    def test_participant_creation_key_cannot_replay_with_changed_contents(self):
        payload = {'action': 'add_participant', 'room': self.room.code,
                   'request_id': 'create-participant-02', 'nickname': '新增老板',
                   'small': 2, 'red': 0, 'grand': 0}
        self.client.post('/control/', payload)
        response = self.client.post('/control/', {**payload, 'small': 9})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Participant.objects.filter(nickname='新增老板').count(), 1)
        self.assertEqual(Entry.objects.get(participant__nickname='新增老板').remaining, 2)
        self.assertEqual(EntryGrant.objects.count(), 1)

    def test_reset_post_replay_does_not_rotate_secret_twice_or_store_raw_in_session(self):
        payload = {'action': 'reset_code', 'room': self.room.code,
                   'participant': self.person.pk, 'request_id': 'reset-participant-01'}
        response = self.client.post('/control/', payload)
        self.assertEqual(response.status_code, 200)
        issued = response.context['issued']
        self.person.refresh_from_db()
        first_digest = self.person.code_digest
        self.assertEqual(first_digest, hashlib.sha256(issued['code'].encode()).hexdigest())
        self.assertNotIn('lottery_issued', self.client.session)
        self.assertEqual(self.client.post('/control/', payload).status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.code_digest, first_digest)


class RepeatingPityTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()

    def test_150_actual_rolls_repeat_per_pool_and_award_both_thresholds(self):
        self.grant(count=150)
        base_time = timezone.now()
        results = {}
        with patch('lottery.dice.secrets.randbelow', return_value=0) as secure:
            for number in range(1, 151):
                with patch('lottery.engine.timezone.now', return_value=base_time + timedelta(seconds=number * 6)):
                    result = draw(self.person, 'small', f'pity-actual-roll-{number:03}', 1)
                    results[number] = result
            self.assertEqual(secure.call_count, 300)
        entry = Entry.objects.get(participant=self.person, pool__slug='small')
        self.assertEqual((entry.total_rolls, entry.remaining, entry.consumed), (150, 0, 150))
        self.assertFalse(Award.objects.exists())
        self.assertEqual(BonusAward.objects.count(), 8)
        for threshold, count, amount in [(30, 5, 500), (50, 3, 1000)]:
            bonuses = list(BonusAward.objects.filter(threshold=threshold))
            self.assertEqual(len(bonuses), count)
            self.assertTrue(all(bonus.data['balance_amount'] == amount and bonus.status == 'PENDING' for bonus in bonuses))
        self.assertEqual(set(results[150].bonuses.values_list('threshold', flat=True)), {30, 50})
        self.assertEqual(results[29].bonuses.count(), 0)
        self.assertEqual(results[30].bonuses.get().data, {'balance_amount': 500, 'threshold': 30, 'roll_number': 30})
        self.assertEqual(results[50].bonuses.get().data['balance_amount'], 1000)
        self.assertEqual(results[60].bonuses.get().data['balance_amount'], 500)
        self.assertEqual(results[100].bonuses.get().data['balance_amount'], 1000)
        with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('replay must not sample')):
            replay = draw(self.person, 'small', 'pity-actual-roll-150', 1)
        self.assertEqual(replay.pk, results[150].pk)
        self.assertEqual(replay.bonuses.count(), 2)
        self.assertEqual(BonusAward.objects.count(), 8)
        data = participant_data(self.person)
        self.assertEqual(data['pity']['small'], {'total_rolls': 150, 'until_lower': 30, 'until_upper': 50,
                                                'lower_yuan': 5, 'upper_yuan': 10})
        self.assertEqual(data['pity']['red'], {'total_rolls': 0, 'until_lower': 30, 'until_upper': 50,
                                              'lower_yuan': 10, 'upper_yuan': 20})
        self.assertEqual(data['draws'][0]['total_rolls'], 150)
        self.assertEqual(len(data['draws'][0]['bonuses']), 2)

    def test_retry_counts_toward_bonus_without_consuming_entry_and_new_grant_does_not_reset(self):
        self.grant()
        Entry.objects.filter(participant=self.person).update(total_rolls=29)
        result = self.pick(row=3, column=3)
        entry = Entry.objects.get()
        self.assertEqual((entry.total_rolls, entry.remaining, entry.consumed), (30, 1, 0))
        self.assertEqual(result.result_snapshot['kind'], 'RETRY')
        self.assertEqual(result.bonuses.get().data['balance_amount'], 500)
        self.grant(count=3, key='pity-followup-grant')
        entry.refresh_from_db()
        self.assertEqual((entry.total_rolls, entry.remaining), (30, 4))
        self.assertEqual(draw_data(result)['pity'], {'total_rolls': 30, 'until_lower': 30, 'until_upper': 20,
                                                    'lower_yuan': 5, 'upper_yuan': 10})

    def test_normal_prize_does_not_reset_pity_and_bonus_failure_rolls_back_all(self):
        self.grant(count=2)
        Entry.objects.filter(participant=self.person).update(total_rolls=28)
        first = self.pick(column=2, key='pity-normal-prize')
        self.assertEqual(first.award.kind, 'POINTS')
        self.assertEqual(first.roll_number, 29)
        self.assertEqual(first.bonuses.count(), 0)
        with patch('lottery.engine.BonusAward.objects.create', side_effect=RuntimeError('bonus unavailable')):
            with self.assertRaises(RuntimeError):
                self.pick(column=2, key='pity-rollback-001')
        entry = Entry.objects.get()
        self.assertEqual((entry.total_rolls, entry.remaining), (29, 1))
        self.assertEqual(Draw.objects.count(), 1)
        self.assertEqual(Award.objects.count(), 1)
        self.assertEqual(BonusAward.objects.count(), 0)
        second = self.pick(column=2, key='pity-rollback-001')
        self.assertEqual(second.roll_number, 30)
        self.assertEqual(second.bonuses.count(), 1)
        self.assertEqual(Award.objects.count(), 2)

    def test_per_pool_history_is_independent_and_failed_roll_does_not_advance(self):
        self.grant()
        self.grant(pool='red', key='pity-red-initial')
        Entry.objects.filter(participant=self.person, pool__slug='small').update(total_rolls=49)
        result = self.pick()
        self.assertEqual(result.bonuses.get().threshold, 50)
        self.assertEqual(result.bonuses.get().data['balance_amount'], 1000)
        other = self.pick(pool='red', row=1, column=1, key='pity-red-roll-01')
        self.assertEqual(other.roll_number, 1)
        self.assertEqual(other.bonuses.count(), 0)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'pity-noentry-001', 1)
        self.assertEqual(Entry.objects.get(participant=self.person, pool__slug='small').total_rolls, 50)

    def test_higher_pools_guarantee_their_two_largest_balance_rewards(self):
        for pool, lower, upper in [('red', 1000, 2000), ('grand', 2000, 4000)]:
            self.grant(pool=pool, count=2, key=f'tier-grant-{pool}')
            Entry.objects.filter(participant=self.person, pool__slug=pool).update(total_rolls=29)
            first = self.pick(pool=pool, key=f'tier-lower-{pool}')
            self.assertEqual(first.bonuses.get().data['balance_amount'], lower)
            self.assertEqual(first.bonuses.get().threshold, 30)
            Entry.objects.filter(participant=self.person, pool__slug=pool).update(total_rolls=49)
            second = self.pick(pool=pool, key=f'tier-upper-{pool}')
            self.assertEqual(second.bonuses.get().data['balance_amount'], upper)
            self.assertEqual(second.bonuses.get().threshold, 50)
            self.assertEqual(draw_data(second)['pity'], {'total_rolls': 50, 'until_lower': 10, 'until_upper': 50,
                                                        'lower_yuan': lower // 100, 'upper_yuan': upper // 100})
        configurations = {pool['slug']: pool['pity_rewards'] for pool in catalog_data()['campaigns']}
        self.assertEqual(configurations['small'], {'lower_yuan': 5, 'upper_yuan': 10, 'lower_interval': 30, 'upper_interval': 50})
        self.assertEqual(configurations['red']['lower_yuan'], 10)
        self.assertEqual(configurations['grand']['upper_yuan'], 40)


class FairDiceEngineTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()

    def test_old_dry_streak_and_history_do_not_affect_fair_draws(self):
        self.grant(count=4)
        Entry.objects.filter(participant=self.person).update(ordinary_dry_streak=40)
        results = []
        for number, (row, column) in enumerate([(1, 1), (1, 2), (2, 6), (3, 3)]):
            results.append(self.pick(row=row, column=column, key=f'fair-history-{number}'))
        entry = Entry.objects.get()
        self.assertEqual((entry.total_rolls, entry.ordinary_dry_streak), (4, 40))
        for result in results:
            snapshot = result.probability_snapshot
            self.assertEqual(snapshot['algorithm'], 'independent-fair-v4')
            self.assertEqual(snapshot['denominator'], 6)
            self.assertEqual(snapshot['joint_denominator'], 36)
            self.assertEqual(snapshot['row_weights'], [1] * 6)
            self.assertEqual(snapshot['column_weights'], [1] * 6)
            self.assertEqual([cell['probability'] for cell in snapshot['cells']], [1] * 36)
            self.assertNotIn('phase', snapshot)
            self.assertNotIn('ordinary_dry_streak', snapshot)
        self.assertFalse(BonusAward.objects.exists())

    def test_old_private_snapshot_replay_remains_unchanged(self):
        self.grant()
        result = self.pick(key='fair-legacy-replay')
        legacy = {'algorithm': 'independent-v3-soft', 'phase': 83,
                  'row_weights': [1, 2, 3, 4, 5, 6], 'ordinary_dry_streak': 7}
        Draw.objects.filter(pk=result.pk).update(probability_snapshot=legacy)
        with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not reroll')):
            replay = draw(self.person, 'small', 'fair-legacy-replay', 1)
        self.assertEqual(replay.pk, result.pk)
        self.assertEqual(replay.probability_snapshot, legacy)
        self.assertEqual(Entry.objects.get().total_rolls, 1)

    def test_owner_history_only_includes_current_room_and_replay_rejects_old_room(self):
        self.grant()
        result = self.pick(key='fair-owner-room-01')
        data = draw_data(result)
        self.assertEqual(data['participant_id'], self.person.pk)
        self.assertEqual(data['account_id'], self.person.pk)
        self.assertEqual(data['room_code'], self.room.code)
        self.assertEqual(data['room_id'], str(self.room.pk))
        other_room = Room.objects.create(code='other-private-room', name='其他房间', created_by=self.admin,
            starts_at=self.room.starts_at, ends_at=self.room.ends_at)
        Participant.objects.filter(pk=self.person.pk).update(room=other_room)
        self.person.refresh_from_db()
        self.assertEqual(participant_data(self.person)['draws'], [])
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'fair-owner-room-01', 1)
        self.assertEqual(Draw.objects.count(), 1)

    def test_150th_roll_in_higher_pools_awards_both_correct_cash_tiers(self):
        for pool, amounts in [('red', [1000, 2000]), ('grand', [2000, 4000])]:
            self.grant(pool=pool, key=f'fair-150-grant-{pool}')
            Entry.objects.filter(participant=self.person, pool__slug=pool).update(total_rolls=149)
            result = self.pick(pool=pool, key=f'fair-150-draw-{pool}')
            self.assertEqual(sorted(result.bonuses.values_list('data__balance_amount', flat=True)), amounts)
            self.assertEqual(set(result.bonuses.values_list('threshold', flat=True)), {30, 50})
            self.assertEqual(result.roll_number, 150)


class IndependentDiceMigrationTests(TransactionTestCase):
    def test_existing_rolls_backfill_and_old_committed_replay_snapshot_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('lottery', '0002_participant_operation_keys')])
        old_apps = executor.loader.project_state([('lottery', '0002_participant_operation_keys')]).apps
        User = old_apps.get_model('auth', 'User')
        OldPool = old_apps.get_model('lottery', 'Pool')
        OldRoom = old_apps.get_model('lottery', 'Room')
        OldParticipant = old_apps.get_model('lottery', 'Participant')
        OldEntry = old_apps.get_model('lottery', 'Entry')
        OldCell = old_apps.get_model('lottery', 'Cell')
        OldDraw = old_apps.get_model('lottery', 'Draw')
        operator = User.objects.create(username='migration-operator', is_superuser=True, password='legacy-password-hash-preserve')
        pool = OldPool.objects.create(slug='small', name='旧奖池', version=7)
        room = OldRoom.objects.create(code='migration-room', name='迁移房间', created_by_id=operator.pk,
            starts_at=timezone.now() - timedelta(days=1), ends_at=timezone.now() + timedelta(days=1))
        person = OldParticipant.objects.create(room=room, nickname='迁移老板', code_digest='m' * 64)
        entry = OldEntry.objects.create(participant=person, pool=pool, remaining=4, granted=5, consumed=1)
        cell = OldCell.objects.create(pool=pool, row=1, column=1, label='感谢参与', kind='THANKS', probability=1)
        for number in range(2):
            OldDraw.objects.create(participant=person, room=room, pool=pool, cell=cell,
                event_key=f'migration-old-{number}', request_fingerprint='f' * 64,
                campaign_version=7, random_ticket=123,
                result_snapshot={'kind': 'THANKS', 'legacy': number}, probability_snapshot=[{'legacy': True}],
                entries_remaining=4)
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('lottery', '0003_independent_dice_pity')])
            stage_apps = executor.loader.project_state([('lottery', '0003_independent_dice_pity')]).apps
            StagePool = stage_apps.get_model('lottery', 'Pool')
            StageEntry = stage_apps.get_model('lottery', 'Entry')
            StageDraw = stage_apps.get_model('lottery', 'Draw')
            self.assertEqual(StagePool.objects.get(pk=pool.pk).version, 8)
            current = StageEntry.objects.get(pk=entry.pk)
            self.assertEqual((current.total_rolls, current.remaining, current.granted, current.consumed), (2, 4, 5, 1))
            results = list(StageDraw.objects.order_by('id'))
            self.assertEqual([row.roll_number for row in results], [1, 2])
            self.assertEqual([row.result_snapshot for row in results], [{'kind': 'THANKS', 'legacy': 0}, {'kind': 'THANKS', 'legacy': 1}])
            self.assertTrue(all(row.random_ticket == 123 and row.second_random_ticket is None for row in results))
            executor = MigrationExecutor(connection)
            executor.migrate([('lottery', '0003_independent_dice_pity')])
            self.assertEqual(StagePool.objects.get(pk=pool.pk).version, 8)
            executor = MigrationExecutor(connection)
            executor.migrate([('lottery', '0004_ordinary_soft_pity')])
            self.assertEqual(Pool.objects.get(pk=pool.pk).version, 9)
            self.assertEqual(Entry.objects.get(pk=entry.pk).ordinary_dry_streak, 2)
            self.assertEqual(Entry.objects.get(pk=entry.pk).total_rolls, 2)
            self.assertEqual(StageDraw.objects.order_by('id').first().result_snapshot, {'kind': 'THANKS', 'legacy': 0})
            MigrationExecutor(connection).migrate([('lottery', '0004_ordinary_soft_pity')])
            self.assertEqual(Pool.objects.get(pk=pool.pk).version, 9)
            MigrationExecutor(connection).migrate([('lottery', '0005_fair_dice')])
            self.assertEqual(Pool.objects.get(pk=pool.pk).version, 10)
            self.assertEqual(Entry.objects.get(pk=entry.pk).ordinary_dry_streak, 2)
            self.assertEqual(Entry.objects.get(pk=entry.pk).total_rolls, 2)
            self.assertEqual(StageDraw.objects.order_by('id').first().result_snapshot, {'kind': 'THANKS', 'legacy': 0})
            old_draw_fields = [field.attname for field in StageDraw._meta.fields]
            old_draw_rows = list(StageDraw.objects.order_by('id').values(*old_draw_fields))
            old_entry_rows = list(Entry.objects.order_by('id').values())
            old_pool_rows = list(Pool.objects.order_by('id').values())
            old_user_rows = list(User.objects.order_by('id').values())
            old_participant_rows = list(OldParticipant.objects.order_by('id').values())
            MigrationExecutor(connection).migrate([('lottery', '0006_draw_batches')])
            self.assertEqual(Pool.objects.get(pk=pool.pk).version, 10)
            self.assertEqual(list(Draw.objects.order_by('id').values(*old_draw_fields)), old_draw_rows)
            self.assertEqual(list(Entry.objects.order_by('id').values()), old_entry_rows)
            self.assertEqual(list(Pool.objects.order_by('id').values()), old_pool_rows)
            self.assertEqual(list(User.objects.order_by('id').values()), old_user_rows)
            self.assertEqual(list(OldParticipant.objects.order_by('id').values()), old_participant_rows)
            self.assertFalse(Draw.objects.exclude(batch_id=None, batch_index=None).exists())
        finally:
            MigrationExecutor(connection).migrate([('lottery', '0007_targeted_prize_reduction')])


@skipUnlessDBFeature('has_select_for_update')
class LotteryConcurrencyTests(Fixtures, TransactionTestCase):
    """Run on isolated MySQL to exercise real row locks and idempotency."""
    reset_sequences = True

    def setUp(self):
        self.setup_lottery()

    def race(self, operation):
        barrier = Barrier(2)

        def worker(number):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                return operation(number)
            except ValidationError:
                return None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(worker, range(2)))

    def test_two_distinct_requests_cannot_spend_single_entry_twice(self):
        self.grant()
        with patch('lottery.engine.secrets.randbelow', return_value=0):
            results = self.race(lambda number: draw(self.person, 'small', f'race-distinct-{number}', 1).pk)
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(Draw.objects.count(), 1)
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().consumed), (0, 1))

    def test_same_request_returns_same_draw_and_award_once(self):
        self.grant()
        with patch('lottery.dice.secrets.randbelow', side_effect=self.ticket('small', 1, 2)):
            results = self.race(lambda number: draw(self.person, 'small', 'same-race-draw1', 1).pk)
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0], results[1])
        self.assertEqual(Draw.objects.count(), 1)
        self.assertEqual(Award.objects.count(), 1)
        self.assertEqual(Entry.objects.get().remaining, 0)

    def test_same_grant_request_only_adds_once(self):
        results = self.race(lambda number: grant_entries(self.person, 'small', 5,
            'same-race-grant', '活动发放', self.admin).pk)
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0], results[1])
        self.assertEqual(EntryGrant.objects.count(), 1)
        self.assertEqual(Entry.objects.get().remaining, 5)

    def test_grant_and_draw_preserve_entry_totals(self):
        self.grant()
        with patch('lottery.engine.secrets.randbelow', return_value=0):
            self.race(lambda number: draw(self.person, 'small', 'race-with-grant', 1).pk if number == 0
                else grant_entries(self.person, 'small', 3, 'race-add-grant', '追加发放', self.admin).pk)
        entry = Entry.objects.get()
        self.assertEqual((entry.remaining, entry.granted, entry.consumed), (3, 4, 1))

    def test_same_150th_roll_creates_two_bonuses_once(self):
        self.grant()
        Entry.objects.filter(participant=self.person).update(total_rolls=149)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            results = self.race(lambda number: draw(self.person, 'small', 'same-race-pity150', 1).pk)
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0], results[1])
        self.assertEqual(Draw.objects.count(), 1)
        self.assertEqual(BonusAward.objects.count(), 2)
        self.assertEqual(set(BonusAward.objects.values_list('threshold', flat=True)), {30, 50})
        self.assertEqual(Entry.objects.get().total_rolls, 150)
