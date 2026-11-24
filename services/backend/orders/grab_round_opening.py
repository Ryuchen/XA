"""Persist bounds around each real round-creation commit.

The two samples bound a commit; neither is the commit timestamp. Missing or
untrustworthy observations cannot authorize automatic allocation or refund.
"""
import logging
from datetime import timedelta

from django.db import DatabaseError
from stores.context import current_store
from stores.db import transaction

from .models import Order, OrderGrabRound, OrderGrabRoundOpeningEvidence, OrderGrabPoolPassSnapshot

logger = logging.getLogger(__name__)


def stage(round_):
    from .grab_clock_contract import production_enabled, sample
    if not production_enabled():
        return
    connection = transaction.get_connection()
    store = current_store()
    if not connection.in_atomic_block or not store or store.database_alias != connection.alias:
        raise ValueError('抢单轮次提交证据缺少本店事务')
    lower, driver, lower_sample = sample()
    evidence = OrderGrabRoundOpeningEvidence.objects.create(round=round_, lower_at=lower,
        clock_evidence={'lower': lower_sample})
    alias, slug, order_id = connection.alias, store.slug, round_.pool.order_id
    transaction.on_commit(lambda: _observe(evidence.pk, order_id, alias, slug, driver), robust=True)


def _observe(evidence_id, order_id, alias, slug, driver):
    from .grab_clock_contract import sample, valid_bounds
    store = current_store()
    connection = transaction.get_connection()
    if not store or store.slug != slug or store.database_alias != alias or connection.alias != alias:
        return
    try:
        upper, after_driver, upper_sample = sample()
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            evidence = (OrderGrabRoundOpeningEvidence.objects.select_for_update()
                        .filter(pk=evidence_id, round__pool__order_id=order_id).first())
            if not order or not evidence or evidence.state != 'PENDING':
                return
            lower_sample = evidence.clock_evidence.get('lower') if isinstance(evidence.clock_evidence, dict) else None
            clock = {'lower': lower_sample, 'upper': upper_sample}
            opening = getattr(order, 'grab_opening_intent', None)
            identity = getattr(opening, 'database_identity', '')
            if (after_driver is not driver or not identity or not valid_bounds(
                    evidence.lower_at, upper, clock, identity=identity)):
                evidence.state, evidence.reason = 'REVIEW', '开池提交前后时钟或连接无法核对'
            else:
                evidence.upper_at, evidence.clock_evidence = upper, clock
                round_ = OrderGrabRound.objects.select_for_update().select_related('pool').get(pk=evidence.round_id)
                if round_.number == 1:
                    from users.pass_rules import lock_pass_config
                    from users.models import EscortProfile
                    from .grab_receipt_probe import _db_clock
                    lock_pass_config()
                    before, _, _ = _db_clock()
                    profiles = list(EscortProfile.objects.select_for_update().filter(
                        pass_expires_at__gt=before).exclude(pass_tier='').order_by('pk')
                        .values('pk', 'user_id', 'account_id', 'pass_tier', 'pass_expires_at'))
                activation_lower, activation_driver, activation_sample = sample()
                if activation_driver is not driver or activation_lower < upper:
                    raise ValueError('抢单激活时钟或连接已变化')
                if round_.number == 1:
                    eligible = [row for row in profiles if row['pass_expires_at'] > activation_lower]
                    if any(row['pass_tier'] not in ('BLACK', 'GOLD', 'SILVER', 'BRONZE') for row in eligible):
                        raise ValueError('抢单激活通行证快照无效')
                    round_.pool.entry_passes.all().delete()
                    OrderGrabPoolPassSnapshot.objects.bulk_create([
                        OrderGrabPoolPassSnapshot(pool=round_.pool, profile_id_snapshot=row['pk'],
                            user_id_snapshot=row['user_id'], account_id_snapshot=row['account_id'],
                            tier=row['pass_tier'], expires_at=row['pass_expires_at']) for row in eligible], batch_size=500)
                    round_.pool.pass_snapshot_count = len(eligible)
                    round_.pool.save(update_fields=['pass_snapshot_count'])
                evidence.state, evidence.reason = 'PREPARED', ''
                evidence.activation_lower_at = activation_lower
                evidence.activation_clock_evidence = {'lower': activation_sample}
                transaction.on_commit(lambda: _observe_activation(evidence.pk, order_id, alias, slug, driver), robust=True)
            evidence.save(update_fields=['state', 'reason', 'upper_at', 'clock_evidence',
                                         'activation_lower_at', 'activation_clock_evidence'])
    except (DatabaseError, ValueError):
        # No later observation can be substituted for this same-connection
        # post-commit sample. Keep PENDING for staff review and cancellation.
        logger.exception('抢单轮次提交时间待核对 order=%s round_evidence=%s', order_id, evidence_id)


def _observe_activation(evidence_id, order_id, alias, slug, driver):
    from .grab_clock_contract import sample, valid_bounds
    store = current_store()
    connection = transaction.get_connection()
    if not store or store.slug != slug or store.database_alias != alias or connection.alias != alias:
        return
    try:
        upper, after_driver, upper_sample = sample()
        with transaction.atomic():
            Order.objects.select_for_update().get(pk=order_id)
            evidence = OrderGrabRoundOpeningEvidence.objects.select_for_update().get(
                pk=evidence_id, round__pool__order_id=order_id)
            if evidence.state != 'PREPARED':
                return
            lower_sample = evidence.activation_clock_evidence.get('lower') if isinstance(evidence.activation_clock_evidence, dict) else None
            clock = {'lower': lower_sample, 'upper': upper_sample}
            identity = evidence.clock_evidence.get('lower', {}).get('database_identity', '')
            if (after_driver is not driver or not identity or not valid_bounds(
                    evidence.activation_lower_at, upper, clock, identity=identity)):
                evidence.state, evidence.reason = 'REVIEW', '开池激活提交时钟或连接无法核对'
            else:
                evidence.state, evidence.reason = 'OBSERVED', ''
                evidence.activation_upper_at, evidence.activation_clock_evidence = upper, clock
                transaction.on_commit(lambda: _resume_waiting(evidence.round_id), robust=True)
            evidence.save(update_fields=['state', 'reason', 'activation_upper_at', 'activation_clock_evidence'])
    except (DatabaseError, ValueError):
        logger.exception('抢单激活提交待核对 order=%s round_evidence=%s', order_id, evidence_id)


def _resume_waiting(round_id):
    from .grab_clock_contract import production_enabled, round_identity_valid
    from .grab_customer_review import has_pending_review
    from .grab_receipt_probe import _lock_round, finalize_opening_waiters
    from .grab_round_facts import current_pending_round_state

    if not production_enabled():
        return
    finalize_opening_waiters(round_id)
    try:
        with transaction.atomic():
            order, round_ = _lock_round(round_id)
            state = current_pending_round_state(order, round_id)
            if (not state or state.status not in ('OPEN', 'PARKED')
                    or has_pending_review(order.pk, lock=True) or not round_identity_valid(round_, lock=True)):
                return
            deadline = cutoff_bounds(round_, 'OPEN', lock=True)[1] + timedelta(seconds=1)
            if round_.number > 1:
                from .dispatching import reschedule_pool_dispatch
                try:
                    reschedule_pool_dispatch(order.pk)
                except Exception:
                    # The periodic dispatcher can still recover pending group records.
                    logger.exception('重开抢单群发重排待恢复 order=%s', order.pk)
            if order.required_provider_count == 1:
                from .grab_single_flow import enabled, enqueue
            else:
                from .grab_team_flow import enabled, enqueue
            if enabled():
                transaction.on_commit(lambda: enqueue(order.pk, expected_round_id=round_id), robust=True)
                transaction.on_commit(lambda: enqueue(order.pk, eta=deadline, expected_round_id=round_id), robust=True)
    except ValueError:
        return


def _observed_opening(round_, *, lock):
    observations = OrderGrabRoundOpeningEvidence.objects.filter(round=round_)
    evidence = (observations.select_for_update() if lock else observations).first()
    return validated_opening(evidence)


def validated_opening(evidence, *, identity=None):
    from .grab_clock_contract import database_id, valid_bounds
    if (not evidence or evidence.state != 'OBSERVED' or not evidence.upper_at
            or not evidence.activation_lower_at or not evidence.activation_upper_at or evidence.reason):
        raise ValueError('抢单轮次提交时间待核对')
    identity = database_id() if identity is None else identity
    if not valid_bounds(evidence.lower_at, evidence.upper_at, evidence.clock_evidence, identity=identity):
        raise ValueError('抢单轮次提交时钟证据异常')
    if not valid_bounds(evidence.activation_lower_at, evidence.activation_upper_at,
                        evidence.activation_clock_evidence, identity=identity):
        raise ValueError('抢单轮次激活提交时钟证据异常')
    return evidence


def _opening_interval(evidence):
    from .grab_clock_contract import TOLERANCE_NS

    precision = timedelta(microseconds=TOLERANCE_NS // 1000)
    return evidence.activation_lower_at - precision, evidence.activation_upper_at + precision


def opening_bounds(round_, *, lock=False):
    """Return conservative bounds, never an invented opening instant."""
    return _opening_interval(_observed_opening(round_, lock=lock))


def provisional_opening_lower(round_):
    """Return only a lower bound while activation acknowledgement is pending."""
    evidence = OrderGrabRoundOpeningEvidence.objects.filter(round=round_).first()
    return provisional_lower(evidence)


def provisional_lower(evidence, *, identity=None):
    from .grab_clock_contract import TOLERANCE_NS, database_id, valid_bounds
    if (not evidence or evidence.state != 'PREPARED' or evidence.reason or not evidence.upper_at
            or not evidence.activation_lower_at or not isinstance(evidence.activation_clock_evidence, dict)):
        raise ValueError('抢单轮次激活提交证据待核对')
    identity = database_id() if identity is None else identity
    sample = evidence.activation_clock_evidence.get('lower')
    if (not valid_bounds(evidence.lower_at, evidence.upper_at, evidence.clock_evidence, identity=identity)
            or not isinstance(sample, dict) or sample.get('database_identity') != identity
            or evidence.activation_lower_at < evidence.upper_at):
        raise ValueError('抢单轮次激活提交证据异常')
    return evidence.activation_lower_at - timedelta(microseconds=TOLERANCE_NS // 1000)


def _verified_reopen(previous, cutoff, reopened_at, *, lock):
    from .grab_cutoff_watermark import verified_reopen_seal
    from .models import OrderGrabSingleRoundState, OrderGrabTeamRoundState
    state_model = OrderGrabSingleRoundState if previous.pool.required_provider_count == 1 else OrderGrabTeamRoundState
    states = state_model.objects.filter(round=previous)
    state = (states.select_for_update() if lock else states).first()
    if not state or state.status != 'REOPENED':
        return None
    return verified_reopen_seal(previous, cutoff=cutoff, reopened_at=reopened_at, lock=lock)


def _sealed_reopen_lower(previous, reopened, evidence, *, cutoff, total_lower, read_seal):
    if (not reopened.simultaneous_visibility or reopened.opened_at <= cutoff
            or not reopened.opened_at <= evidence.lower_at <= evidence.upper_at <= evidence.activation_lower_at):
        return None
    mark = read_seal(previous, cutoff, reopened.opened_at)
    if not mark:
        return None
    precision = timedelta(microseconds=mark.precision_us)
    if reopened.opened_at + precision >= total_lower:
        return None
    # The order lock places this round after the original seal, even if the
    # expanded activation interval overlaps the preceding deadline.
    return mark.observed_at - precision


def cutoff_bounds(round_, phase, *, lock=False):
    """Derive earliest/latest cutoff from real opening commit bounds."""
    def read_rounds():
        rounds = round_.pool.rounds.filter(number__lte=round_.number).order_by('number')
        return list(rounds.select_for_update() if lock else rounds)

    return cutoff_bounds_from_rounds(
        round_, phase, read_rounds,
        read_opening=lambda row: _observed_opening(row, lock=lock),
        read_seal=lambda previous, cutoff, reopened_at: _verified_reopen(previous, cutoff, reopened_at, lock=lock),
    )


def cutoff_bounds_from_rounds(round_, phase, read_rounds, *, read_opening, read_seal):
    if phase not in ('OPEN', 'PARKED'):
        raise ValueError('抢单轮次状态待核对')
    pool = round_.pool
    timeout_minutes = pool.order.pending_timeout_minutes
    if (type(timeout_minutes) is not int or not 1 <= timeout_minutes <= 1440
            or type(pool.window_seconds) is not int or not 1 <= pool.window_seconds <= 86400
            or pool.original_total_deadline != pool.opened_at + timedelta(minutes=timeout_minutes)):
        raise ValueError('抢单总等待快照待核对')
    rows = read_rounds()
    if len(rows) != round_.number or not rows or rows[-1].pk != round_.pk:
        raise ValueError('抢单轮次历史待核对')
    total_lower = total_upper = deadline_upper = previous = None
    for number, row in enumerate(rows, 1):
        if row.number != number:
            raise ValueError('抢单轮次历史待核对')
        evidence = read_opening(row)
        lower, upper = _opening_interval(evidence)
        if previous is not None and lower < deadline_upper:
            sealed_lower = _sealed_reopen_lower(previous, row, evidence, cutoff=deadline_upper,
                                               total_lower=total_lower, read_seal=read_seal)
            if sealed_lower is None or sealed_lower > upper:
                raise ValueError('重开时间与上轮截止区间交叉，待核对')
            lower = max(lower, sealed_lower)
        deadline_lower = lower + timedelta(seconds=pool.window_seconds)
        deadline_upper = upper + timedelta(seconds=pool.window_seconds)
        if number == 1:
            total_lower = max(lower + timedelta(minutes=timeout_minutes), deadline_lower)
            total_upper = max(upper + timedelta(minutes=timeout_minutes), deadline_upper)
        else:
            total_lower = max(total_lower, deadline_lower)
            total_upper = max(total_upper, deadline_upper)
        previous = row
    return (deadline_lower, deadline_upper) if phase == 'OPEN' else (total_lower, total_upper)


def classify_window(*, lower, upper, precision_us, round_, phase, cancellation=False, lock=False):
    """Compare a commit interval with a cutoff interval, preserving ambiguity."""
    from datetime import datetime
    if (not isinstance(lower, datetime) or lower.utcoffset() is None
            or not isinstance(upper, datetime) or upper.utcoffset() is None or upper < lower
            or type(precision_us) is not int or not 1 <= precision_us <= 1_000_000
            or type(cancellation) is not bool):
        raise ValueError('登记时间证据无效')
    earliest, latest = cutoff_bounds(round_, phase, lock=lock)
    quantum = timedelta(microseconds=precision_us)
    if lower - quantum < round_.opened_at:
        return 'REVIEW', 'CROSSED_OPENING'
    if (upper + quantum <= earliest if cancellation else upper + quantum < earliest):
        return 'BEFORE', ''
    if (lower - quantum > latest if cancellation else lower - quantum >= latest):
        return 'AFTER', ''
    return 'REVIEW', 'CROSSED_CUTOFF_INTERVAL'
