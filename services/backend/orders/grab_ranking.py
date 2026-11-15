"""Exact comparison of verified candidates for one automatic grab round.

Callers must supply store-local, independently verified 30-day facts. This
module deliberately does not infer participation or refund allocation from
legacy settlement shares and does not assign orders or move money.
"""
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction


PASS_RANK = {'': 0, 'BRONZE': 1, 'SILVER': 2, 'GOLD': 3, 'BLACK': 4}


@dataclass(frozen=True)
class MemberFacts:
    account_id: int
    submitted_pass_tier: str
    net_original_amount: int
    good_evaluations: int
    valid_evaluations: int

    def __post_init__(self):
        if type(self.account_id) is not int or self.account_id <= 0:
            raise ValueError('候选陪玩账号无效')
        if not isinstance(self.submitted_pass_tier, str) or self.submitted_pass_tier not in PASS_RANK:
            raise ValueError('抢单提交时通行证等级无法核实')
        values = (self.net_original_amount, self.good_evaluations, self.valid_evaluations)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError('接单额或评价统计无效')
        if self.good_evaluations > self.valid_evaluations:
            raise ValueError('好评数不能超过有效评价数')

    @property
    def good_rate(self):
        return Fraction(self.good_evaluations, self.valid_evaluations) if self.valid_evaluations else Fraction(0)


@dataclass(frozen=True)
class CandidateFacts:
    candidate_id: int
    members: tuple[MemberFacts, ...]
    completed_at: datetime | None
    historical: bool = False

    def __post_init__(self):
        if type(self.candidate_id) is not int or self.candidate_id <= 0:
            raise ValueError('候选记录无效')
        if (not isinstance(self.members, tuple) or len(self.members) not in (1, 2)
                or any(not isinstance(member, MemberFacts) for member in self.members)
                or len({member.account_id for member in self.members}) != len(self.members)):
            raise ValueError('候选必须是不同陪玩组成的完整单人或双人队伍')
        if type(self.historical) is not bool:
            raise ValueError('历史候选标识无效')
        if self.completed_at is None:
            if not self.historical:
                raise ValueError('新候选必须记录完成提交时间')
        elif not isinstance(self.completed_at, datetime) or self.completed_at.utcoffset() is None:
            raise ValueError('提交完成时间必须包含时区')

    @property
    def comparison_key(self):
        # Each team dimension takes its own maximum, not one member's tuple.
        return (max(PASS_RANK[member.submitted_pass_tier] for member in self.members),
                max(member.net_original_amount for member in self.members),
                max(member.good_rate for member in self.members))


@dataclass(frozen=True)
class RankingDecision:
    winner_id: int | None
    tied_ids: tuple[int, ...] = ()
    discarded_missing_time: tuple[int, ...] = ()


def decide(candidates):
    """Return a winner, an exact tie requiring reopening, or no candidate.

    A historical candidate with unknown completion time is discarded only
    when another candidate has the same first three dimensions and time is
    therefore needed to break that tie. The caller must persist the decision
    and revalidate the winner under the order and provider locks.
    """
    candidates = tuple(candidates)
    if any(not isinstance(candidate, CandidateFacts) for candidate in candidates):
        raise ValueError('候选事实无效')
    if len({candidate.candidate_id for candidate in candidates}) != len(candidates):
        raise ValueError('候选记录重复')
    groups = {}
    for candidate in candidates:
        groups.setdefault(candidate.comparison_key, []).append(candidate)
    discarded = []
    for key in sorted(groups, reverse=True):
        group = groups[key]
        if len(group) == 1:
            return RankingDecision(winner_id=group[0].candidate_id, discarded_missing_time=tuple(sorted(discarded)))
        discarded.extend(candidate.candidate_id for candidate in group if candidate.completed_at is None)
        group = [candidate for candidate in group if candidate.completed_at is not None]
        if not group:
            continue
        earliest = min(candidate.completed_at for candidate in group)
        first = sorted(candidate.candidate_id for candidate in group if candidate.completed_at == earliest)
        if len(first) > 1:
            return RankingDecision(winner_id=None, tied_ids=tuple(first), discarded_missing_time=tuple(sorted(discarded)))
        return RankingDecision(winner_id=first[0], discarded_missing_time=tuple(sorted(discarded)))
    return RankingDecision(winner_id=None, discarded_missing_time=tuple(sorted(discarded)))


def evaluation_facts(account_ids, *, as_of):
    """Count each requested provider's valid store-local 30-day evaluations.

    Business-account identity takes precedence; the legacy user is used only
    when an evaluation has no business account. A later void does not change a
    historical as-of comparison, while an already voided evaluation is omitted.
    """
    from datetime import timedelta
    from django.db.models import Count, Q
    from users.models import EscortProfile
    from .models import Evaluation
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('评价统计时间必须包含时区')
    account_ids = tuple(account_ids)
    if any(type(account_id) is not int or account_id <= 0 for account_id in account_ids):
        raise ValueError('陪玩账号无效')
    account_ids = set(account_ids)
    profiles = list(EscortProfile.objects.filter(account_id__in=account_ids).values_list('account_id', 'user_id'))
    if {account_id for account_id, _ in profiles} != account_ids:
        raise ValueError('陪玩账号或本店资料不存在')
    by_user = {user_id: account_id for account_id, user_id in profiles}
    totals = {account_id: {'good_evaluations': 0, 'valid_evaluations': 0} for account_id in account_ids}
    if not account_ids:
        return totals
    rows = Evaluation.objects.filter(
        Q(provider_account_id__in=account_ids) | Q(provider_account__isnull=True, provider_id__in=by_user),
        Q(voided_at__isnull=True) | Q(voided_at__gt=as_of),
        created_at__gte=as_of-timedelta(days=30), created_at__lte=as_of,
    ).values('provider_account_id', 'provider_id').annotate(
        valid=Count('pk'), good=Count('pk', filter=Q(score__gte=4)))
    for row in rows:
        account_id = row['provider_account_id'] or by_user.get(row['provider_id'])
        if account_id in totals:
            totals[account_id]['valid_evaluations'] += row['valid']
            totals[account_id]['good_evaluations'] += row['good']
    return totals


@dataclass(frozen=True)
class SubmittedMember:
    account_id: int
    submitted_pass_tier: str


@dataclass(frozen=True)
class SubmittedCandidate:
    candidate_id: int
    members: tuple[SubmittedMember, ...]
    completed_at: datetime | None
    historical: bool = False


class RankingEvidenceError(ValueError):
    """A candidate's exact original-amount facts require reconciliation."""
    def __init__(self, review_orders):
        self.review_orders = review_orders
        super().__init__('候选陪玩的历史接单额或退款归属待核对')


def build_rankable_candidates(submissions, *, as_of):
    """Bind saved pass snapshots to verified local order and evaluation facts.

    Submissions must come from durable server-side candidate snapshots, never
    client-supplied tiers. The future dispatcher owns transaction isolation,
    the receive-time watermark and final qualification recheck.
    """
    from .grab_original_facts import original_amount_facts
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError('排序统计时间必须包含时区')
    submissions = tuple(submissions)
    if any(not isinstance(row, SubmittedCandidate) for row in submissions):
        raise ValueError('候选提交记录无效')
    if len({row.candidate_id for row in submissions}) != len(submissions):
        raise ValueError('候选记录重复')
    ids = set()
    for row in submissions:
        if not isinstance(row.members, tuple) or any(not isinstance(member, SubmittedMember) for member in row.members):
            raise ValueError('候选成员快照无效')
        # Reuse the same strict team, tier and completion-time validation as
        # the final comparison, before any database query.
        placeholders = tuple(MemberFacts(member.account_id, member.submitted_pass_tier, 0, 0, 0)
                             for member in row.members)
        CandidateFacts(row.candidate_id, placeholders, row.completed_at, row.historical)
        if row.completed_at is not None and row.completed_at > as_of:
            raise ValueError('候选完成时间不能晚于排序统计时间')
        ids.update(member.account_id for member in row.members)
    originals = original_amount_facts(ids, as_of=as_of)
    unresolved = {pk: fact.review_order_ids for pk, fact in originals.items() if fact.amount is None}
    if unresolved:
        raise RankingEvidenceError(unresolved)
    ratings = evaluation_facts(ids, as_of=as_of)
    return tuple(CandidateFacts(row.candidate_id, tuple(MemberFacts(member.account_id,
        member.submitted_pass_tier, originals[member.account_id].amount, **ratings[member.account_id])
        for member in row.members), row.completed_at, row.historical) for row in submissions)
