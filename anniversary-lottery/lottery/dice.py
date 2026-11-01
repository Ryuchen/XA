"""Secure dice results with an exact, pool-specific prize reduction."""
import secrets

from .catalog import MATRICES, NAMES, THANKS

DENOMINATOR = 6
ALGORITHM = 'independent-fair-v4'
TARGETED_ALGORITHM = 'targeted-quarter-v6'
UNIFORM_WEIGHTS = (1, 1, 1, 1, 1, 1)
REDUCED_LABELS = {
    'red': frozenset(('9折优惠券', '20元店铺余额')),
    'grand': frozenset(('85折优惠券', '40元店铺余额')),
}
RETENTION_DENOMINATOR = 4


def compute_weights(pool, counters=None):
    """Unit weights describe the initial dice, before any targeted transfer."""
    if pool not in NAMES:
        raise ValueError('Unknown dice pool')
    return UNIFORM_WEIGHTS, UNIFORM_WEIGHTS


def face_for_ticket(ticket):
    if type(ticket) is not int or not 0 <= ticket < DENOMINATOR:
        raise ValueError('Ticket outside dice interval')
    return ticket + 1


def probability_distribution(pool):
    """Exact integer probabilities; only reduced cells and THANKS may change."""
    compute_weights(pool)
    labels = MATRICES[pool]
    reduced = REDUCED_LABELS.get(pool, frozenset())
    if not reduced:
        return {'cell_weights': [[1] * 6 for _ in range(6)], 'joint_denominator': 36}
    thanks_count = sum(label == THANKS for row in labels for label in row)
    reduced_count = sum(label in reduced for row in labels for label in row)
    if not thanks_count or not reduced_count:
        raise ValueError('Targeted reduction requires prizes and THANKS cells')
    base_weight = RETENTION_DENOMINATOR * thanks_count
    transferred_weight = (RETENTION_DENOMINATOR - 1) * reduced_count
    weights = [
        [thanks_count if label in reduced else
         base_weight + transferred_weight if label == THANKS else base_weight
         for label in row]
        for row in labels
    ]
    return {'cell_weights': weights, 'joint_denominator': 36 * base_weight}


def roll_dice(pool, counters=None):
    rows, columns = compute_weights(pool, counters)
    row_ticket = secrets.randbelow(DENOMINATOR)
    column_ticket = secrets.randbelow(DENOMINATOR)
    result = {'algorithm': ALGORITHM, 'row': face_for_ticket(row_ticket),
              'column': face_for_ticket(column_ticket),
              'row_ticket': row_ticket, 'column_ticket': column_ticket,
              'row_weights': list(rows), 'column_weights': list(columns),
              'denominator': DENOMINATOR}
    reduced = REDUCED_LABELS.get(pool)
    if not reduced:
        return result

    # Never re-sample the whole pool: that would increase unrelated prizes.
    # Keep one quarter of targeted hits and transfer the rest only to THANKS.
    result.pop('row_weights')
    result.pop('column_weights')
    result.update(
        algorithm=TARGETED_ALGORITHM,
        initial_row=result['row'], initial_column=result['column'],
        retention_numerator=1, retention_denominator=RETENTION_DENOMINATOR,
        retention_ticket=None, thanks_ticket=None,
        **probability_distribution(pool),
    )
    if MATRICES[pool][result['row'] - 1][result['column'] - 1] in reduced:
        result['retention_ticket'] = secrets.randbelow(RETENTION_DENOMINATOR)
        if result['retention_ticket'] != 0:
            thanks = [(r, c) for r, row in enumerate(MATRICES[pool], 1)
                      for c, label in enumerate(row, 1) if label == THANKS]
            result['thanks_ticket'] = secrets.randbelow(len(thanks))
            result['row'], result['column'] = thanks[result['thanks_ticket']]
    return result
