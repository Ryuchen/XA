"""Decision rules for one grab round, independent of storage and scheduling.

The caller must hold the order/round locks and prove that every command
received through the cutoff has been processed before calling finalize.
This module does not itself supply that durable watermark or move funds.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .grab_ranking import CandidateFacts, decide


def _time(value, label):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f'{label}必须包含时区')
    return value


@dataclass(frozen=True)
class RoundWindow:
    opened_at: datetime
    deadline: datetime
    total_deadline: datetime
    duration_seconds: int
    simultaneous_visibility: bool = False

    def __post_init__(self):
        for value, label in ((self.opened_at, '开始时间'), (self.deadline, '本轮截止'),
                             (self.total_deadline, '订单截止')):
            _time(value, label)
        if type(self.duration_seconds) is not int or self.duration_seconds <= 0:
            raise ValueError('抢单窗口必须是正整数秒')
        if self.deadline != self.opened_at + timedelta(seconds=self.duration_seconds):
            raise ValueError('本轮截止与窗口长度不一致')
        if self.total_deadline < self.deadline:
            raise ValueError('订单截止不能早于完整窗口')
        if type(self.simultaneous_visibility) is not bool:
            raise ValueError('可见规则无效')


def first_round(*, opened_at, duration_seconds, original_total_deadline):
    _time(opened_at, '入池时间')
    _time(original_total_deadline, '订单原截止')
    if type(duration_seconds) is not int or duration_seconds <= 0:
        raise ValueError('抢单窗口必须是正整数秒')
    if original_total_deadline < opened_at:
        raise ValueError('订单截止不能早于入池')
    deadline = opened_at + timedelta(seconds=duration_seconds)
    return RoundWindow(opened_at, deadline, max(deadline, original_total_deadline), duration_seconds)


@dataclass(frozen=True)
class WindowDecision:
    action: str
    winner_id: int | None = None
    next_round: RoundWindow | None = None
    tied_ids: tuple[int, ...] = ()
    discarded_missing_time: tuple[int, ...] = ()


def finalize(window, candidates, *, now, processed_through, cancel_received_at=None,
             winner_eligibility=None):
    """Evaluate a closed round against a verified command-processing watermark.

    ``processed_through`` is the proven successful durable-registration
    watermark, not a request arrival time, a pre-commit timestamp, a client
    timestamp or the scheduler's wall clock. A cancellation is supplied
    only after ownership and validity are verified. ``winner_eligibility`` is
    None until the top candidate is rechecked; then pass {candidate_id: bool}
    from the same locked transaction. Earlier eligibility must never be reused.
    """
    if not isinstance(window, RoundWindow):
        raise ValueError('抢单轮次无效')
    _time(now, '结算时间')
    _time(processed_through, '已处理水位')
    if processed_through > now:
        raise ValueError('已处理水位不能晚于当前时间')
    if cancel_received_at is not None:
        _time(cancel_received_at, '取消接收时间')
        if cancel_received_at > now:
            raise ValueError('取消接收时间不能晚于当前时间')
    if now <= window.deadline or processed_through < window.deadline:
        return WindowDecision('WAIT')
    if cancel_received_at is not None and cancel_received_at <= window.deadline:
        return WindowDecision('CANCEL')
    # New submissions must finish strictly before cutoff. A migrated historical
    # record without reliable time may participate only if the caller proved
    # its migration into this round; ranking handles its tie-time limitation.
    candidates = tuple(candidates)
    if any(not isinstance(candidate, CandidateFacts) for candidate in candidates):
        raise ValueError('候选事实无效')
    candidates = tuple(candidate for candidate in candidates if
        (candidate.completed_at is None and candidate.historical and not window.simultaneous_visibility) or
        (candidate.completed_at is not None and candidate.completed_at < window.deadline and
         (candidate.completed_at >= window.opened_at or (candidate.historical and not window.simultaneous_visibility))))
    result = decide(candidates)
    if result.tied_ids:
        next_window = _reopen(window, now)
        return WindowDecision('REOPEN', next_round=next_window, tied_ids=result.tied_ids,
                              discarded_missing_time=result.discarded_missing_time)
    if result.winner_id is None:
        return WindowDecision('CANCEL' if now >= window.total_deadline else 'PARK',
                              discarded_missing_time=result.discarded_missing_time)
    if winner_eligibility is not None and not isinstance(winner_eligibility, dict):
        raise ValueError('获胜候选资格复核结果无效')
    if winner_eligibility is None or result.winner_id not in winner_eligibility:
        return WindowDecision('CHECK_WINNER', winner_id=result.winner_id,
                              discarded_missing_time=result.discarded_missing_time)
    eligible = winner_eligibility[result.winner_id]
    if type(eligible) is not bool:
        raise ValueError('获胜候选资格复核结果无效')
    if eligible:
        return WindowDecision('ASSIGN', winner_id=result.winner_id,
                              discarded_missing_time=result.discarded_missing_time)
    if now >= window.total_deadline:
        return WindowDecision('CANCEL', discarded_missing_time=result.discarded_missing_time)
    return WindowDecision('REOPEN', next_round=_reopen(window, now),
                          discarded_missing_time=result.discarded_missing_time)


def _reopen(window, now):
    # Every reopening retains the duration frozen when this order entered.
    deadline = now + timedelta(seconds=window.duration_seconds)
    return RoundWindow(now, deadline, max(window.total_deadline, deadline), window.duration_seconds, True)
