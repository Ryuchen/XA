"""KOOK-owned identity proof and durable, exactly-once points accrual."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from django.db import IntegrityError
from stores.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import KookBinding, KookBindingChallenge, KookPointEvent, KookPointsConfig
from .services import change_points, config, locked_profile


def enabled_config():
    return KookPointsConfig.objects.filter(pk=1, enabled=True).first()


def allowed_account(account):
    return (account.is_active and account.can_login and account.can_view
            and account.account_type in ['BOSS', 'PROVIDER']
            and (account.account_type != 'BOSS' or account.license_membership_active))


def _lock_bosses(accounts):
    """Serialize with boss retirement and re-entry before locking KOOK rows."""
    from club_accounts.models import ClubAccount
    accounts = {account.pk: account for account in accounts if account.account_type == 'BOSS'}
    locked = {account.pk: account for account in ClubAccount.objects.select_for_update().filter(
        pk__in=accounts, account_type='BOSS').order_by('pk')}
    if (len(locked) != len(accounts) or any(locked[pk].business_cycle != account.business_cycle
                                            for pk, account in accounts.items())):
        raise ValidationError('老板账号或业务周期已变化，请重试')


def _boss_binding_current(binding):
    return (binding.account.account_type != 'BOSS' or
            (binding.account.license_membership_active and binding.business_cycle is not None
             and binding.business_cycle == binding.account.business_cycle))


def challenge(account):
    from django.conf import settings
    from club_accounts.models import ClubAccount
    if account.account_type == ClubAccount.AccountType.BOSS:
        with transaction.atomic():
            _lock_bosses([account])
            current = ClubAccount.objects.get(pk=account.pk, account_type='BOSS')
            if not allowed_account(current):
                raise ValidationError('老板账号状态或业务周期已变化，请重新登录')
            return _challenge(current, _protect_existing=True)
    if (not getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
            or account.account_type != ClubAccount.AccountType.PROVIDER):
        return _challenge(account)
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operation, provider_membership_current)
    try:
        ensure_provider_lifecycle(account)
        with transaction.atomic():
            lock_provider_operation(account)
            if not provider_membership_current(account):
                raise ValidationError('陪玩账号状态或业务周期已变化，请重新登录')
            return _challenge(account, _protect_existing=True)
    except ProviderLifecycleError as exc:
        raise ValidationError('陪玩账号状态暂不可核验，请稍后重试') from exc


@transaction.atomic
def _challenge(account, *, _protect_existing=False):
    if not enabled_config():
        raise ValidationError('KOOK积分尚未开启')
    if _protect_existing:
        # Consume and refresh paths both lock challenge before binding.
        KookBindingChallenge.objects.select_for_update().filter(account=account).first()
    binding_query = KookBinding.objects.select_for_update() if _protect_existing else KookBinding.objects
    binding = binding_query.filter(account=account).first()
    if binding:
        from django.conf import settings
        from club_accounts.models import ClubAccount
        guarded = (account.account_type == ClubAccount.AccountType.BOSS or
                   (getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                    and account.account_type == ClubAccount.AccountType.PROVIDER))
        if not guarded or binding.business_cycle == account.business_cycle:
            raise ValidationError('此账号已绑定KOOK')
    token='XA'+secrets.token_hex(8).upper()
    KookBindingChallenge.objects.update_or_create(account=account, defaults={
        'code_hash':hashlib.sha256(token.encode()).hexdigest(), 'expires_at':timezone.now()+timedelta(minutes=10),
        'business_cycle':account.business_cycle})
    return token


def _license_point_evidence(binding, *, outcome, stage, now, detail):
    """Keep uncertain or denied accrual facts without issuing points."""
    from console.models import AdminAuditLog
    from .models import ClubOperationConfig
    config()
    operation = ClubOperationConfig.objects.select_for_update().get(pk=1)
    evidence_version = 3 if ('business_certificate_interval' in detail or 'business_certificate' in detail) else 2
    return AdminAuditLog.objects.create(operator_name='系统', method='SYSTEM', path='kook/license-points',
        resource='kook-license-points', object_id=str(binding.pk), status_code=200,
        request_body={'evidence_version': evidence_version, 'account_id': binding.account_id, 'account_type': binding.account.account_type,
                      'business_cycle': binding.account.business_cycle, 'binding_cycle': binding.business_cycle,
                      'membership_active': binding.account.license_membership_active,
                      'channel_id': binding.channel_id, 'points_enabled': operation.points_enabled,
                      'points_config_updated_at': operation.updated_at.isoformat(),
                      'outcome': outcome, 'stage': stage, 'observed_at': now.isoformat(), **detail})


def _accrue(binding, now, *, outcome):
    # Validate every segment before changing any counter. New rule versions
    # must remain unpublished until replay and manual review support them.
    if binding.channel_id and binding.accounted_at and 0 < (now - binding.accounted_at).total_seconds() <= 90:
        from stores.certificate_admission import interval_admission
        from .kook_online_rules import interval_segments
        parts = interval_admission(binding.accounted_at, now) or [(binding.accounted_at, now, 'ALLOW', None)]
        segments = [(start, end, rule, certificate_status, evidence)
                    for interval_start, interval_end, certificate_status, evidence in parts
                    for start, end, rule in interval_segments(interval_start, interval_end)]
        result = outcome
        for segment_start, segment_end, rule, certificate_status, evidence in segments:
            if segment_end - segment_start < timedelta(milliseconds=1):
                # Accounting is millisecond based; never fabricate a full unit
                # for a sub-millisecond piece at a rule boundary.
                binding.accounted_at = segment_end
                continue
            certificate = (certificate_status, evidence) if evidence is not None else None
            if rule.revision == 0:
                segment_result = _accrue_one(binding, segment_end, outcome=outcome, certificate=certificate)
            else:
                segment_result = _accrue_versioned(binding, segment_start, segment_end, rule, outcome=outcome,
                                                   certificate=certificate)
            if segment_result == 'UNKNOWN' or result == 'ALLOW':
                result = segment_result
        return result
    return _accrue_one(binding, now, outcome=outcome)


def _with_certificate(outcome, certificate):
    if certificate is None:
        return outcome, {}
    decision, evidence = certificate
    if decision == 'DENY' or (decision == 'UNKNOWN' and outcome == 'ALLOW'):
        outcome = decision
    return outcome, {'business_certificate_interval': evidence}


def _accrue_versioned(binding, started_at, ended_at, rule, *, outcome, certificate=None):
    """Record one definite authorized segment before the ordered consumer."""
    from stores.license_benefits import check_license_benefits
    from .models import KookOnlineInterval
    from .kook_online_queue import lock_cursor, replay_waiting
    if not allowed_account(binding.account) or not binding.channel_id or binding.accounted_at != started_at:
        raise ValidationError('KOOK 在线片段的账号或连接状态无法核验')
    decision, evidence = check_license_benefits()
    from django.utils.dateparse import parse_datetime
    current = evidence.get('current') or {}
    effective = parse_datetime(current.get('effective_at', '')) if current else None
    if outcome == 'DENY' or decision == 'DENY':
        segment_outcome = 'DENY'
    elif outcome != 'ALLOW' or decision != 'ALLOW' or (effective and started_at < effective):
        segment_outcome = 'UNKNOWN'
    else:
        segment_outcome = 'ALLOW'
    segment_outcome, certificate_facts = _with_certificate(segment_outcome, certificate)
    cursor = lock_cursor(binding)
    elapsed = int((ended_at - started_at).total_seconds() * 1000)
    if elapsed <= 0 or elapsed > 90000 or (cursor.last_ended_at and started_at < cursor.last_ended_at):
        raise ValidationError('KOOK 在线片段时序无法核验')
    decision_source = None
    if segment_outcome == 'UNKNOWN':
        decision_source = _license_point_evidence(binding, outcome='UNKNOWN', stage='INTERVAL', now=ended_at,
            detail={'from': started_at.isoformat(), 'to': ended_at.isoformat(), 'elapsed_ms': elapsed,
                    'online_milliseconds': binding.online_milliseconds,
                    'previous_online_base': binding.cycle_online_base,
                    'previous_awarded_points': binding.awarded_points,
                    'cycle_awarded_base': binding.cycle_awarded_base,
                    'rule_revision': rule.revision, 'rule_window_ms': rule.window_ms, 'rule_points': rule.points,
                    **certificate_facts})
    KookOnlineInterval.objects.create(binding=binding, account_id=binding.account_id,
        account_cycle=binding.account.business_cycle, binding_cycle=binding.business_cycle,
        channel_id=binding.channel_id, started_at=started_at, ended_at=ended_at, elapsed_ms=elapsed,
        rule_revision=rule.revision, rule_version=rule, rule_window_ms=rule.window_ms, rule_points=rule.points,
        online_before=binding.online_milliseconds, online_after=binding.online_milliseconds,
        awarded_before=binding.awarded_points, awarded_after=binding.awarded_points,
        online_base_before=binding.cycle_online_base, online_base_after=binding.cycle_online_base,
        award_base_before=binding.cycle_awarded_base, award_base_after=binding.cycle_awarded_base,
        points_enabled_used=config().points_enabled if segment_outcome == 'ALLOW' else None,
        interval_outcome=segment_outcome, award_outcome=segment_outcome,
        decision_source=decision_source, processing_status='WAITING')
    if segment_outcome == 'UNKNOWN':
        cursor.blocked = True
        cursor.save(update_fields=['blocked', 'updated_at'])
    elif not cursor.blocked:
        replay_waiting(binding, cursor)
    binding.accounted_at = ended_at
    return segment_outcome


def _accrue_one(binding, now, *, outcome, certificate=None):
    eligible = binding.channel_id and binding.accounted_at and allowed_account(binding.account)
    interval_start = binding.accounted_at if eligible else None
    interval_channel = binding.channel_id if eligible else ''
    elapsed_evidence = int((now - interval_start).total_seconds() * 1000) if interval_start else 0
    decision_source = None
    points_ledger = None
    points_enabled_used = None
    from .kook_online_rules import initial_rule, earned, remainder, base_after_unawarded
    rule = initial_rule() if eligible else None
    before = {'online': binding.online_milliseconds, 'awarded': binding.awarded_points,
              'online_base': binding.cycle_online_base, 'award_base': binding.cycle_awarded_base}
    if eligible and outcome == 'ALLOW':
        from django.utils.dateparse import parse_datetime
        from stores.license_benefits import check_license_benefits
        outcome, evidence = check_license_benefits()
        current = evidence.get('current') or {}
        effective = parse_datetime(current.get('effective_at', '')) if current else None
        # A checkpoint spanning a new grant can include time from an expired
        # or unverifiable interval. Do not blend it into the new grant.
        if outcome == 'ALLOW' and effective and binding.accounted_at < effective:
            outcome = 'UNKNOWN'
    outcome, certificate_facts = _with_certificate(outcome, certificate)
    interval_outcome = outcome
    cursor = None
    deferred = False
    if interval_start and 0 < elapsed_evidence <= 90000:
        from .kook_online_queue import lock_cursor, verify_cursor
        cursor = lock_cursor(binding)
        verify_cursor(cursor, binding)
        if cursor.last_ended_at and interval_start < cursor.last_ended_at:
            raise ValidationError('KOOK 在线区间顺序冲突，请人工核对')
        deferred = cursor.blocked
        if deferred and outcome == 'ALLOW':
            points_enabled_used = config().points_enabled
    if eligible and outcome == 'UNKNOWN':
        elapsed = int((now - binding.accounted_at).total_seconds() * 1000)
        if 0 < elapsed <= 90000:
            decision_source = _license_point_evidence(binding, outcome=outcome, stage='INTERVAL', now=now,
                detail={'from': binding.accounted_at.isoformat(), 'to': now.isoformat(), 'elapsed_ms': elapsed,
                        'online_milliseconds': binding.online_milliseconds,
                        'previous_online_base': binding.cycle_online_base,
                        'previous_awarded_points': binding.awarded_points,
                        'cycle_awarded_base': binding.cycle_awarded_base, **certificate_facts})
    if outcome == 'ALLOW' and eligible and not deferred:
        elapsed=int((now-binding.accounted_at).total_seconds()*1000)
        prior_online = binding.online_milliseconds
        # A disconnected listener never fabricates online time. Connection
        # heartbeats checkpoint every 30s; gaps require fresh KOOK presence.
        if 0 < elapsed <= 90000:
            binding.online_milliseconds+=elapsed
        if (binding.cycle_online_base > binding.online_milliseconds
                or binding.cycle_awarded_base > binding.awarded_points):
            raise ValidationError('KOOK在线积分周期凭证异常')
        total = earned(binding.online_milliseconds, binding.cycle_online_base, binding.cycle_awarded_base, rule)
        if cursor:
            from .kook_online_balances import pending_after
            if total - binding.awarded_points != pending_after(cursor, elapsed_evidence, rule):
                raise ValidationError('KOOK 在线区间与规则余额不一致')
        if total > binding.awarded_points:
            points_enabled_used = config().points_enabled
        if total > binding.awarded_points and points_enabled_used:
            profile=locked_profile(binding.account)
            from stores.license_benefits import check_license_benefits, check_new_benefits
            award_certificate_facts = certificate_facts
            if certificate is None:
                # Releasing a group without a new observed segment uses the
                # current certificate. Observed intervals carry their own proof.
                final_outcome, award_evidence = check_new_benefits()
                if 'business_certificate' in award_evidence:
                    award_certificate_facts = {'business_certificate': award_evidence['business_certificate']}
            else:
                final_outcome, _ = check_license_benefits()
            if final_outcome == 'ALLOW':
                points_ledger = change_points(profile,(total-binding.awarded_points)*100,
                    f'KOOK_ONLINE_TOTAL:{binding.pk}:{total}','KOOK_ONLINE',
                    f'KOOK语音累计在线，规则版本 {rule.revision}')
                binding.awarded_points=total
            else:
                missed = total - binding.awarded_points
                prior_total = earned(prior_online, binding.cycle_online_base, binding.cycle_awarded_base, rule)
                prior_pending = max(0, prior_total - binding.awarded_points)
                prior_remainder = remainder(prior_online, binding.cycle_online_base, rule)
                credited_elapsed = elapsed if 0 < elapsed <= 90000 else 0
                decision_source = _license_point_evidence(binding, outcome=final_outcome, stage='AWARD', now=timezone.now(),
                    detail={'checkpoint_at': now.isoformat(), 'online_milliseconds': binding.online_milliseconds,
                            'previous_online_base': binding.cycle_online_base,
                            'previous_awarded_points': binding.awarded_points,
                            'cycle_awarded_base': binding.cycle_awarded_base, 'points_not_awarded': missed,
                            'prior_online_milliseconds': prior_online, 'eligible_interval_ms': credited_elapsed,
                            'prior_pending_points': prior_pending, 'prior_remainder_ms': prior_remainder,
                            **award_certificate_facts})
                # Preserve only the fraction already counted before this
                # uncertain interval; neither it nor denied full units may
                # silently reappear after a future renewal.
                binding.cycle_online_base = base_after_unawarded(binding.online_milliseconds, prior_remainder,
                    binding.awarded_points, binding.cycle_awarded_base, rule)
                outcome = final_outcome
    if cursor and outcome == 'ALLOW' and interval_outcome == 'ALLOW' and not deferred:
        from .kook_online_balances import accrue_interval, consume_current_pending
        balance_pending = accrue_interval(cursor, rule, elapsed_evidence)
        if balance_pending != total - before['awarded']:
            raise ValidationError('KOOK 在线积分流水与规则余额不一致')
        if points_ledger is not None:
            consume_current_pending(cursor, balance_pending, points_ledger)
    if interval_start and 0 < elapsed_evidence <= 90000:
        from .models import KookOnlineInterval
        KookOnlineInterval.objects.create(binding=binding, account_id=binding.account_id,
            account_cycle=binding.account.business_cycle, binding_cycle=binding.business_cycle,
            channel_id=interval_channel, started_at=interval_start, ended_at=now,
            elapsed_ms=elapsed_evidence, rule_revision=0, rule_version=rule,
            rule_window_ms=rule.window_ms, rule_points=rule.points,
            online_before=before['online'], online_after=binding.online_milliseconds,
            awarded_before=before['awarded'], awarded_after=binding.awarded_points,
            online_base_before=before['online_base'], online_base_after=binding.cycle_online_base,
            award_base_before=before['award_base'], award_base_after=binding.cycle_awarded_base,
            points_enabled_used=points_enabled_used,
            interval_outcome=interval_outcome, award_outcome=outcome,
            points_ledger=points_ledger, decision_source=decision_source,
            processing_status='WAITING' if deferred or outcome == 'UNKNOWN' else 'DONE',
            processed_at=None if deferred or outcome == 'UNKNOWN' else timezone.now())
        if cursor:
            from .kook_online_queue import sync_cursor
            sync_cursor(cursor, binding)
            if outcome == 'UNKNOWN':
                cursor.blocked = True
            elif not deferred:
                cursor.last_ended_at = now
            cursor.save(update_fields=['blocked', 'last_ended_at', 'updated_at'])
    binding.accounted_at=now
    return outcome


def checkpoint(now=None, status='已连接，自动计分中', *, expected_revision=None):
    """Hold provider lifecycle and boss account locks before KOOK rows."""
    from django.conf import settings
    from club_accounts.models import ClubAccount
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operations)
    initial = list(KookBinding.objects.filter(channel_id__gt='', account__account_type=ClubAccount.AccountType.PROVIDER)
                   .select_related('account').order_by('pk'))
    accounts = {row.account_id: row.account for row in initial} if guarded else {}
    expected = {row.pk: (row.account_id, row.account.business_cycle) for row in initial} if guarded else None
    bosses = list(KookBinding.objects.filter(channel_id__gt='', account__account_type='BOSS').select_related('account'))
    boss_snapshot = {row.pk: (row.account_id, row.account.business_cycle) for row in bosses}
    try:
        for account in accounts.values():
            ensure_provider_lifecycle(account)
        with transaction.atomic():
            lock_provider_operations(accounts.values())
            _lock_bosses([row.account for row in bosses])
            return _checkpoint(now=now, status=status, _provider_snapshot=expected, _boss_snapshot=boss_snapshot, expected_revision=expected_revision)
    except ProviderLifecycleError as exc:
        raise ValidationError('陪玩账号状态暂不可核验，自动计分未执行') from exc


@transaction.atomic
def _checkpoint(now=None, status='已连接，自动计分中', *, _provider_snapshot=None, _boss_snapshot=None, expected_revision=None):
    cfg = KookPointsConfig.objects.select_for_update().filter(pk=1, enabled=True).first()
    if not cfg or (expected_revision is not None and cfg.listener_revision != expected_revision):
        return False
    now=now or timezone.now()
    from stores.license_benefits import check_license_benefits
    from club_accounts.models import ClubAccount
    license_outcome, _ = check_license_benefits()
    outcome = license_outcome
    bindings = list(KookBinding.objects.select_for_update().filter(channel_id__gt='').select_related('account').order_by('id'))
    if _boss_snapshot is not None and {row.pk: (row.account_id, row.account.business_cycle) for row in bindings
            if row.account.account_type == 'BOSS'} != _boss_snapshot:
        raise ValidationError('老板在线账号或业务周期已变化，自动计分请重试')
    if _provider_snapshot is not None:
        actual = {row.pk: (row.account_id, row.account.business_cycle) for row in bindings
                  if row.account.account_type == ClubAccount.AccountType.PROVIDER}
        if actual != _provider_snapshot:
            raise ValidationError('陪玩在线账号或业务周期已变化，自动计分请重试')
    for binding in bindings:
        provider_allowed = (_provider_snapshot is None or binding.account.account_type != ClubAccount.AccountType.PROVIDER
                            or (binding.account.license_membership_active and binding.business_cycle is not None
                                and binding.business_cycle == binding.account.business_cycle))
        provider_allowed = provider_allowed and _boss_binding_current(binding)
        result = _accrue(binding, now, outcome=license_outcome if provider_allowed else 'DENY')
        if provider_allowed and (result == 'UNKNOWN' or outcome == 'ALLOW'):
            outcome = result
        binding.save(update_fields=['accounted_at','online_milliseconds','awarded_points','cycle_online_base'])
    if outcome == 'UNKNOWN':
        status = f'{status.replace("自动计分中", "积分暂停")}；门店授权待核验'
    elif outcome != 'ALLOW':
        status = f'{status.replace("自动计分中", "积分暂停")}；门店授权不允许发放积分'
    KookPointsConfig.objects.filter(pk=1,enabled=True).update(heartbeat_at=now,status=status[:80])
    return True


def reset_presence(presence, now=None, *, expected_revision=None):
    """Seed only current presence. Never award time from before this connection."""
    from django.conf import settings
    from club_accounts.models import ClubAccount
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operations)
    initial = list(KookBinding.objects.filter(account__account_type=ClubAccount.AccountType.PROVIDER)
                   .select_related('account').order_by('pk'))
    accounts = {row.account_id: row.account for row in initial} if guarded else {}
    snapshot = {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in initial} if guarded else None
    bosses = list(KookBinding.objects.filter(account__account_type='BOSS').select_related('account'))
    boss_snapshot = {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in bosses}
    try:
        for account in accounts.values():
            ensure_provider_lifecycle(account)
        with transaction.atomic():
            lock_provider_operations(accounts.values())
            _lock_bosses([row.account for row in bosses])
            return _reset_presence(presence, now=now, _provider_snapshot=snapshot, _boss_snapshot=boss_snapshot, expected_revision=expected_revision)
    except ProviderLifecycleError as exc:
        raise ValidationError('陪玩账号状态暂不可核验，在线状态请重试') from exc


@transaction.atomic
def _reset_presence(presence, now=None, *, _provider_snapshot=None, _boss_snapshot=None, expected_revision=None):
    if expected_revision is not None and not KookPointsConfig.objects.select_for_update().filter(pk=1, enabled=True, listener_revision=expected_revision).exists():
        return False
    from club_accounts.models import ClubAccount
    now=now or timezone.now()
    bindings = list(KookBinding.objects.select_for_update().select_related('account').order_by('id'))
    if _boss_snapshot is not None and {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in bindings
            if row.account.account_type == 'BOSS'} != _boss_snapshot:
        raise ValidationError('老板绑定账号或业务周期已变化，在线状态请重试')
    if _provider_snapshot is not None:
        actual = {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in bindings
                  if row.account.account_type == ClubAccount.AccountType.PROVIDER}
        if actual != _provider_snapshot:
            raise ValidationError('陪玩绑定账号或业务周期已变化，在线状态请重试')
    for binding in bindings:
        provider_out = (_provider_snapshot is not None and binding.account.account_type == ClubAccount.AccountType.PROVIDER
                        and (not binding.account.license_membership_active or binding.business_cycle is None
                             or binding.business_cycle != binding.account.business_cycle))
        binding.channel_id='' if provider_out or not _boss_binding_current(binding) else presence.get(binding.user_id,'')
        binding.accounted_at=now
        binding.event_at=now
        binding.save(update_fields=['channel_id','accounted_at','event_at'])
    return True


def attach_presence(presence, *, uninitialized_only=False, now=None, expected_revision=None):
    """Attach current KOOK presence without crossing an account cycle."""
    from django.conf import settings
    from club_accounts.models import ClubAccount
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operations)
    if not isinstance(presence, dict) or not presence:
        return
    query = KookBinding.objects.filter(user_id__in=presence)
    query = query.filter(accounted_at__isnull=True) if uninitialized_only else query.filter(channel_id='')
    initial = list(query.select_related('account').order_by('pk'))
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    providers = {row.account_id: row.account for row in initial
                 if guarded and row.account.account_type == ClubAccount.AccountType.PROVIDER}
    bosses = {row.account_id: row.account for row in initial if row.account.account_type == 'BOSS'}
    snapshot = {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in initial}
    try:
        for account in providers.values():
            ensure_provider_lifecycle(account)
        with transaction.atomic():
            lock_provider_operations(providers.values())
            _lock_bosses(bosses.values())
            if expected_revision is not None and not KookPointsConfig.objects.select_for_update().filter(pk=1, enabled=True, listener_revision=expected_revision).exists():
                return
            locked = list(query.select_for_update().select_related('account').order_by('pk'))
            if {row.pk: (row.account_id, row.account.business_cycle, row.user_id) for row in locked} != snapshot:
                raise ValidationError('KOOK 在线账号或业务周期已变化，请重试')
            now = now or timezone.now()
            for binding in locked:
                valid_provider = (not guarded or binding.account.account_type != 'PROVIDER'
                    or (binding.account.license_membership_active and binding.business_cycle is not None
                        and binding.business_cycle == binding.account.business_cycle))
                if not valid_provider or not _boss_binding_current(binding) or not allowed_account(binding.account):
                    continue
                binding.channel_id, binding.accounted_at, binding.event_at = presence[binding.user_id], now, now
                binding.save(update_fields=['channel_id', 'accounted_at', 'event_at'])
    except ProviderLifecycleError as exc:
        raise ValidationError('陪玩账号状态暂不可核验，在线状态请重试') from exc


def event_time(milliseconds):
    try:
        return datetime.fromtimestamp(int(milliseconds)/1000,tz=dt_timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValidationError('KOOK事件时间不合法') from None


def process_event(event, now=None, *, expected_revision=None):
    """Serialize provider targets before the event, binding and points locks."""
    from django.conf import settings
    from club_accounts.models import ClubAccount
    guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
    extra = event.get('extra') or {}
    body = extra.get('body') or {}
    is_voice = event.get('type') == 255 and extra.get('type') in ('joined_channel', 'exited_channel')
    user_id = str(body.get('user_id', '')) if is_voice else str(event.get('author_id', ''))
    bindings = list(KookBinding.objects.filter(user_id=user_id).select_related('account')) if user_id else []
    content = str(event.get('content', '')).strip()
    match = re.fullmatch(r'绑定\s+(XA[0-9A-F]{16})', content, re.I)
    challenges = (list(KookBindingChallenge.objects.filter(
        code_hash=hashlib.sha256(match[1].upper().encode()).hexdigest()).select_related('account')) if match else [])
    targets = [('binding', item) for item in bindings] + [('challenge', item) for item in challenges]
    providers = {item.account_id: item.account for _, item in targets
                 if guarded and item.account.account_type == ClubAccount.AccountType.PROVIDER}
    bosses = {item.account_id: item.account for _, item in targets
              if item.account.account_type == ClubAccount.AccountType.BOSS}
    snapshot = {(kind, item.pk): (item.account_id, item.account.business_cycle) for kind, item in targets
                if item.account.account_type == ClubAccount.AccountType.BOSS
                or (guarded and item.account.account_type == ClubAccount.AccountType.PROVIDER)}
    from club_accounts.provider_lifecycle import (ProviderLifecycleError, ensure_provider_lifecycle,
                                                   lock_provider_operations)
    try:
        for account in providers.values():
            ensure_provider_lifecycle(account)
        with transaction.atomic():
            lock_provider_operations(providers.values())
            _lock_bosses(bosses.values())
            return _process_event(event, now=now, _provider_snapshot=snapshot, expected_revision=expected_revision)
    except ProviderLifecycleError as exc:
        raise ValidationError('陪玩账号状态暂不可核验，KOOK事件请重试') from exc


def _event_provider_allowed(kind, item, snapshot):
    from django.conf import settings
    from club_accounts.models import ClubAccount
    if snapshot is None:
        return True
    expected = snapshot.get((kind, item.pk))
    if item.account.account_type == ClubAccount.AccountType.PROVIDER and not getattr(
            settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
        return True
    if (item.account.account_type not in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)
            or expected != (item.account_id, item.account.business_cycle)):
        raise ValidationError('KOOK绑定账号或业务周期已变化，请重试事件')
    return (item.account.license_membership_active and item.business_cycle is not None
            and item.business_cycle == item.account.business_cycle)


@transaction.atomic
def _process_event(event, now=None, *, _provider_snapshot=None, expected_revision=None):
    now=now or timezone.now();cfg=KookPointsConfig.objects.select_for_update().filter(pk=1, enabled=True).first()
    if cfg and expected_revision is not None and cfg.listener_revision != expected_revision:return False
    if not cfg or not config().points_enabled:return False
    timestamp=event_time(event.get('msg_timestamp'))
    if timestamp<cfg.started_at or timestamp>now+timedelta(seconds=60):return False
    extra=event.get('extra') or {};kind=extra.get('type');body=extra.get('body') or {}
    is_voice=event.get('type')==255 and kind in ['joined_channel','exited_channel']
    if is_voice:
        if str(event.get('target_id'))!=cfg.guild_id or str(body.get('channel_id')) not in cfg.voice_channel_ids:return False
        user_id=str(body.get('user_id',''))
    else:
        if event.get('channel_type')!='GROUP' or event.get('type') not in [1,9] or str(event.get('target_id'))!=cfg.checkin_channel_id:return False
        if str(extra.get('guild_id'))!=cfg.guild_id or (extra.get('author') or {}).get('bot'):return False
        user_id=str(event.get('author_id',''))
    event_id=str(event.get('msg_id',''))
    if not user_id or not event_id or len(event_id)>100:return False
    if not KookPointEvent.objects.get_or_create(event_id=event_id)[1]:return False
    if is_voice:
        binding=KookBinding.objects.select_for_update().select_related('account').filter(user_id=user_id).first()
        if binding and not _event_provider_allowed('binding', binding, _provider_snapshot):return False
        if not binding or not allowed_account(binding.account):return False
        if binding.event_at and timestamp<=binding.event_at:return False
        # Late events cannot move a checkpoint backwards or double-count.
        at=max(timestamp,binding.accounted_at or timestamp)
        from stores.license_benefits import check_license_benefits
        _accrue(binding,at,outcome=check_license_benefits()[0])
        if kind=='joined_channel':binding.channel_id=str(body['channel_id'])
        elif binding.channel_id==str(body['channel_id']):binding.channel_id=''
        binding.event_at=timestamp;binding.save()
        return True
    content=str(event.get('content','')).strip()
    match=re.fullmatch(r'绑定\s+(XA[0-9A-F]{16})',content,re.I)
    if match:
        proof=KookBindingChallenge.objects.select_for_update().select_related('account').filter(
            code_hash=hashlib.sha256(match[1].upper().encode()).hexdigest(),expires_at__gte=now).first()
        if proof and not _event_provider_allowed('challenge', proof, _provider_snapshot):return False
        if not proof or not allowed_account(proof.account):return False
        if KookBinding.objects.filter(user_id=user_id).exclude(account=proof.account).exists():return False
        existing = KookBinding.objects.select_for_update().filter(account=proof.account).first()
        if existing:
            from club_accounts.models import ClubAccount
            if (proof.account.account_type not in (ClubAccount.AccountType.BOSS, ClubAccount.AccountType.PROVIDER)
                    or _provider_snapshot is None
                    or existing.user_id != user_id or existing.business_cycle == proof.business_cycle
                    or _provider_snapshot.get(('binding', existing.pk)) != (proof.account_id, proof.business_cycle)
                    or existing.cycle_online_base > existing.online_milliseconds
                    or existing.cycle_awarded_base > existing.awarded_points):
                return False
            # Preserve all previously earned points and the KOOK identity;
            # earlier partial online time must not count in the new cycle.
            existing.business_cycle = proof.business_cycle
            existing.cycle_online_base = existing.online_milliseconds
            existing.cycle_awarded_base = existing.awarded_points
            existing.channel_id = ''
            existing.accounted_at = now
            existing.event_at = now
            existing.save(update_fields=['business_cycle', 'cycle_online_base', 'cycle_awarded_base',
                                         'channel_id', 'accounted_at', 'event_at'])
        else:
            try:
                with transaction.atomic():
                    KookBinding.objects.create(account=proof.account, business_cycle=proof.business_cycle,
                        user_id=user_id,nickname=str((extra.get('author') or {}).get('username',''))[:100])
            except IntegrityError:return False
        proof.delete()
        from .notifier import notify_member_points_after_commit
        notify_member_points_after_commit(proof.account_id)
        return True
    if content not in ['签到','/签到','打卡','/打卡']:return False
    binding=KookBinding.objects.select_for_update().select_related('account').filter(user_id=user_id).first()
    if binding and not _event_provider_allowed('binding', binding, _provider_snapshot):return False
    if not binding or not allowed_account(binding.account):return False
    from stores.license_benefits import check_new_benefits
    if check_new_benefits('CHECKIN')[0] != 'ALLOW':return False
    day=timezone.localdate(timestamp)
    profile=locked_profile(binding.account)
    key = f'KOOK_CHECKIN:{binding.account_id}:{day.isoformat()}'
    from .models import PointLedger
    if PointLedger.objects.filter(event_key=key, account_id=binding.account_id, kind='KOOK_CHECKIN').exists():
        return True
    change_points(profile,cfg.daily_checkin_points * 100,key,
        'KOOK_CHECKIN',f'KOOK签到大厅签到（规则版本 {cfg.revision}）')
    return True
