from collections import Counter
from fractions import Fraction
from itertools import product
from unittest import TestCase as SimpleTestCase
from unittest.mock import call, patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from .catalog import MATRICES
from .dice import probability_distribution, roll_dice
from .engine import draw, draw_batch
from .models import Award, BonusAward, Draw, DrawBatch, Entry, Pool
from .tests import Fixtures


# Independent requirements fixture: do not derive expected targets from policy.
TARGETS = {
    'red': {(2, 1), (6, 2), (4, 3)},
    'grand': {(2, 1), (6, 4), (3, 1)},
}


class ExactReductionTests(SimpleTestCase):
    def test_exhaustive_random_space_reduces_only_requested_cells_by_75_percent(self):
        for slug, targets in TARGETS.items():
            thanks = [(r, c) for r, row in enumerate(MATRICES[slug], 1)
                      for c, label in enumerate(row, 1) if label == '感谢参与']
            counts = Counter()
            # Enumerate the full equally likely elementary space, including
            # unused gate/fallback tickets for rolls that do not need them.
            for r, c, gate, fallback in product(range(1, 7), range(1, 7), range(4), range(len(thanks))):
                tickets = [r - 1, c - 1]
                calls = [call(6), call(6)]
                if (r, c) in targets:
                    tickets.append(gate)
                    calls.append(call(4))
                    if gate:
                        tickets.append(fallback)
                        calls.append(call(len(thanks)))
                with patch('lottery.dice.secrets.randbelow', side_effect=tickets) as rng:
                    result = roll_dice(slug)
                self.assertEqual(rng.call_args_list, calls)
                final = (result['row'], result['column'])
                self.assertEqual(final, thanks[fallback] if (r, c) in targets and gate else (r, c))
                self.assertEqual(result['algorithm'], 'targeted-quarter-v6')
                counts[final] += 1
            total = 36 * 4 * len(thanks)
            snapshot = probability_distribution(slug)
            self.assertEqual(sum(counts.values()), total)
            self.assertEqual(snapshot['joint_denominator'], total)
            for coordinate, count in counts.items():
                r, c = coordinate
                expected = (Fraction(1, 144) if coordinate in targets else
                            Fraction(1, 36) + Fraction(1, 16 * len(thanks)) if coordinate in thanks else
                            Fraction(1, 36))
                self.assertEqual(Fraction(count, total), expected, (slug, coordinate))
                self.assertEqual(snapshot['cell_weights'][r - 1][c - 1], count)
            self.assertEqual(sum(Fraction(counts[cell], total) for cell in thanks),
                             Fraction(len(thanks), 36) + Fraction(1, 16))

    def test_small_pool_all_36_outcomes_and_rng_calls_are_unchanged(self):
        for row, column in product(range(6), repeat=2):
            with patch('lottery.dice.secrets.randbelow', side_effect=[row, column]) as rng:
                result = roll_dice('small')
            self.assertEqual((result['row'], result['column']), (row + 1, column + 1))
            self.assertEqual(rng.call_args_list, [call(6), call(6)])
            self.assertEqual(result['algorithm'], 'independent-fair-v4')
            self.assertNotIn('retention_ticket', result)
        self.assertEqual(probability_distribution('small'),
                         {'cell_weights': [[1] * 6 for _ in range(6)], 'joint_denominator': 36})

    def test_history_cannot_change_targeted_probability_or_award_selection(self):
        for slug in TARGETS:
            for progress in [0, 29, 49, 149, 100000]:
                with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 0]):
                    retained = roll_dice(slug, {'total_rolls': progress, 'ordinary_dry_streak': progress})
                with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 3, 0]):
                    redirected = roll_dice(slug, {'total_rolls': progress, 'ordinary_dry_streak': progress})
                self.assertEqual((retained['row'], retained['column']), (2, 1))
                self.assertEqual(MATRICES[slug][redirected['row'] - 1][redirected['column'] - 1], '感谢参与')
                self.assertEqual(retained['cell_weights'], redirected['cell_weights'])
                self.assertNotIn('ordinary_dry_streak', redirected)


class TargetedDrawTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_lottery()

    def test_all_target_cells_retain_original_awards_and_exact_probability(self):
        for slug, targets in TARGETS.items():
            self.grant(pool=slug, count=3, key=f'target-grant-{slug}')
            for row, column in sorted(targets):
                result = self.pick(slug, row, column, f'target-{slug}-{row}-{column}')
                self.assertEqual(result.award.label, MATRICES[slug][row - 1][column - 1])
                audit = result.probability_snapshot
                self.assertEqual(audit['algorithm'], 'targeted-quarter-v6')
                self.assertEqual(Fraction(result.result_snapshot['probability'], audit['joint_denominator']),
                                 Fraction(1, 144))
                self.assertEqual(sum(cell['probability'] for cell in audit['cells']), audit['joint_denominator'])
                self.assertEqual(audit['retention_ticket'], 0)
                self.assertIsNone(audit['thanks_ticket'])

    def test_transferred_result_has_matching_cell_and_no_primary_award_but_preserves_pity(self):
        for slug, amounts in [('red', [1000, 2000]), ('grand', [2000, 4000])]:
            self.grant(pool=slug, count=1, key=f'transfer-grant-{slug}')
            Entry.objects.filter(pool__slug=slug).update(total_rolls=149)
            with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 2, 0]):
                result = draw(self.person, slug, f'transfer-pity-{slug}', 1)
            self.assertEqual(result.cell.kind, 'THANKS')
            self.assertEqual(result.result_snapshot['label'], result.cell.label)
            self.assertEqual((result.result_snapshot['row'], result.result_snapshot['column']),
                             (result.cell.row, result.cell.column))
            self.assertFalse(Award.objects.filter(draw=result).exists())
            self.assertEqual(sorted(result.bonuses.values_list('data__balance_amount', flat=True)), amounts)
            self.assertEqual(result.roll_number, 150)
            self.assertEqual(result.entries_remaining, 0)
            self.assertEqual(result.probability_snapshot['initial_row'], 2)
            self.assertEqual(result.probability_snapshot['initial_column'], 1)
            self.assertEqual(result.probability_snapshot['retention_ticket'], 2)
            with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not reroll')):
                self.assertEqual(draw(self.person, slug, f'transfer-pity-{slug}', 1).pk, result.pk)

    def test_mixed_ten_draws_use_same_policy_keep_refunds_pity_and_permanent_replay(self):
        for slug, retry, amounts in [('red', [1, 2], [1000, 2000]), ('grand', [3, 1], [2000, 4000])]:
            self.grant(pool=slug, count=10, key=f'mixed-grant-{slug}')
            Entry.objects.filter(pool__slug=slug).update(total_rolls=145)
            # One retained coupon, three transferred coupons, one retry, five
            # ordinary outcomes. All 10 count; only nine entries are consumed.
            tickets = [1, 0, 0] + [1, 0, 1, 0] * 3 + retry + [0, 4] * 5
            with patch('lottery.dice.secrets.randbelow', side_effect=tickets):
                batch = draw_batch(self.person, slug, f'mixed-batch-{slug}', 1)
            results = list(batch.draws.order_by('batch_index'))
            self.assertEqual(len(results), 10)
            self.assertEqual(results[0].award.kind, 'COUPON')
            self.assertEqual([d.cell.kind for d in results[1:5]], ['THANKS'] * 3 + ['RETRY'])
            self.assertEqual((batch.entries_consumed, batch.entries_remaining, batch.total_rolls), (9, 1, 155))
            self.assertEqual(sorted(results[4].bonuses.values_list('data__balance_amount', flat=True)), amounts)
            snapshots = list(batch.draws.order_by('batch_index').values('id', 'result_snapshot', 'probability_snapshot'))
            Pool.objects.filter(slug=slug).update(version=8, enabled=False)
            with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not reroll')):
                replay = draw_batch(self.person, slug, f'mixed-batch-{slug}', 1)
            self.assertEqual(replay.pk, batch.pk)
            self.assertEqual(list(replay.draws.order_by('batch_index').values('id', 'result_snapshot', 'probability_snapshot')),
                             snapshots)

    def test_rng_failure_after_prior_batch_rolls_rolls_back_everything(self):
        self.grant(pool='grand', count=10)
        Entry.objects.update(total_rolls=149)
        with patch('lottery.dice.secrets.randbelow',
                   side_effect=[0, 1, 1, 0, 1, RuntimeError('random source unavailable')]):
            with self.assertRaises(RuntimeError):
                draw_batch(self.person, 'grand', 'targeted-atomic-rng', 1)
        self.assertFalse(Draw.objects.exists())
        self.assertFalse(DrawBatch.objects.exists())
        self.assertFalse(Award.objects.exists())
        self.assertFalse(BonusAward.objects.exists())
        self.assertEqual((Entry.objects.get().remaining, Entry.objects.get().total_rolls), (10, 149))

    def test_public_single_batch_catalog_and_owner_history_do_not_leak_odds(self):
        self.grant(pool='red', count=11)
        api = APIClient()
        login = api.post('/api/login/', {'code': self.raw_code, 'room': self.room.code}, format='json')
        self.assertEqual(login.status_code, 200)
        api.credentials(HTTP_AUTHORIZATION='Bearer ' + login.data['data']['token'], HTTP_X_LOTTERY_ROOM=self.room.code)
        with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 1, 0]):
            single = api.post('/api/draw/', {'campaign': 'red', 'version': 1,
                              'client_request_id': 'private-single-odds'}, format='json')
        with patch('lottery.dice.secrets.randbelow', side_effect=[1, 0, 0] * 10):
            batch = api.post('/api/draw-batch/', {'campaign': 'red', 'version': 1,
                             'client_request_id': 'private-batch-odds'}, format='json')
        forbidden = {'probability', 'probability_percent', 'probability_snapshot',
                     'row_ticket', 'column_ticket', 'cell_weights', 'joint_denominator',
                     'retention_ticket', 'retention_numerator', 'retention_denominator',
                     'thanks_ticket', 'initial_row', 'initial_column', 'weight'}

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        for response in [single, batch, api.get('/api/catalog/'), api.get('/api/me/'), api.get('/api/history/')]:
            self.assertEqual(response.status_code, 200)
            check(response.data)
        rules = api.get('/api/catalog/').data['data']['rules']
        self.assertFalse(any('公平' in rule or '每个点数出现的机会相同' in rule for rule in rules))
        self.assertEqual(Draw.objects.count(), 11)


class TargetedMigrationTests(Fixtures, TransactionTestCase):
    def test_only_affected_versions_change_existing_records_and_batch_replay_survive(self):
        MigrationExecutor(connection).migrate([('lottery', '0006_draw_batches')])
        try:
            self.setup_lottery()
            self.grant(pool='red', count=11)
            # Capture real v4-format committed records, including an entire batch.
            fair = {'algorithm': 'independent-fair-v4', 'row': 1, 'column': 1,
                    'row_ticket': 0, 'column_ticket': 0, 'row_weights': [1] * 6,
                    'column_weights': [1] * 6, 'denominator': 6}
            with patch('lottery.engine.roll_dice', return_value=fair):
                prior = draw(self.person, 'red', 'legacy-single-replay', 1)
                batch = draw_batch(self.person, 'red', 'legacy-batch-replay', 1)
            from django.apps import apps
            models = [model for model in apps.get_models()
                      if model._meta.app_label == 'lottery' and model is not Pool]
            before = {m._meta.label: list(m.objects.order_by('pk').values()) for m in models}
            password = type(self.admin).objects.get(pk=self.admin.pk).password
            MigrationExecutor(connection).migrate([('lottery', '0007_targeted_prize_reduction')])
            self.assertEqual(dict(Pool.objects.values_list('slug', 'version')), {'small': 1, 'red': 2, 'grand': 2})
            self.assertEqual({m._meta.label: list(m.objects.order_by('pk').values()) for m in models}, before)
            self.assertEqual(type(self.admin).objects.get(pk=self.admin.pk).password, password)
            with patch('lottery.dice.secrets.randbelow', side_effect=AssertionError('must not reroll')):
                self.assertEqual(draw(self.person, 'red', 'legacy-single-replay', 1).pk, prior.pk)
                self.assertEqual(draw_batch(self.person, 'red', 'legacy-batch-replay', 1).pk, batch.pk)
                with self.assertRaises(ValidationError):
                    draw(self.person, 'red', 'stale-new-request', 1)
            MigrationExecutor(connection).migrate([('lottery', '0007_targeted_prize_reduction')])
            self.assertEqual(dict(Pool.objects.values_list('slug', 'version')), {'small': 1, 'red': 2, 'grand': 2})
        finally:
            MigrationExecutor(connection).migrate([('lottery', '0007_targeted_prize_reduction')])
