"""Experimental two-sided commit observations for future grab commands.

These probes only record an intent and a conservative cutoff classification;
they do not validate eligibility, apply the intent, reserve a candidate, or
allow a round to finalize. No live endpoint calls this module. Before use,
the deployment must establish trustworthy database time, clock monitoring,
connection/failover behavior and crash recovery for every store.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
import hashlib
import json
from itertools import islice

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Q
from stores.db import connection, transaction
from club_accounts.models import ClubAccount
from club_accounts.provider_lifecycle import ProviderLifecycleError
from users.models import EscortProfile
from users.pass_rules import lock_pass_config

from .models import (Order, OrderGrabReceiptPassEvidence, OrderGrabReceiptProbe, OrderGrabRound,
                     OrderGrabTeamDraft)


@dataclass(frozen=True)
class ProbeOutcome:
    receipt_id: int
    state: str
    replayed: bool = False


class GrabRequestConflict(ValueError):
    pass


def actor_membership_evidence_present(probe):
    return (probe.actor_cycle_guarded and type(probe.actor_business_cycle) is int
            and probe.actor_business_cycle > 0 and probe.kind in ('SINGLE', 'INVITE', 'CONFIRM'))


def team_invitation_cycles_match(team, receipt, *, captain_cycle, partner_cycle):
    """Match both current memberships to the original invitation evidence."""
    return bool(receipt and actor_membership_evidence_present(receipt)
        and receipt.kind == 'INVITE' and receipt.round_id == team.round_id
        and receipt.actor_id == team.captain_id
        and isinstance(receipt.payload, dict) and receipt.payload.get('partner_id') == team.partner_id
        and type(receipt.invitee_business_cycle) is int and receipt.invitee_business_cycle > 0
        and receipt.actor_business_cycle == captain_cycle
        and receipt.invitee_business_cycle == partner_cycle)


def locked_actor_membership_matches(probe):
    """Evaluate the submitted actor cycle; caller must hold its lifecycle lock."""
    from stores.context import current_database, current_store
    connection = transaction.get_connection()
    store = current_store()
    if (store is None or store.database_alias != current_database() or connection.alias != store.database_alias
            or probe._state.db != connection.alias or not connection.in_atomic_block
            or not actor_membership_evidence_present(probe)):
        return False
    return ClubAccount.objects.filter(pk=probe.actor_id, account_type='PROVIDER', is_active=True,
        can_login=True, license_membership_active=True, business_cycle=probe.actor_business_cycle).exists()


def _effective_state(probe, evidence, *, verified_profiles=None):
    """Recheck immutable command, timing and pass facts on every read."""
    if probe.state == 'REVIEW':
        return 'REVIEW'
    try:
        canonical = _payload(probe.kind, probe.payload, probe.actor_id)
        if probe.fingerprint != hashlib.sha256(canonical.encode('ascii')).hexdigest():
            return 'REVIEW'
        if probe.state in ('BEFORE', 'AFTER'):
            from .grab_clock_contract import production_enabled, round_identity_valid, valid_bounds
            production_clock = production_enabled()
            if production_clock or not getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False):
                if (not round_identity_valid(probe.round) or not valid_bounds(
                        probe.lower_at, probe.upper_observed_at, probe.clock_evidence,
                        identity=probe.round.pool.order.grab_opening_intent.database_identity)):
                    return 'REVIEW'
            cutoff = probe.cutoff_at or probe.round.deadline
            if probe.cutoff_at is not None:
                expected_cutoff = (probe.round.total_deadline if probe.round_phase == 'PARKED'
                                   else probe.round.deadline if probe.round_phase == 'OPEN' else None)
                if cutoff != expected_cutoff:
                    return 'REVIEW'
                if probe.kind == 'SINGLE':
                    admission = getattr(probe, 'single_admission', None)
                    if not admission or admission.account_id != probe.actor_id or admission.round_id != probe.round_id or admission.phase != probe.round_phase:
                        return 'REVIEW'
            if production_clock:
                from .grab_round_opening import classify_window, opening_bounds
                state, reason = classify_window(lower=probe.lower_at, upper=probe.upper_observed_at,
                    precision_us=probe.precision_us, round_=probe.round, phase=probe.round_phase,
                    cancellation=probe.kind == 'BOSS_CANCEL')
                if (not probe.opening_prepared_observed and
                        probe.lower_at - timedelta(microseconds=probe.precision_us) < opening_bounds(probe.round)[1]):
                    return 'REVIEW'
            else:
                state, reason = classify_bounds(lower=probe.lower_at, upper=probe.upper_observed_at,
                    precision_us=probe.precision_us, deadline=cutoff,
                    cancellation=probe.kind == 'BOSS_CANCEL')
            if (state != probe.state or reason or not probe.round.opened_at
                    or probe.lower_at - timedelta(microseconds=probe.precision_us) < probe.round.opened_at):
                return 'REVIEW'
        elif probe.state == 'WAIT_OPENING':
            if probe.upper_observed_at is None or probe.review_reason:
                return 'REVIEW'
        elif probe.state != 'PENDING' or probe.upper_observed_at is not None:
            return 'REVIEW'
    except (ValueError, TypeError, AttributeError, DatabaseError):
        return 'REVIEW'
    if probe.kind == 'BOSS_CANCEL':
        if (probe.round.pool.order.customer_account_id != probe.actor_id
                or probe.actor.account_type != 'BOSS'):
            return 'REVIEW'
    elif probe.actor.account_type != 'PROVIDER':
        return 'REVIEW'
    if probe.kind not in ('SINGLE', 'INVITE', 'CONFIRM'):
        return 'PENDING' if probe.state == 'WAIT_OPENING' else probe.state
    expected = {'PENDING': 'PENDING', 'WAIT_OPENING': 'PENDING', 'BEFORE': 'KNOWN', 'AFTER': 'LATE', 'REVIEW': 'REVIEW'}
    if (not evidence or evidence.state != expected.get(probe.state)
            or (evidence.state == 'KNOWN' and evidence.submitted_tier is None)):
        return 'REVIEW'
    if evidence.state == 'KNOWN':
        try:
            derived = classify_submitted_tier(raw_tier=evidence.raw_tier,
                expires_at=evidence.raw_expires_at, lower=probe.lower_at,
                upper=probe.upper_observed_at, precision_us=probe.precision_us)
        except ValueError:
            return 'REVIEW'
        if derived != ('KNOWN', evidence.submitted_tier, ''):
            return 'REVIEW'
        identity = (evidence.profile_id_snapshot, evidence.user_id_snapshot, probe.actor_id)
        if verified_profiles is None:
            found = EscortProfile.objects.filter(pk=identity[0], user_id=identity[1], account_id=identity[2]).exists()
        else:
            found = identity in verified_profiles
        if not found:
            return 'REVIEW'
    return 'PENDING' if probe.state == 'WAIT_OPENING' else probe.state


def classify_bounds(*, lower, upper, precision_us, deadline, cancellation=False):
    """Classify only what follows from trusted before/after DB observations.

    Each sample has up to ``precision_us`` of resolution uncertainty. ``upper`` is the DB clock
    sampled after the commit returned; it is not the exact commit timestamp.
    A time boundary intersecting the interval, a missing upper bound, or a
    backwards clock always requires review.
    """
    if (not isinstance(lower, datetime) or lower.utcoffset() is None
            or not isinstance(deadline, datetime) or deadline.utcoffset() is None
            or (upper is not None and (not isinstance(upper, datetime) or upper.utcoffset() is None))
            or type(precision_us) is not int or not 1 <= precision_us <= 1_000_000
            or type(cancellation) is not bool):
        raise ValueError('登记时间证据无效')
    if upper is None:
        return 'REVIEW', 'ACK_MISSING'
    if upper < lower:
        return 'REVIEW', 'CLOCK_BACKWARDS'
    # Expand both observations: SQLite may round milliseconds, while the
    # MySQL sample has microsecond precision. Neither displayed value is an
    # exact physical commit timestamp.
    quantum = timedelta(microseconds=precision_us)
    # Candidate submissions must be strictly before the cutoff. A boss
    # cancellation at the cutoff is allowed, so equality is sufficient only
    # for cancellation. The expanded upper bound is inclusive.
    if (upper + quantum <= deadline if cancellation else upper + quantum < deadline):
        return 'BEFORE', ''
    if (lower - quantum > deadline if cancellation else lower - quantum >= deadline):
        return 'AFTER', ''
    return 'REVIEW', 'CROSSED_CUTOFF'


def classify_submitted_tier(*, raw_tier, expires_at, lower, upper, precision_us):
    """Infer the pass only when it cannot expire within the commit interval."""
    tiers = ('', 'BLACK', 'GOLD', 'SILVER', 'BRONZE')
    if (raw_tier not in tiers or not isinstance(lower, datetime) or lower.utcoffset() is None
            or not isinstance(upper, datetime) or upper.utcoffset() is None or upper < lower
            or type(precision_us) is not int or not 1 <= precision_us <= 1_000_000
            or (expires_at is not None and (not isinstance(expires_at, datetime) or expires_at.utcoffset() is None))):
        raise ValueError('通行证时间证据无效')
    if not raw_tier or expires_at is None:
        return 'KNOWN', '', ''
    quantum = timedelta(microseconds=precision_us)
    if expires_at <= lower - quantum:
        return 'KNOWN', '', ''
    if expires_at > upper + quantum:
        return 'KNOWN', raw_tier, ''
    return 'REVIEW', None, 'PASS_EXPIRY_UNCERTAIN'


def _db_clock():
    """Sample current tenant DB UTC time with its known truncation precision."""
    if connection.vendor == 'mysql':
        sql, precision = 'SELECT UTC_TIMESTAMP(6)', 1
    elif connection.vendor == 'sqlite':
        sql, precision = "SELECT strftime('%Y-%m-%d %H:%M:%f', 'now')", 1000
    else:
        raise ValueError('当前数据库尚无可核验的登记时钟')
    with connection.cursor() as cursor:
        cursor.execute(sql)
        value = cursor.fetchone()[0]
        driver = connection.connection
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError('门店数据库时钟无效')
    if value.utcoffset() is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    else:
        value = value.astimezone(dt_timezone.utc)
    return value, precision, driver


def _payload(kind, payload, actor_id):
    if not isinstance(payload, dict):
        raise ValueError('登记内容无效')
    keys = {'SINGLE': set(), 'INVITE': {'partner_id'}, 'CONFIRM': {'team_id'},
            'WITHDRAW': {'team_id'}, 'BOSS_CANCEL': set()}
    if kind == 'BOSS_CANCEL' and set(payload) == {'reason'}:
        if not isinstance(payload['reason'], str) or len(payload['reason']) > 255:
            raise ValueError('取消原因无效')
    elif kind not in keys or set(payload) != keys[kind]:
        raise ValueError('登记类型或内容无效')
    if payload and kind != 'BOSS_CANCEL':
        value = next(iter(payload.values()))
        if type(value) is not int or value <= 0 or (kind == 'INVITE' and value == actor_id):
            raise ValueError('登记对象无效')
    return json.dumps({'kind': kind, 'payload': payload}, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _lock_round(round_id):
    order_id = OrderGrabRound.objects.filter(pk=round_id).values_list('pool__order_id', flat=True).first()
    if order_id is None:
        raise ValueError('抢单轮次不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    round_ = OrderGrabRound.objects.select_for_update().get(pk=round_id, pool__order_id=order_id)
    return order, round_


def record_intent(round_id, actor_id, kind, request_key, payload, *, single_flow=False, team_flow=False,
                  actor_snapshot=None):
    """Durably record an intent, then independently record a post-commit bound.

    The request must already be authenticated by the future caller. This
    method verifies store-local identity and resource ownership but is not a
    substitute for the full eligibility check. It must own both transactions;
    callers cannot wrap it in an outer atomic block.
    """
    if transaction.get_connection().in_atomic_block or not transaction.get_autocommit():
        raise RuntimeError('登记证据必须使用独立的最外层事务')
    if (type(round_id) is not int or round_id <= 0 or type(actor_id) is not int or actor_id <= 0
            or not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 64):
        raise ValueError('登记身份或请求编号无效')
    canonical = _payload(kind, payload, actor_id)
    fingerprint = hashlib.sha256(canonical.encode('ascii')).hexdigest()
    lifecycle_accounts = []
    lifecycle_team_ids = None
    order_invite = team_flow and kind == 'INVITE'
    team_cycle_guard = team_flow and kind in ('INVITE', 'CONFIRM')
    lifecycle_guard = team_cycle_guard or (single_flow and kind == 'SINGLE')
    if lifecycle_guard:
        if (not isinstance(actor_snapshot, ClubAccount) or actor_snapshot.pk != actor_id
                or actor_snapshot._state.db != transaction.get_connection().alias):
            raise ValueError('陪玩身份状态无法核验，请重新登录')
        lifecycle_accounts = [actor_snapshot]
        other_id = None
        if kind == 'INVITE':
            other_id = payload['partner_id']
        elif kind == 'CONFIRM':
            lifecycle_team_ids = (OrderGrabTeamDraft.objects.filter(pk=payload['team_id'], round_id=round_id)
                                  .values_list('captain_id', 'partner_id').first())
            if not lifecycle_team_ids or lifecycle_team_ids[1] != actor_id:
                raise ValueError('组队与登记人不匹配')
            other_id = lifecycle_team_ids[0]
        if other_id is not None:
            other = ClubAccount.objects.filter(pk=other_id, account_type='PROVIDER').first()
            if other is None and not order_invite:
                raise ValueError('双陪队友不可用')
            if other is not None:
                lifecycle_accounts.append(other)
    if single_flow and kind == 'SINGLE':
        from .services import ensure_provider_dispatch_locks
        ensure_provider_dispatch_locks([actor_id])
    if team_flow and kind in ('INVITE', 'CONFIRM'):
        from .services import ensure_provider_dispatch_locks
        participant_ids = [actor_id]
        if kind == 'CONFIRM':
            captain = OrderGrabTeamDraft.objects.filter(pk=payload['team_id']).values_list('captain_id', flat=True).first()
            if captain:
                participant_ids.append(captain)
        ensure_provider_dispatch_locks(participant_ids)
    with transaction.atomic(durable=True):
        if lifecycle_guard:
            from club_accounts.provider_lifecycle import lock_provider_operations
            try:
                lock_provider_operations(lifecycle_accounts)
            except ProviderLifecycleError as exc:
                raise ValueError('陪玩状态暂不可核验，请稍后重试') from exc
        order, round_ = _lock_round(round_id)
        pool = round_.pool
        actor = ClubAccount.objects.filter(pk=actor_id, is_active=True, can_login=True).first()
        if actor is None:
            raise ValueError('本店账号不可用')
        if kind == 'BOSS_CANCEL':
            if actor.account_type != 'BOSS' or order.customer_account_id != actor_id:
                raise ValueError('只能登记本人订单取消')
        elif actor.account_type != 'PROVIDER':
            raise ValueError('仅陪玩可登记抢单意向')
        matching = OrderGrabReceiptProbe.objects.select_for_update().filter(actor_id=actor_id, request_key=request_key)
        if order_invite:
            # The order lock serializes invitations with changes to the active round.
            matches = list(matching.filter(Q(round=round_) | Q(round__pool_id=pool.pk, kind='INVITE'))
                           .order_by('pk')[:2])
            if len(matches) > 1:
                raise GrabRequestConflict('请求编号关联多个组队登记，请核对原请求')
            old = matches[0] if matches else None
        else:
            old = matching.filter(round=round_).first()
        if old:
            if old.fingerprint != fingerprint or old.kind != kind or old.payload != payload:
                conflict = GrabRequestConflict if order_invite else ValueError
                raise conflict('请求编号已用于其他登记')
            if order_invite:
                from club_accounts.provider_lifecycle import provider_membership_current
                if (len(lifecycle_accounts) != 2 or not actor_membership_evidence_present(old)
                        or old.actor_business_cycle != lifecycle_accounts[0].business_cycle
                        or old.invitee_business_cycle != lifecycle_accounts[1].business_cycle
                        or not all(provider_membership_current(account) for account in lifecycle_accounts)):
                    raise GrabRequestConflict('原组队成员身份已变化，请重新组队')
            evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=old).first()
            return ProbeOutcome(old.pk, _effective_state(old, evidence), True)
        if order_invite and len(lifecycle_accounts) != 2:
            raise ValueError('双陪队友不可用')
        if lifecycle_guard:
            from club_accounts.provider_lifecycle import provider_membership_current
            if not all(provider_membership_current(account) for account in lifecycle_accounts):
                raise ValueError('陪玩门店身份已变化，请重新选择')
        from .grab_round_facts import current_round
        round_ = current_round(pool, round_.pk, lock=True)
        phase, cutoff = 'OPEN', round_.deadline
        if single_flow:
            from .models import OrderGrabSingleRoundState
            state_row = OrderGrabSingleRoundState.objects.select_for_update().filter(round=round_).first()
            if not state_row or state_row.status not in ('OPEN', 'PARKED') or kind not in ('SINGLE', 'BOSS_CANCEL'):
                raise ValueError('抢单轮次已结束或正在核对')
            phase = state_row.status
            cutoff = round_.total_deadline if phase == 'PARKED' else round_.deadline
        if team_flow:
            from .models import OrderGrabTeamRoundState
            state_row = OrderGrabTeamRoundState.objects.select_for_update().filter(round=round_).first()
            if not state_row or state_row.status not in ('OPEN', 'PARKED') or kind not in ('INVITE', 'CONFIRM', 'WITHDRAW', 'BOSS_CANCEL'):
                raise ValueError('双陪抢单轮次已结束或正在核对')
            phase = state_row.status
            cutoff = round_.total_deadline if phase == 'PARKED' else round_.deadline
        if (single_flow or team_flow) and kind == 'BOSS_CANCEL':
            # The buyer can cancel until the total wait expires while the
            # order is unassigned. Keep this receipt out of the OPEN seal.
            phase, cutoff = 'PARKED', round_.total_deadline
        elif (single_flow or team_flow) and phase == 'OPEN':
            from .grab_cutoff_watermark import enabled as watermark_enabled
            if watermark_enabled():
                from .models import OrderGrabCutoffWatermark
                if OrderGrabCutoffWatermark.objects.filter(round=round_).exists():
                    # The OPEN set is immutable; later real requests belong
                    # to the remaining total-wait period, not that set.
                    phase, cutoff = 'PARKED', round_.total_deadline
        if (order.status != Order.Status.PENDING or order.payment_status != Order.PaymentStatus.PAID
                or order.provider_id or order.provider_account_id or order.requires_boss_selection):
            raise ValueError('订单不在待抢单状态')
        if kind == 'SINGLE' and round_.pool.required_provider_count != 1:
            raise ValueError('本订单需要双陪')
        if kind == 'INVITE':
            if round_.pool.required_provider_count != 2 or not ClubAccount.objects.filter(
                    pk=payload['partner_id'], account_type='PROVIDER', is_active=True, can_login=True).exists():
                raise ValueError('双陪队友不可用')
        team = None
        if kind in ('CONFIRM', 'WITHDRAW'):
            team = OrderGrabTeamDraft.objects.filter(pk=payload['team_id'], round=round_).first()
            if (not team or (kind == 'CONFIRM' and team.partner_id != actor_id)
                    or (kind == 'WITHDRAW' and actor_id not in (team.captain_id, team.partner_id))):
                raise ValueError('组队与登记人不匹配')
            if lifecycle_team_ids and (team.captain_id, team.partner_id) != lifecycle_team_ids:
                raise ValueError('组队成员已变化，请刷新后重试')
        if team_flow and kind in ('INVITE', 'CONFIRM'):
            from .services import lock_provider_dispatch
            lock_provider_dispatch([actor_id] if kind == 'INVITE' else [actor_id, team.captain_id])
        profile = None
        if kind in ('SINGLE', 'INVITE', 'CONFIRM'):
            if single_flow:
                from .services import lock_provider_dispatch
                from .models import OrderGrabSingleAdmission
                lock_provider_dispatch([actor_id])
                if OrderGrabSingleAdmission.objects.filter(round=round_, account_id=actor_id, phase=phase,
                                                           status='ACTIVE').exists():
                    raise ValueError('本轮已提交抢单，请等待结果')
            # Pass purchase/refund take the same lock before changing a
            # profile. Preserve the raw expiry, not a pre-commit active tier.
            lock_pass_config()
            profile = EscortProfile.objects.select_for_update().filter(account_id=actor_id).first()
            if not profile or profile.pass_tier not in ('', 'BLACK', 'GOLD', 'SILVER', 'BRONZE'):
                raise ValueError('陪玩通行证资料无法核对')
            if single_flow:
                from .grab_admission import check_candidate
                check_candidate(pool.pk, actor_id, round_id=round_.pk)
        from .grab_clock_contract import TOLERANCE_NS, production_enabled, round_identity_valid, sample
        production_clock = production_enabled()
        lower_sample = None
        opening_prepared = False
        if production_clock:
            lower, driver, lower_sample = sample()
            precision = TOLERANCE_NS // 1000
            if not round_identity_valid(round_, identity=lower_sample['database_identity'], allow_prepared=True):
                raise ValueError('抢单轮次数据库身份待核对')
            from .models import OrderGrabRoundOpeningEvidence
            opening_prepared = OrderGrabRoundOpeningEvidence.objects.filter(
                round=round_, state='PREPARED').exists()
        else:
            lower, precision, driver = _db_clock()
        if team_flow and kind in ('INVITE', 'CONFIRM'):
            from .grab_team_draft import invite, confirm
            if kind == 'INVITE':
                team = invite(round_.pk, actor_id, payload['partner_id'], request_key)
            else:
                captain_member = team.members.filter(account_id=team.captain_id).select_related('receipt_pass_evidence__receipt').first()
                captain_evidence = captain_member.receipt_pass_evidence if captain_member else None
                captain_state = _effective_state(captain_evidence.receipt, captain_evidence) if captain_evidence else None
                if captain_state != 'BEFORE' and not (captain_state == 'PENDING' and captain_evidence
                        and captain_evidence.receipt.state == 'WAIT_OPENING'):
                    raise ValueError('队长邀请尚未完成登记')
                cycles = {account.pk: account.business_cycle for account in lifecycle_accounts}
                if not team_invitation_cycles_match(team, captain_evidence.receipt,
                        captain_cycle=cycles.get(team.captain_id), partner_cycle=cycles.get(team.partner_id)):
                    raise ValueError('邀请时的陪玩身份已变化，请重新组队')
                # A different request key must not replace the receipt that
                # already proves this partner's completed confirmation.
                if team.status == 'UNVERIFIED':
                    raise ValueError('队友已确认本次组队，请查看原请求结果')
                team = confirm(round_.pk, team.pk, actor_id)
        probe = OrderGrabReceiptProbe.objects.create(round=round_, actor=actor, request_key=request_key,
            kind=kind, payload=payload, fingerprint=fingerprint, lower_at=lower, precision_us=precision,
            actor_business_cycle=actor.business_cycle if kind in ('SINGLE', 'INVITE', 'CONFIRM') else None,
            invitee_business_cycle=(next(item.business_cycle for item in lifecycle_accounts
                if item.pk == payload['partner_id']) if team_cycle_guard and kind == 'INVITE' else None),
            actor_cycle_guarded=lifecycle_guard or team_cycle_guard,
            cutoff_at=cutoff if single_flow or team_flow else None, round_phase=phase,
            opening_prepared_observed=opening_prepared,
            clock_evidence={'lower': lower_sample} if lower_sample else None)
        if single_flow and kind == 'SINGLE':
            from .models import OrderGrabSingleAdmission
            last = (OrderGrabSingleAdmission.objects.filter(round=round_, account=actor, phase=phase)
                    .order_by('-attempt').values_list('attempt', flat=True).first() or 0)
            OrderGrabSingleAdmission.objects.create(receipt=probe, round=round_, account=actor, phase=phase,
                                                    attempt=last + 1)
        if profile:
            evidence = OrderGrabReceiptPassEvidence.objects.create(receipt=probe, profile_id_snapshot=profile.pk,
                user_id_snapshot=profile.user_id, raw_tier=profile.pass_tier, raw_expires_at=profile.pass_expires_at)
            if team_flow and team is not None and kind in ('INVITE', 'CONFIRM'):
                member = team.members.get(account_id=actor_id)
                member.receipt_pass_evidence = evidence
                member.save(update_fields=['receipt_pass_evidence'])
        receipt_id, deadline = probe.pk, cutoff

    try:
        if production_clock:
            upper, after_driver, upper_sample = sample()
            after_precision = TOLERANCE_NS // 1000
        else:
            upper, after_precision, after_driver = _db_clock()
            upper_sample = None
    except (DatabaseError, ValueError):
        # The first commit succeeded, but no trustworthy upper observation was
        # stored. A replay must never fabricate a later bound as the original.
        return ProbeOutcome(receipt_id, 'PENDING')
    if after_driver is not driver or after_precision != precision:
        state, reason = 'REVIEW', 'CONNECTION_CHANGED'
    elif production_clock:
        from .grab_clock_contract import valid_bounds
        if not valid_bounds(lower, upper, {'lower': lower_sample, 'upper': upper_sample},
                            identity=lower_sample['database_identity']):
            state, reason = 'REVIEW', 'CLOCK_DISCONTINUITY'
        else:
            from .grab_round_opening import classify_window, opening_bounds
            try:
                state, reason = classify_window(lower=lower, upper=upper, precision_us=precision,
                    round_=round_, phase=phase, cancellation=kind == 'BOSS_CANCEL')
                opening_upper = opening_bounds(round_)[1]
            except ValueError:
                from .grab_round_opening import provisional_opening_lower
                try:
                    provisional_opening_lower(round_)
                    state, reason, opening_upper = 'WAIT_OPENING', '', None
                except ValueError:
                    state, reason, opening_upper = 'REVIEW', 'OPENING_EVIDENCE_UNAVAILABLE', None
            if (state in ('BEFORE', 'AFTER') and not opening_prepared
                    and lower - timedelta(microseconds=precision) < opening_upper):
                state, reason = 'REVIEW', 'CROSSED_OPENING'
    elif not getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False):
        # Merely reading the same server clock twice does not prove it did not
        # step backward or forward during the commit. No live clock contract
        # has yet been implemented; the override is for isolated tests only.
        state, reason = 'REVIEW', 'CLOCK_CONTRACT_UNVERIFIED'
    else:
        state, reason = classify_bounds(lower=lower, upper=upper, precision_us=precision,
                                        deadline=deadline, cancellation=kind == 'BOSS_CANCEL')
        # A pre-commit sample close to opening cannot prove that registration
        # completed after the pool opened. Preserve the uncertainty on the
        # durable receipt, rather than persisting a misleading BEFORE state.
        if (state in ('BEFORE', 'AFTER')
                and lower - timedelta(microseconds=precision) < round_.opened_at):
            state, reason = 'REVIEW', 'CROSSED_OPENING'
    try:
        with transaction.atomic(durable=True):
            if lifecycle_guard:
                # This transaction only finalizes the timing evidence for an
                # already committed intent. Serialize with lifecycle changes,
                # but do not reject that in-flight evidence on a changed status.
                from club_accounts.provider_lifecycle import lock_provider_operations
                lock_provider_operations(lifecycle_accounts)
            _lock_round(round_id)
            saved = OrderGrabReceiptProbe.objects.select_for_update().get(pk=receipt_id, round_id=round_id)
            if saved.state != 'PENDING':
                evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=saved).first()
                return ProbeOutcome(saved.pk, _effective_state(saved, evidence))
            evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=saved).first()
            if saved.kind in ('SINGLE', 'INVITE', 'CONFIRM'):
                if evidence is None or evidence.state != 'PENDING':
                    state, reason = 'REVIEW', 'PASS_EVIDENCE_MISSING'
                elif state == 'BEFORE':
                    try:
                        evidence.state, evidence.submitted_tier, evidence.review_reason = classify_submitted_tier(
                            raw_tier=evidence.raw_tier, expires_at=evidence.raw_expires_at,
                            lower=lower, upper=upper, precision_us=precision)
                    except ValueError:
                        evidence.state, evidence.submitted_tier, evidence.review_reason = 'REVIEW', None, 'PASS_FACT_INVALID'
                elif state == 'AFTER':
                    evidence.state = 'LATE'
                elif state == 'WAIT_OPENING':
                    pass
                else:
                    evidence.state, evidence.review_reason = 'REVIEW', 'RECEIPT_UNCERTAIN'
                if evidence:
                    evidence.save(update_fields=['state', 'submitted_tier', 'review_reason'])
            saved.upper_observed_at = upper
            saved.state, saved.review_reason = state, reason
            if production_clock:
                saved.clock_evidence = {'lower': lower_sample, 'upper': upper_sample}
            saved.save(update_fields=['upper_observed_at', 'state', 'review_reason', 'clock_evidence', 'updated_at'])
            if state == 'AFTER' and saved.kind in ('INVITE', 'CONFIRM'):
                _release_late_team_receipt(saved)
            if state == 'BEFORE' and saved.kind == 'WITHDRAW':
                from .grab_team_draft import withdraw_with_receipt
                try:
                    withdraw_with_receipt(round_id, saved.payload.get('team_id'), saved.pk)
                except ValueError:
                    saved.state, saved.review_reason = 'REVIEW', 'WITHDRAWAL_ORDER_UNCERTAIN'
                    saved.save(update_fields=['state', 'review_reason', 'updated_at'])
            if state == 'WAIT_OPENING':
                from .models import OrderGrabRoundOpeningEvidence
                if OrderGrabRoundOpeningEvidence.objects.filter(round_id=round_id, state='OBSERVED').exists():
                    from .grab_round_opening import _resume_waiting
                    transaction.on_commit(lambda: _resume_waiting(round_id), robust=True)
            outcome_state = _effective_state(saved, evidence)
    except (DatabaseError, ProviderLifecycleError):
        # The update might itself have committed before its acknowledgement
        # was lost. Return unresolved and force a read by request key.
        return ProbeOutcome(receipt_id, 'PENDING')
    return ProbeOutcome(receipt_id, outcome_state)


def finalize_opening_waiters(round_id):
    """Reclassify saved receipt bounds after activation evidence is durable."""
    from .grab_clock_contract import database_id, round_identity_valid, valid_bounds
    from .grab_round_opening import classify_window
    ids = list(OrderGrabReceiptProbe.objects.filter(round_id=round_id, state='WAIT_OPENING')
               .order_by('pk').values_list('pk', flat=True))
    for receipt_id in ids:
        try:
            with transaction.atomic():
                _order, round_ = _lock_round(round_id)
                saved = OrderGrabReceiptProbe.objects.select_for_update().filter(
                    pk=receipt_id, round_id=round_id, state='WAIT_OPENING').first()
                if not saved:
                    continue
                evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=saved).first()
                try:
                    identity = database_id()
                    if (not round_identity_valid(round_, identity=identity) or not saved.opening_prepared_observed
                            or not saved.upper_observed_at or not valid_bounds(saved.lower_at, saved.upper_observed_at,
                                                                             saved.clock_evidence, identity=identity)):
                        raise ValueError('登记或开池提交证据异常')
                    state, reason = classify_window(lower=saved.lower_at, upper=saved.upper_observed_at,
                        precision_us=saved.precision_us, round_=round_, phase=saved.round_phase,
                        cancellation=saved.kind == 'BOSS_CANCEL')
                except (ValueError, TypeError):
                    state, reason = 'REVIEW', 'OPENING_EVIDENCE_UNAVAILABLE'
                if saved.kind in ('SINGLE', 'INVITE', 'CONFIRM'):
                    if not evidence or evidence.state != 'PENDING':
                        state, reason = 'REVIEW', 'PASS_EVIDENCE_MISSING'
                    elif state == 'BEFORE':
                        try:
                            evidence.state, evidence.submitted_tier, evidence.review_reason = classify_submitted_tier(
                                raw_tier=evidence.raw_tier, expires_at=evidence.raw_expires_at,
                                lower=saved.lower_at, upper=saved.upper_observed_at, precision_us=saved.precision_us)
                        except ValueError:
                            evidence.state, evidence.submitted_tier, evidence.review_reason = 'REVIEW', None, 'PASS_FACT_INVALID'
                    elif state == 'AFTER':
                        evidence.state = 'LATE'
                    else:
                        evidence.state, evidence.review_reason = 'REVIEW', 'RECEIPT_UNCERTAIN'
                    if evidence:
                        evidence.save(update_fields=['state', 'submitted_tier', 'review_reason'])
                saved.state, saved.review_reason = state, reason
                saved.save(update_fields=['state', 'review_reason', 'updated_at'])
                if state == 'BEFORE' and saved.kind == 'WITHDRAW':
                    from .grab_team_draft import withdraw_with_receipt
                    try:
                        withdraw_with_receipt(round_id, saved.payload.get('team_id'), saved.pk)
                    except ValueError:
                        saved.state, saved.review_reason = 'REVIEW', 'WITHDRAWAL_ORDER_UNCERTAIN'
                        saved.save(update_fields=['state', 'review_reason', 'updated_at'])
                if state == 'BEFORE' and saved.kind == 'INVITE':
                    team = OrderGrabTeamDraft.objects.filter(round=round_, captain_id=saved.actor_id,
                        request_key=saved.request_key).first()
                    if team and team.status == 'INVITING':
                        from site_messages.grab_invite import schedule
                        schedule(saved.pk)
                if state == 'AFTER' and saved.kind in ('INVITE', 'CONFIRM'):
                    _release_late_team_receipt(saved)
        except Exception:
            # This receipt transaction rolls back in full. Later receipts
            # still recover, and the original WAIT_OPENING row can retry.
            import logging
            logging.getLogger(__name__).exception('抢单开池收件恢复失败，等待重试 round_id=%s receipt_id=%s',
                                                  round_id, receipt_id)


def _release_late_team_receipt(receipt):
    """Release the exact late team in the same transaction as classification."""
    if receipt.kind == 'CONFIRM':
        team_id = receipt.payload.get('team_id')
    else:
        team_id = OrderGrabTeamDraft.objects.filter(round_id=receipt.round_id, captain_id=receipt.actor_id,
            request_key=receipt.request_key).values_list('pk', flat=True).first()
    if team_id:
        from .grab_team_flow import _release_late_team
        _release_late_team(team_id, confirmation=receipt.kind == 'CONFIRM', receipt_id=receipt.pk)


def unresolved_round(round_id):
    """No command processor exists yet: every recorded intent blocks finalizing."""
    if type(round_id) is not int or round_id <= 0:
        raise ValueError('抢单轮次无效')
    if not OrderGrabRound.objects.filter(pk=round_id).exists():
        raise ValueError('抢单轮次不存在')
    rows = (OrderGrabReceiptProbe.objects.filter(round_id=round_id)
            .select_related('round__pool__order', 'actor', 'pass_evidence').order_by('pk'))
    found, pending = False, False
    stream = rows.iterator(chunk_size=256)
    while batch := list(islice(stream, 256)):
        ids = {evidence.profile_id_snapshot for row in batch
               if (evidence := getattr(row, 'pass_evidence', None)) and evidence.state == 'KNOWN'}
        profiles = set(EscortProfile.objects.filter(pk__in=ids).values_list('pk', 'user_id', 'account_id'))
        for row in batch:
            found = True
            evidence = getattr(row, 'pass_evidence', None)
            effective = _effective_state(row, evidence, verified_profiles=profiles)
            if effective == 'REVIEW':
                return 'REVIEW'
            pending |= effective == 'PENDING'
    if pending:
        return 'PENDING'
    # Even no records is not a proven cutoff watermark.
    return 'WAIT_APPLY' if found else 'WAIT_WATERMARK'


def compare_candidate_bounds(first_id, second_id):
    """Compare non-overlapping proved intervals; overlapping times need review.

    This does not establish that either command is an active or eligible
    candidate. A double team may only use its completed-confirmation intent.
    """
    from .grab_clock_contract import production_enabled
    if not (getattr(settings, 'GRAB_DB_CLOCK_TRUSTED_FOR_TESTS_ONLY', False) or production_enabled()):
        raise ValueError('登记时钟尚未验证')
    if (type(first_id) is not int or first_id <= 0 or type(second_id) is not int or second_id <= 0
            or first_id == second_id):
        raise ValueError('候选登记不属于同一门店和轮次')
    query = OrderGrabReceiptProbe.objects.filter(pk__in=[first_id, second_id])
    if transaction.get_connection().in_atomic_block:
        from django.db.models import Prefetch
        from .models import OrderGrabSingleAdmission
        query = query.select_for_update().prefetch_related(Prefetch(
            'single_admission', queryset=OrderGrabSingleAdmission.objects.select_for_update()))
    rows = {row.pk: row for row in query}
    first, second = rows.get(first_id), rows.get(second_id)
    if not first or not second or first.round_id != second.round_id:
        raise ValueError('候选登记不属于同一门店和轮次')
    intervals = []
    for item in (first, second):
        if (item.state != 'BEFORE' or item.kind not in ('SINGLE', 'CONFIRM')
                or not isinstance(item.lower_at, datetime) or item.lower_at.utcoffset() is None
                or not isinstance(item.upper_observed_at, datetime) or item.upper_observed_at.utcoffset() is None
                or item.upper_observed_at < item.lower_at or type(item.precision_us) is not int
                or not 1 <= item.precision_us <= 1_000_000):
            raise ValueError('候选登记时间缺少可比较的证据')
        evidence_query = OrderGrabReceiptPassEvidence.objects.filter(receipt=item, state='KNOWN')
        if transaction.get_connection().in_atomic_block:
            evidence_query = evidence_query.select_for_update()
        evidence = evidence_query.first()
        if not evidence or evidence.submitted_tier is None:
            raise ValueError('候选登记时通行证档位需要核对')
        if classify_submitted_tier(raw_tier=evidence.raw_tier, expires_at=evidence.raw_expires_at,
                lower=item.lower_at, upper=item.upper_observed_at, precision_us=item.precision_us) != ('KNOWN', evidence.submitted_tier, ''):
            raise ValueError('候选登记通行证证据不一致')
        if _effective_state(item, evidence) != 'BEFORE':
            raise ValueError('候选登记时间或内容证据不一致')
        quantum = timedelta(microseconds=item.precision_us)
        intervals.append((item.lower_at - quantum, item.upper_observed_at + quantum))
    (first_lower, first_upper), (second_lower, second_upper) = intervals
    # Both expanded intervals include their endpoints. If they touch, both
    # commits could have happened at that instant; neither order is proven.
    if first_upper < second_lower:
        return 'FIRST_EARLIER'
    if second_upper < first_lower:
        return 'SECOND_EARLIER'
    # Overlap could mean either order, or the exact tie that must reopen.
    return 'REVIEW'


def mark_unacknowledged_for_review(round_id, receipt_id):
    """Recovery-only: call after verifying the original worker is gone.

    This does not infer a missing timestamp or release the order. A racing
    acknowledgement may win first; in that case preserve its recorded result.
    """
    if type(round_id) is not int or round_id <= 0 or type(receipt_id) is not int or receipt_id <= 0:
        raise ValueError('抢单登记无效')
    with transaction.atomic():
        _lock_round(round_id)
        probe = OrderGrabReceiptProbe.objects.select_for_update().filter(pk=receipt_id, round_id=round_id).first()
        if not probe:
            raise ValueError('本店抢单登记不存在')
        if probe.state == 'PENDING':
            probe.state = 'REVIEW'; probe.review_reason = 'ACK_MISSING'
            probe.save(update_fields=['state', 'review_reason', 'updated_at'])
            evidence = OrderGrabReceiptPassEvidence.objects.select_for_update().filter(receipt=probe).first()
            if evidence and evidence.state == 'PENDING':
                evidence.state, evidence.review_reason = 'REVIEW', 'RECEIPT_UNCERTAIN'
                evidence.save(update_fields=['state', 'review_reason'])
        return probe.state
