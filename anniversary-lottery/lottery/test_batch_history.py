from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import call, patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from .engine import batch_data, draw, draw_batch, grant_entries, history_data, participant_data
from .models import Award, BonusAward, Draw, DrawBatch, Entry, Participant, Pool, Room
from .tests import Fixtures


class BatchEngineTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()

    def batch(self, key='batch-request-01', pool='small', tickets=None):
        with patch('lottery.dice.secrets.randbelow', side_effect=tickets or [0, 0] * 10) as random:
            result = draw_batch(self.person, pool, key, 1)
        self.assertEqual(random.call_args_list, [call(6)] * 20)
        return result

    def test_ten_fair_rolls_are_ordered_and_have_individual_permanent_records(self):
        self.grant(count=12)
        result = self.batch(tickets=[0, 0] * 4 + [2, 2] * 2 + [0, 0] * 4)
        data = batch_data(result)
        self.assertEqual(data['count'], 10)
        self.assertEqual((data['entries_consumed'], data['entries_remaining'], data['total_rolls']), (8, 4, 10))
        self.assertEqual([row['batch_index'] for row in data['draws']], list(range(1, 11)))
        self.assertEqual([row['total_rolls'] for row in data['draws']], list(range(1, 11)))
        self.assertEqual(sum(row['kind'] == 'RETRY' for row in data['draws']), 2)
        self.assertTrue(all(row['batch_id'] == result.pk and row['participant_id'] == self.person.pk
                            and row['room_code'] == self.room.code for row in data['draws']))
        self.assertEqual(Draw.objects.count(), 10)
        self.assertEqual(DrawBatch.objects.count(), 1)
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().consumed), (4, 8))
        self.assertTrue(all(row.probability_snapshot['algorithm'] == 'independent-fair-v4'
                            for row in result.draws.all()))

    def test_nine_entries_cannot_start_even_if_every_roll_would_retry(self):
        self.grant(count=9)
        with patch('lottery.dice.secrets.randbelow') as random:
            with self.assertRaises(ValidationError):
                draw_batch(self.person, 'small', 'batch-not-enough', 1)
        random.assert_not_called()
        self.assertFalse(DrawBatch.objects.exists())
        self.assertFalse(Draw.objects.exists())
        self.assertEqual(Entry.objects.get().remaining, 9)

    def test_all_retries_restore_all_ten_entries_but_count_ten_real_rolls(self):
        self.grant(count=10)
        result = self.batch(tickets=[2, 2] * 10)
        self.assertEqual((result.entries_consumed, result.entries_remaining, result.total_rolls), (0, 10, 10))
        self.assertEqual(Entry.objects.get().consumed, 0)
        self.assertFalse(Award.objects.exists())

    def test_replay_survives_expiry_pause_zero_entries_and_config_change(self):
        self.grant(count=10)
        first = self.batch()
        ids = list(first.draws.order_by('batch_index').values_list('id', flat=True))
        Pool.objects.filter(slug='small').update(enabled=False, version=9)
        Room.objects.filter(pk=self.room.pk).update(enabled=False)
        with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not reroll')):
            replay = draw_batch(self.person, 'small', 'batch-request-01', 1)
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(list(replay.draws.order_by('batch_index').values_list('id', flat=True)), ids)
        self.assertEqual(batch_data(replay)['entries_remaining'], 0)
        self.assertEqual(Entry.objects.get().total_rolls, 10)

    def test_same_key_cannot_change_pool_version_or_single_batch_request_type(self):
        self.grant(count=20)
        self.batch()
        with self.assertRaises(ValidationError):
            draw_batch(self.person, 'red', 'batch-request-01', 1)
        with self.assertRaises(ValidationError):
            draw_batch(self.person, 'small', 'batch-request-01', 2)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'batch-request-01', 1)
        self.pick(key='single-before-batch')
        with self.assertRaises(ValidationError):
            draw_batch(self.person, 'small', 'single-before-batch', 1)
        self.assertEqual(Draw.objects.count(), 11)

    def test_failed_seventh_award_rolls_back_entire_batch_then_same_key_can_retry(self):
        self.grant(count=10)
        original_create = Award.objects.create
        calls = 0

        def fail_seventh(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 7:
                raise RuntimeError('award write failed')
            return original_create(**kwargs)

        with patch('lottery.dice.secrets.randbelow', side_effect=[0, 1] * 10), patch('lottery.engine.Award.objects.create', side_effect=fail_seventh):
            with self.assertRaises(RuntimeError):
                draw_batch(self.person, 'small', 'batch-atomic-award', 1)
        self.assertFalse(Draw.objects.exists())
        self.assertFalse(DrawBatch.objects.exists())
        self.assertFalse(Award.objects.exists())
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().total_rolls), (10, 0))
        self.batch(key='batch-atomic-award', tickets=[0, 1] * 10)
        self.assertEqual(Award.objects.count(), 10)

    def test_bonus_failure_at_150_rolls_back_all_rolls_and_both_guarantees(self):
        self.grant(count=10)
        Entry.objects.filter(participant=self.person).update(total_rolls=145)
        original_create = BonusAward.objects.create

        def fail_second(**kwargs):
            if kwargs['threshold'] == 50:
                raise RuntimeError('bonus write failed')
            return original_create(**kwargs)

        with patch('lottery.dice.secrets.randbelow', return_value=0), patch('lottery.engine.BonusAward.objects.create', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                draw_batch(self.person, 'small', 'batch-atomic-bonus', 1)
        self.assertFalse(Draw.objects.exists())
        self.assertFalse(DrawBatch.objects.exists())
        self.assertFalse(BonusAward.objects.exists())
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().total_rolls), (10, 145))
        result = self.batch(key='batch-atomic-bonus')
        self.assertEqual(result.total_rolls, 155)
        self.assertEqual(sorted(bonus['data']['balance_amount'] for bonus in batch_data(result)['bonuses']), [500, 1000])

    def test_batch_crosses_both_150_guarantees_for_all_three_cash_tiers(self):
        for slug, amounts in [('small', [500, 1000]), ('red', [1000, 2000]), ('grand', [2000, 4000])]:
            self.grant(pool=slug, count=10, key=f'batch-tier-grant-{slug}')
            Entry.objects.filter(participant=self.person, pool__slug=slug).update(total_rolls=145)
            result = self.batch(pool=slug, key=f'batch-tier-roll-{slug}')
            data = batch_data(result)
            self.assertEqual(sorted(row['data']['balance_amount'] for row in data['bonuses']), amounts)
            self.assertEqual({row['batch_index'] for row in data['bonuses']}, {5})
            self.assertEqual(result.total_rolls, 155)

    def test_minute_limit_counts_rolls_reserves_whole_batch_and_replay_is_exempt(self):
        self.grant(count=10)
        for number in range(6):
            self.batch(key=f'batch-rate-{number:04}', tickets=[2, 2] * 10)
        with self.assertRaises(ValidationError):
            draw_batch(self.person, 'small', 'batch-rate-0006', 1)
        with self.assertRaises(ValidationError):
            draw(self.person, 'small', 'single-over-rate', 1)
        self.assertEqual(draw_batch(self.person, 'small', 'batch-rate-0000', 1).count, 10)
        self.assertEqual(Draw.objects.count(), 60)
        self.assertEqual(Entry.objects.get().remaining, 10)

    def test_daily_limit_reserves_all_ten_before_any_random_sampling(self):
        self.grant(count=20)
        self.batch()
        with patch('lottery.engine.ROLLS_PER_DAY', 15), patch('lottery.dice.secrets.randbelow') as random:
            with self.assertRaises(ValidationError):
                draw_batch(self.person, 'small', 'batch-over-daily', 1)
        random.assert_not_called()
        self.assertEqual(Draw.objects.count(), 10)


class HistoryAndBatchApiTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()
        self.api = APIClient()

    def login(self):
        result = self.api.post('/api/login/', {'room': self.room.code, 'code': self.raw_code}, format='json')
        self.assertEqual(result.status_code, 200)
        token = result.data['data']['token']
        self.api.credentials(HTTP_AUTHORIZATION='Bearer ' + token, HTTP_X_LOTTERY_ROOM=self.room.code)
        return token

    def history_fixture(self):
        self.grant(count=55)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            for number in range(5):
                draw_batch(self.person, 'small', f'history-batch-{number:04}', 1)

    def test_new_endpoints_require_owner_token_and_matching_room_header(self):
        self.assertEqual(self.api.get('/api/history/').status_code, 401)
        self.assertEqual(self.api.post('/api/draw-batch/', {}, format='json').status_code, 401)
        token = self.login()
        self.api.credentials(HTTP_AUTHORIZATION='Bearer ' + token, HTTP_X_LOTTERY_ROOM='wrong-room')
        self.assertEqual(self.api.get('/api/history/').status_code, 403)
        self.assertEqual(self.api.post('/api/draw-batch/', {}, format='json').status_code, 403)
        self.assertFalse(DrawBatch.objects.exists())

    def test_non_object_draw_payloads_fail_without_any_database_effect(self):
        self.login()
        for endpoint in ['/api/draw/', '/api/draw-batch/']:
            response = self.api.post(endpoint, [], format='json')
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Draw.objects.exists())
        self.assertFalse(DrawBatch.objects.exists())

    def test_batch_api_strict_owner_metadata_and_replay_is_not_request_throttled(self):
        self.grant(count=10)
        self.login()
        body = {'campaign': 'small', 'version': 1, 'client_request_id': 'api-batch-request'}
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            response = self.api.post('/api/draw-batch/', body, format='json')
        self.assertEqual(response.status_code, 200)
        first = response.data['data']
        self.assertEqual((first['count'], len(first['draws']), first['entries_consumed']), (10, 10, 10))
        self.assertTrue(all(row['participant_id'] == self.person.pk and row['room_code'] == self.room.code for row in first['draws']))
        for _ in range(25):
            replay = self.api.post('/api/draw-batch/', body, format='json')
            self.assertEqual(replay.status_code, 200)
            self.assertEqual([row['id'] for row in replay.data['data']['draws']], [row['id'] for row in first['draws']])
        self.assertEqual(Draw.objects.count(), 10)
        self.assertNotIn('probability', str(first))
        self.assertNotIn('weights', str(first))

    def test_all_50_records_are_paginated_and_new_rolls_do_not_shift_fixed_anchor(self):
        self.history_fixture()
        self.login()
        expected = list(Draw.objects.filter(participant=self.person).order_by('-id').values_list('id', flat=True))
        first = self.api.get('/api/history/').data['data']
        self.assertEqual(first['pagination'], {'page': 1, 'page_size': 10, 'total': 50, 'total_pages': 5,
            'has_previous': False, 'has_next': True, 'before_id': expected[0]})
        self.assertEqual(first['participant_id'], self.person.pk)
        self.assertEqual(len(self.api.get('/api/me/').data['data']['draws']), 30)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            draw(self.person, 'small', 'history-new-single', 1)
        all_ids = []
        for page in range(1, 6):
            response = self.api.get('/api/history/', {'page': page, 'before_id': first['pagination']['before_id']})
            self.assertEqual(response.status_code, 200)
            data = response.data['data']
            self.assertEqual(data['pagination']['total'], 50)
            all_ids.extend(row['id'] for row in data['draws'])
            self.assertTrue(all(row['room_code'] == self.room.code and row['participant_id'] == self.person.pk for row in data['draws']))
        self.assertEqual(all_ids, expected)
        self.assertEqual(self.api.get('/api/history/').data['data']['pagination']['total'], 51)

    def test_history_filters_peer_owner_and_foreign_room_even_when_parameters_spoofed(self):
        self.history_fixture()
        peer = Participant.objects.create(room=self.room, nickname='同房其他老板', code_digest='p' * 64)
        grant_entries(peer, 'small', 10, 'history-peer-grant', '测试', self.admin)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            draw_batch(peer, 'small', 'history-peer-batch', 1)
        other_room = Room.objects.create(code='history-other-room', name='另一房间', created_by=self.admin,
            starts_at=self.room.starts_at, ends_at=self.room.ends_at)
        # Defensive historical data case: owner remains the same but room differs.
        foreign = Draw.objects.filter(participant=self.person).first()
        Draw.objects.filter(pk=foreign.pk).update(room=other_room)
        self.login()
        data = self.api.get('/api/history/', {'participant_id': peer.pk, 'room': other_room.code, 'page_size': 30}).data['data']
        self.assertEqual(data['pagination']['total'], 49)
        self.assertTrue(all(row['participant_id'] == self.person.pk and row['room_code'] == self.room.code for row in data['draws']))
        self.assertNotIn('同房其他老板', str(data))

    def test_history_parameter_validation_empty_and_last_pages(self):
        self.login()
        empty = self.api.get('/api/history/').data['data']
        self.assertEqual(empty['draws'], [])
        self.assertEqual(empty['pagination']['before_id'], 0)
        self.assertEqual(empty['pagination']['total_pages'], 0)
        for params in [{'page': 0}, {'page_size': 31}, {'page_size': 0}, {'before_id': -1}, {'page': 'bad'}]:
            self.assertEqual(self.api.get('/api/history/', params).status_code, 400)
        self.history_fixture()
        result = self.api.get('/api/history/', {'page': 2, 'page_size': 30}).data['data']
        self.assertEqual((len(result['draws']), result['pagination']['total_pages'], result['pagination']['has_next']), (20, 2, False))
        self.assertEqual(self.api.get('/api/history/', {'page': 999}).data['data']['draws'], [])


@skipUnlessDBFeature('has_select_for_update')
class BatchConcurrencyTests(Fixtures, TransactionTestCase):
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

    def test_same_batch_request_has_one_set_of_ten_results_and_one_pair_of_bonuses(self):
        self.grant(count=10)
        Entry.objects.filter(participant=self.person).update(total_rolls=145)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            results = self.race(lambda number: draw_batch(self.person, 'small', 'race-same-batch', 1).pk)
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0], results[1])
        self.assertEqual((DrawBatch.objects.count(), Draw.objects.count(), BonusAward.objects.count()), (1, 10, 2))
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().total_rolls), (0, 155))

    def test_distinct_batches_cannot_both_spend_last_ten_entries(self):
        self.grant(count=10)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            results = self.race(lambda number: draw_batch(self.person, 'small', f'race-batch-{number}', 1).pk)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual((DrawBatch.objects.count(), Draw.objects.count(), Entry.objects.get().remaining), (1, 10, 0))

    def test_single_and_batch_competing_for_ten_entries_never_overspend(self):
        self.grant(count=10)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            results = self.race(lambda number: draw(self.person, 'small', 'race-single', 1).pk if number == 0
                else draw_batch(self.person, 'small', 'race-with-single', 1).pk)
        self.assertEqual(sum(result is not None for result in results), 1)
        entry = Entry.objects.get()
        self.assertEqual(entry.remaining + entry.consumed, 10)
        self.assertEqual(entry.total_rolls, Draw.objects.count())
        self.assertIn(Draw.objects.count(), [1, 10])

    def test_grant_and_batch_compete_without_losing_added_entries(self):
        self.grant(count=10)
        with patch('lottery.dice.secrets.randbelow', return_value=0):
            self.race(lambda number: draw_batch(self.person, 'small', 'race-batch-grant', 1).pk if number == 0
                else grant_entries(self.person, 'small', 3, 'race-batch-add-three', '追加次数', self.admin).pk)
        entry = Entry.objects.get()
        self.assertEqual((entry.granted, entry.consumed, entry.remaining, entry.total_rolls), (13, 10, 3, 10))
