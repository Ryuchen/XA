"""Internal team preparation for an experimental automatic grab round.

There is deliberately no HTTP or live-order entry point. A formed draft is NOT
an eligible candidate: commit-time registration and the cutoff watermark are
not established. Provisional pass values must never be used for ranking.
"""
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
import hashlib
from stores.db import transaction
from club_accounts.models import ClubAccount
from users.models import EscortProfile
from users.pass_rules import lock_pass_config

from .grab_admission import check_candidate
from .models import (Order, OrderGrabPoolSnapshot, OrderGrabRound, OrderGrabRoundSeat,
                     OrderGrabReceiptPassEvidence, OrderGrabReceiptProbe, OrderGrabTeamDraft, OrderGrabTeamDraftMember)


def _atomic():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('抢单组队须在门店事务内处理')


def _positive(value):
    return type(value) is int and value > 0


def first_round(pool_id):
    """Prepare the first round once; this does not activate the order pool."""
    _atomic()
    if not _positive(pool_id):
        raise ValueError('抢单池无效')
    order_id = OrderGrabPoolSnapshot.objects.filter(pk=pool_id).values_list('order_id', flat=True).first()
    if order_id is None:
        raise ValueError('抢单池不存在')
    Order.objects.select_for_update().get(pk=order_id)
    pool = OrderGrabPoolSnapshot.objects.select_for_update().get(pk=pool_id, order_id=order_id)
    if (not pool.pass_snapshot_complete or pool.pass_snapshot_count is None
            or len(list(pool.entry_passes.select_for_update().values_list('pk', flat=True))) != pool.pass_snapshot_count):
        raise ValueError('订单入池通行证快照不完整')
    old = OrderGrabRound.objects.select_for_update().filter(pool=pool, number=1).first()
    if old:
        if (old.opened_at != pool.opened_at or old.deadline != pool.first_deadline
                or old.total_deadline != pool.total_deadline or old.simultaneous_visibility):
            raise ValueError('首轮快照不一致，请先核对')
        return old
    return OrderGrabRound.objects.create(pool=pool, number=1, opened_at=pool.opened_at,
        deadline=pool.first_deadline, total_deadline=pool.total_deadline, simultaneous_visibility=False)


def _round(round_id):
    _atomic()
    if not _positive(round_id):
        raise ValueError('抢单轮次无效')
    order_id = OrderGrabRound.objects.filter(pk=round_id).values_list('pool__order_id', flat=True).first()
    if order_id is None:
        raise ValueError('抢单轮次不存在')
    Order.objects.select_for_update().get(pk=order_id)
    round_ = OrderGrabRound.objects.select_for_update().select_related('pool').get(pk=round_id, pool__order_id=order_id)
    if round_.pool.required_provider_count != 2:
        raise ValueError('当前轮次不支持双陪组队准备')
    from .grab_round_facts import current_round
    return current_round(round_.pool, round_.pk, lock=True)


def _member_tier(account_id):
    """A pre-commit observation only; not the submitted ranking tier."""
    lock_pass_config()
    profile = EscortProfile.objects.select_for_update().filter(account_id=account_id).first()
    if profile is None:
        raise ValueError('陪玩资料不存在')
    now = timezone.now()
    if profile.pass_tier and profile.pass_expires_at and profile.pass_expires_at > now:
        if profile.pass_tier not in ('BLACK', 'GOLD', 'SILVER', 'BRONZE'):
            raise ValueError('陪玩通行证档位异常')
        return profile.pass_tier, profile.pass_expires_at
    return '', None


def _seats(team):
    members = list(team.members.select_for_update().order_by('account_id'))
    if {m.account_id for m in members} != {team.captain_id, team.partner_id} or len(members) != 2:
        raise ValueError('组队成员记录不完整')
    seats = list(team.occupied_seats.select_for_update().values_list('round_id', 'account_id'))
    if team.status == 'INVITING':
        expected = {team.captain_id}
    elif team.status == 'UNVERIFIED':
        expected = {team.captain_id, team.partner_id}
    elif team.status == 'WITHDRAWN':
        if team.withdrawn_by_id not in (team.captain_id, team.partner_id):
            raise ValueError('组队退出人员与成员不一致')
        expected = set()
    elif team.status == 'DISQUALIFIED':
        expected = set()
    else:
        raise ValueError('组队状态无效')
    joined = {m.account_id for m in members if m.joined}
    joined_valid = (joined == expected if team.status not in ('WITHDRAWN', 'DISQUALIFIED')
                    else team.captain_id in joined and joined <= {team.captain_id, team.partner_id})
    if ({account_id for round_id, account_id in seats} != expected
            or any(round_id != team.round_id for round_id, _ in seats) or not joined_valid):
        raise ValueError('组队成员与占位记录不一致')
    return {m.account_id: m for m in members}


def invite(round_id, captain_id, partner_id, request_key):
    """Prepare an invitation, without sending or exposing the order to partner."""
    if (not _positive(captain_id) or not _positive(partner_id) or captain_id == partner_id
            or not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 64):
        raise ValueError('请提供不同的陪玩和有效请求编号')
    round_ = _round(round_id)
    prior = OrderGrabTeamDraft.objects.select_for_update().filter(round=round_, captain_id=captain_id,
                                                                  request_key=request_key).first()
    if prior:
        if prior.partner_id != partner_id:
            raise ValueError('请求编号已用于其他组队')
        _seats(prior)
        return prior
    check_candidate(round_.pool_id, captain_id, round_id=round_.pk)
    if not ClubAccount.objects.filter(pk=partner_id, account_type='PROVIDER', is_active=True, can_login=True).exists():
        raise ValueError('队友账号不可用')
    if OrderGrabRoundSeat.objects.select_for_update().filter(round=round_, account_id__in=[captain_id, partner_id]).exists():
        raise ValueError('陪玩已参加本轮其他队伍，请先退出')
    tier, expires = _member_tier(captain_id)
    team = OrderGrabTeamDraft.objects.create(round=round_, captain_id=captain_id, partner_id=partner_id, request_key=request_key)
    OrderGrabTeamDraftMember.objects.create(team=team, account_id=captain_id, joined=True,
        provisional_pass_tier=tier, provisional_pass_expires_at=expires)
    OrderGrabTeamDraftMember.objects.create(team=team, account_id=partner_id)
    OrderGrabRoundSeat.objects.create(round=round_, account_id=captain_id, team=team)
    return team


def confirm(round_id, team_id, account_id):
    """Prepare partner confirmation; ranking remains blocked pending receipt."""
    if not _positive(team_id) or not _positive(account_id):
        raise ValueError('组队或陪玩账号无效')
    round_ = _round(round_id)
    team = OrderGrabTeamDraft.objects.select_for_update().filter(pk=team_id, round=round_).first()
    if not team or team.partner_id != account_id:
        raise ValueError('只有受邀队友本人可确认')
    members = _seats(team)
    if team.status == 'UNVERIFIED':
        return team
    if team.status != 'INVITING':
        raise ValueError('组队已退出')
    check_candidate(round_.pool_id, team.captain_id, round_id=round_.pk)
    check_candidate(round_.pool_id, account_id, round_id=round_.pk)
    if OrderGrabRoundSeat.objects.select_for_update().filter(round=round_, account_id=account_id).exists():
        raise ValueError('陪玩已参加本轮其他队伍，请先退出')
    tier, expires = _member_tier(account_id)
    member = members[account_id]
    member.joined = True; member.provisional_pass_tier = tier; member.provisional_pass_expires_at = expires
    member.save(update_fields=['joined', 'provisional_pass_tier', 'provisional_pass_expires_at'])
    OrderGrabRoundSeat.objects.create(round=round_, account_id=account_id, team=team)
    team.status = 'UNVERIFIED'; team.save(update_fields=['status', 'updated_at'])
    return team


def withdraw(round_id, team_id, account_id):
    """Free both seats but retain the historic draft and member observations."""
    if not _positive(team_id) or not _positive(account_id):
        raise ValueError('组队或陪玩账号无效')
    round_ = _round(round_id)
    team = OrderGrabTeamDraft.objects.select_for_update().filter(pk=team_id, round=round_).first()
    if not team or account_id not in (team.captain_id, team.partner_id):
        raise ValueError('只能退出自己的组队')
    _seats(team)
    if team.status == 'WITHDRAWN':
        return team
    team.occupied_seats.all().delete()
    team.status = 'WITHDRAWN'; team.withdrawn_by_id = account_id; team.provisional_withdrawal_at = timezone.now()
    team.save(update_fields=['status', 'withdrawn_by', 'provisional_withdrawal_at', 'updated_at'])
    return team


def verified_withdrawal(team, members, receipt, actor_id):
    """Verify the stored withdrawal and its order after all joined members.

    A receipt's expanded interval is inclusive. Overlap does not establish
    command order and must never be guessed from the mutable draft timestamp.
    """
    from .grab_receipt_probe import _effective_state
    if (not receipt or receipt.round_id != team.round_id or receipt.kind != 'WITHDRAW'
            or receipt.actor_id != actor_id or actor_id not in (team.captain_id, team.partner_id)
            or receipt.payload != {'team_id': team.pk} or _effective_state(receipt, None) != 'BEFORE'):
        return False
    previous = {}
    for member in members.values():
        evidence = member.receipt_pass_evidence
        if not member.joined:
            if evidence:
                return False
            continue
        if not evidence:
            return False
        row = evidence.receipt
        captain = member.account_id == team.captain_id
        if (row.round_id != team.round_id or row.actor_id != member.account_id
                or row.kind != ('INVITE' if captain else 'CONFIRM')
                or row.payload != ({'partner_id': team.partner_id} if captain else {'team_id': team.pk})
                or (captain and row.request_key != team.request_key)
                or _effective_state(row, evidence) != 'BEFORE'):
            return False
        previous[member.account_id] = row
    invitation = previous.get(team.captain_id)
    confirmation = previous.get(team.partner_id)
    if not invitation:
        return False
    # These receipts are inserted only after locking the same order row.
    # The confirmation reads the persisted invitation, and a later receipt
    # ID proves the prior transaction released that lock before this one.
    # This proves only this team's command order, not candidate ranking.
    from stores.db import connection
    causal_order = (connection.vendor == 'mysql' and invitation.pk < receipt.pk
                    and (not confirmation or invitation.pk < confirmation.pk < receipt.pk))
    if causal_order:
        return True
    if (confirmation and invitation.upper_observed_at + timedelta(microseconds=invitation.precision_us)
            >= confirmation.lower_at - timedelta(microseconds=confirmation.precision_us)):
        return False
    withdrawal_lower = receipt.lower_at - timedelta(microseconds=receipt.precision_us)
    return all(row.upper_observed_at + timedelta(microseconds=row.precision_us) < withdrawal_lower
               for row in previous.values())


def withdraw_with_receipt(round_id, team_id, receipt_id):
    """Apply a proved withdrawal atomically, freeing seats for another team.

    The intent must already have been committed by record_intent. This is an
    internal step; it does not establish an admission or dispatch watermark.
    """
    from .grab_clock_contract import production_enabled
    if not (getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) or production_enabled()):
        raise ValueError('登记时钟尚未验证')
    if not _positive(team_id) or not _positive(receipt_id):
        raise ValueError('组队或退出登记无效')
    round_ = _round(round_id)
    team = OrderGrabTeamDraft.objects.select_for_update().filter(pk=team_id, round=round_).first()
    if not team:
        raise ValueError('本轮组队不存在')
    members = _seats(team)
    receipt = OrderGrabReceiptProbe.objects.select_for_update().filter(pk=receipt_id, round=round_).first()
    if not receipt or not verified_withdrawal(team, members, receipt, receipt.actor_id):
        raise ValueError('退出登记或先前组队证据无法核对')
    if team.status == 'WITHDRAWN':
        if team.withdrawal_receipt_id != receipt.pk or team.withdrawn_by_id != receipt.actor_id:
            raise ValueError('组队已有其他退出记录')
        return team
    team.occupied_seats.all().delete()
    team.status = 'WITHDRAWN'
    team.withdrawn_by_id = receipt.actor_id
    team.provisional_withdrawal_at = timezone.now()
    team.withdrawal_receipt = receipt
    team.save(update_fields=['status', 'withdrawn_by', 'provisional_withdrawal_at',
                             'withdrawal_receipt', 'updated_at'])
    return team


def attach_pass_evidence(round_id, team_id, receipt_id):
    """Link a member's proved receipt to a draft, without making a candidate.

    Registration-time qualification, command ordering and the final watermark
    remain unresolved. The draft stays UNVERIFIED even when both links exist.
    """
    from .grab_clock_contract import production_enabled
    if not (getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) or production_enabled()):
        raise ValueError('登记时钟尚未验证')
    if not _positive(team_id) or not _positive(receipt_id):
        raise ValueError('组队或登记证据无效')
    round_ = _round(round_id)
    team = OrderGrabTeamDraft.objects.select_for_update().filter(pk=team_id, round=round_).first()
    if not team or team.status == 'WITHDRAWN':
        raise ValueError('组队不存在或已退出')
    members = _seats(team)
    receipt = OrderGrabReceiptProbe.objects.select_for_update().filter(pk=receipt_id, round=round_).first()
    if not receipt or receipt.state != 'BEFORE':
        raise ValueError('登记时间尚未核验')
    if receipt.kind == 'INVITE':
        valid = (receipt.actor_id == team.captain_id and receipt.request_key == team.request_key
                 and receipt.payload == {'partner_id': team.partner_id})
    elif receipt.kind == 'CONFIRM':
        valid = (receipt.actor_id == team.partner_id and receipt.payload == {'team_id': team.pk}
                 and team.status == 'UNVERIFIED')
    else:
        valid = False
    if not valid or receipt.actor_id not in members or not members[receipt.actor_id].joined:
        raise ValueError('登记证据与组队成员不匹配')
    from .grab_receipt_probe import _effective_state, _payload, classify_bounds, classify_submitted_tier
    expected = hashlib.sha256(_payload(receipt.kind, receipt.payload, receipt.actor_id).encode('ascii')).hexdigest()
    if (receipt.fingerprint != expected or classify_bounds(lower=receipt.lower_at, upper=receipt.upper_observed_at,
            precision_us=receipt.precision_us, deadline=round_.deadline) != ('BEFORE', '')):
        raise ValueError('登记时间或请求内容需要核对')
    evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=receipt).first()
    if not evidence or evidence.state != 'KNOWN':
        raise ValueError('登记时通行证档位需要核对')
    inferred = classify_submitted_tier(raw_tier=evidence.raw_tier, expires_at=evidence.raw_expires_at,
        lower=receipt.lower_at, upper=receipt.upper_observed_at, precision_us=receipt.precision_us)
    if inferred != ('KNOWN', evidence.submitted_tier, ''):
        raise ValueError('登记时通行证证据不一致')
    if not EscortProfile.objects.select_for_update().filter(pk=evidence.profile_id_snapshot,
            user_id=evidence.user_id_snapshot, account_id=receipt.actor_id).exists():
        raise ValueError('陪玩账号或原资料已变化，请先核对')
    if _effective_state(receipt, evidence) != 'BEFORE':
        raise ValueError('登记时间或身份凭证需要核对')
    member = members[receipt.actor_id]
    if member.receipt_pass_evidence_id:
        if member.receipt_pass_evidence_id != evidence.pk:
            raise ValueError('该成员已有其他登记证据')
        return member
    if OrderGrabTeamDraftMember.objects.select_for_update().filter(receipt_pass_evidence=evidence).exists():
        raise ValueError('登记证据已绑定其他组队')
    member.receipt_pass_evidence = evidence
    member.save(update_fields=['receipt_pass_evidence'])
    return member
