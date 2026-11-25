"""Two-person reservation lifecycle, under the same store transaction."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from users.qualifications import service_skill
from .models import Reservation, ReservationSeat, ReservationSeatEvent, ReservationStatusLog, ServiceItem
from .reservation_deadlines import confirmation_snapshot, payment_snapshot, required_rules
from .reservation_pricing import HOURLY_UNITS, original_amount
from .reservation_services import (ACTIVE_STATUS_LIST, ReservationConflictError, ReservationError,
    _first_error, _legacy_user_for_account, find_conflicting_reservations, lock_provider_profile,
    lock_team_conflicts, validate_reservation_window, validate_reservation_provider_availability,
    validate_reservation_schedule)
from .reservation_state_machine import log_reservation_event, transition_reservation


def _event(reservation, number, action, *, provider=None, actor=None, reason='', now=None, notice_preview=None):
    if now is None:
        from .reservation_clock import sample
        now = sample().instant
    intent = None
    if (action == 'INVITED' and provider and notice_preview is not None
            and getattr(settings, 'RESERVATION_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        seat = ReservationSeat.objects.get(reservation=reservation, number=number, provider_account=provider)
        intent = {'account_id': provider.pk, 'business_cycle': seat.provider_business_cycle,
                  'user_id': seat.provider_id, 'title': '有新的预约待确认', 'preview': notice_preview}
    status_intent = None
    if (action in ('CONFIRMED', 'REJECTED')
            and getattr(settings, 'RESERVATION_STATUS_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        status_intent = {'account_id': reservation.customer_account_id,
            'business_cycle': reservation.customer_business_cycle, 'user_id': reservation.customer_id,
            'title': '双陪预约状态更新',
            'preview': f'预约 {reservation.reservation_no} 的陪玩确认状态已更新'}
    event = ReservationSeatEvent.objects.create(reservation=reservation, seat_number=number, action=action,
        provider_account=provider, provider_business_cycle=(provider.business_cycle if provider and action == 'INVITED' else None),
        actor_account=actor, reason=reason, occurred_at=now,
        invitation_notice_intent=intent, status_notice_intent=status_intent)
    if intent:
        from site_messages.reservation_invite import schedule_seat
        schedule_seat(event.pk)
    if status_intent:
        from site_messages.reservation_status import schedule
        schedule('seat', event.pk)
    return event


def _validate_provider(account, profile, service):
    provider = _legacy_user_for_account(account)
    if (not provider or provider.pk != profile.user_id or not account.is_active
            or not account.can_login or not provider.is_active):
        raise ReservationError('陪玩账号不可用或资料尚未关联')
    if not profile.is_verified:
        raise ReservationError('陪玩尚未通过认证')
    if settings.PROVIDER_WECHAT_GATE_ENABLED:
        error = profile.wechat_acceptance_error()
        if error:
            raise ReservationError(error)
    if not profile.can_take_service(service) or (service.game_category_id and not service_skill(profile, service)):
        raise ReservationError('陪玩尚未开通该服务的接单资格')
    validate_reservation_provider_availability(profile)
    return provider


def _check_slot(account, start, end, *, exclude_id=None, now=None):
    from .reservation_deadlines import due
    if now is None:
        from .reservation_clock import sample
        now = sample().instant
    lock_team_conflicts(account, start, end, exclude_id=exclude_id, now=now)
    rows = Reservation.objects.select_for_update().filter(
        required_provider_count=1, provider_account=account, status__in=ACTIVE_STATUS_LIST,
        start_time__lt=end, end_time__gt=start).order_by('pk')
    if any(not due(row, now=now) for row in rows):
        raise ReservationConflictError('该时间段大神已有预约，请另选时间')
    conflict = find_conflicting_reservations(account, start, end, exclude_id=exclude_id, now=now).select_for_update().first()
    if conflict:
        raise ReservationConflictError('该时间段大神已有预约，请另选时间', conflicting=conflict)


def _lock_profiles(accounts):
    """Always acquire shared provider schedule locks in stable ID order."""
    profiles = {}
    for account in sorted(accounts, key=lambda row: row.pk):
        profiles[account.pk] = lock_provider_profile(account)
    return profiles


def create_team_reservation(*, customer_account, customer, providers, service, start_time, end_time,
                            game_rounds=1, game_region='', game_nickname='', game_uid='', remark='',
                            operator=None, operator_account=None, decision_key=None):
    actor_account = operator_account or customer_account
    rounds = max(int(game_rounds or 1), 1)
    if service.price_unit in HOURLY_UNITS:
        rounds = 1  # Duration, not an integer quantity, determines the price.
    from .reservation_clock import fingerprint_request
    request_hash = fingerprint_request({
        'customer_account_id': getattr(customer_account, 'pk', None),
        'customer_id': getattr(customer, 'pk', None),
        'provider_account_ids': [account.pk for account in providers],
        'service_id': service.pk, 'start_time': start_time, 'end_time': end_time,
        'game_rounds': rounds, 'game_region': game_region,
        'game_nickname': game_nickname, 'game_uid': game_uid, 'remark': remark,
    })
    if decision_key:
        from .reservation_clock import assert_replay, get_receipt, validate_decision_key
        receipt = get_receipt(validate_decision_key(decision_key))
        if receipt is not None:
            original = receipt.reservation
            assert_replay(receipt, reservation=original, action='CREATE',
                          actor_account=actor_account, deadline=original.confirmation_deadline,
                          request_fingerprint=request_hash)
            original._decision_replayed = True
            return original
    if len(providers) != 2 or len({account.pk for account in providers}) != 2:
        raise ReservationError('双陪预约须选择两位不同的陪玩')
    duration = validate_reservation_window(start_time, end_time)
    service_terms = (service.billing_provider_count, service.fulfillment_provider_count, service.price_unit, service.game_category_id)
    from .gameplay import validate_service_gameplay
    try:
        validate_service_gameplay(service, rounds, companion_assignment=True)
    except ValidationError as exc:
        raise ReservationError(_first_error(exc.detail)) from exc
    with transaction.atomic():
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import ProviderLifecycleError, lock_active_provider_operations
            try:
                eligible = lock_active_provider_operations(providers)
            except ProviderLifecycleError as exc:
                raise ReservationError('陪玩状态暂不可核验，请稍后重试') from exc
            if not eligible:
                raise ReservationError('陪玩门店身份已变化，请重新选择')
        service = ServiceItem.objects.select_for_update().get(pk=service.pk)
        if ((service.billing_provider_count, service.fulfillment_provider_count, service.price_unit, service.game_category_id) != service_terms
                or service.fulfillment_provider_count != 2 or service.is_addon
                or (service.service_category_id and service.service_category.is_gift)):
            raise ReservationError('双陪服务已变化，请重新选择')
        try:
            validate_service_gameplay(service, rounds, companion_assignment=True)
        except ValidationError as exc:
            raise ReservationError(_first_error(exc.detail)) from exc
        from .account_fields import lock_and_validate
        try:
            lock_and_validate(service, {'game_region': game_region, 'game_nickname': game_nickname, 'game_uid': game_uid})
        except ValidationError as exc:
            raise ReservationError(_first_error(exc.detail)) from exc
        profiles = _lock_profiles(providers)
        from .reservation_clock import sample, same_connection, ClockUnavailable
        observed = sample()
        if not same_connection(observed):
            raise ClockUnavailable('双陪预约创建锁内数据库连接发生变化')
        now = observed.instant
        legacy = [_validate_provider(account, profiles[account.pk], service) for account in providers]
        for account in sorted(providers, key=lambda row: row.pk):
            validate_reservation_schedule(profiles[account.pk], start_time, end_time)
            _check_slot(account, start_time, end_time, now=now)
        try:
            rules = required_rules(lock=True)
            duration = validate_reservation_window(start_time, end_time, now=now, rules=rules)
            deadline = confirmation_snapshot(start_time, now, rules)
        except ValueError as exc:
            raise ReservationError(str(exc)) from exc
        from .reservation_services import lock_reservation_customer
        customer_account = lock_reservation_customer(customer_account)
        booking = Reservation.objects.create(customer=customer, customer_account=customer_account,
            customer_business_cycle=customer_account.business_cycle if customer_account else None,
            provider=legacy[0], provider_account=providers[0], provider_business_cycle=providers[0].business_cycle,
            required_provider_count=2, service=service,
            start_time=start_time, end_time=end_time, duration_minutes=duration, game_rounds=rounds,
            estimated_amount=original_amount(service, rounds, duration if service.price_unit in HOURLY_UNITS else None),
            game_region=game_region, game_nickname=game_nickname, game_uid=game_uid, remark=remark,
            confirmation_deadline=deadline, confirmation_timeout_minutes=rules['confirmation_minutes'],
            confirmation_config_version=rules['version'])
        notice_recipients = []
        for number, (account, user) in enumerate(zip(providers, legacy), start=1):
            ReservationSeat.objects.create(reservation=booking, number=number, provider_account=account,
                provider_business_cycle=account.business_cycle,
                provider=user, provider_name_snapshot=profiles[account.pk].display_name or account.nickname or account.username,
                status=ReservationSeat.Status.PENDING, confirmation_deadline=deadline)
            notice_recipients.append((user.pk if user else None, account.pk, account.business_cycle))
            _event(booking, number, 'INVITED', provider=account, actor=operator_account, now=now,
                notice_preview=f'{timezone.localtime(booking.start_time):%m-%d %H:%M} 起{booking.duration_minutes} 分钟，请尽快确认')
        log_reservation_event(booking, action=ReservationStatusLog.Action.CREATE, operator=operator,
                              operator_account=actor_account, reason='发起双陪预约', now=now)
        from .reservation_clock import prepare_decision
        clock_decision = prepare_decision(
            reservation=booking, action='CREATE', deadline=deadline,
            actor_account=actor_account, decision_key=decision_key, state_before='',
            request_fingerprint=request_hash,
        )
        booking._notice_recipients = notice_recipients
    from .reservation_clock import finalize_decision
    finalize_decision(clock_decision, state_after=Reservation.Status.PENDING)
    return booking


def refresh_pending(booking, *, now, actor_account=None, observed=None):
    """Expire invitations but preserve confirmed seats until booking start.

    Caller holds the booking and seat locks. Returns True if booking expired.
    """
    from .reservation_clock import record_after
    seats = list(ReservationSeat.objects.select_for_update().filter(reservation=booking).order_by('number'))
    if booking.status != Reservation.Status.PENDING or len(seats) != 2:
        raise ReservationError('双陪预约席位资料异常，请联系客服')
    timeout_events = []
    for seat in seats:
        if seat.status == ReservationSeat.Status.PENDING and seat.confirmation_deadline <= now:
            previous = seat.provider_account
            previous_user_id = seat.provider_id
            previous_cycle = seat.provider_business_cycle
            record_after(
                reservation=booking, action='SEAT_TIMEOUT',
                deadline=seat.confirmation_deadline, actor_account=actor_account,
                seat_number=seat.number, state_before=booking.status, observed=observed,
            )
            seat.provider_account = None
            seat.provider_business_cycle = None
            seat.provider = None
            seat.provider_name_snapshot = ''
            seat.status = ReservationSeat.Status.VACANT
            seat.confirmation_deadline = None
            seat.save(update_fields=['provider_account', 'provider_business_cycle', 'provider', 'provider_name_snapshot',
                                     'status', 'confirmation_deadline', 'updated_at'])
            event = _event(booking, seat.number, 'TIMEOUT', provider=previous, now=now)
            timeout_events.append(event)
            from site_messages.reservation_seat_timeout_notice import enabled, record
            if enabled():
                record(event, cycle=previous_cycle, legacy_user_id=previous_user_id)
            else:
                from .services import push_message_safe
                guarded = getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)
                transaction.on_commit(lambda user_id=previous_user_id, account_id=previous.pk, booking_id=booking.pk, cycle=previous_cycle:
                    push_message_safe(recipient_id=user_id, recipient_account_id=account_id,
                        title='预约确认已超时', preview='该预约席位已释放，如需了解详情请联系门店客服',
                        msg_type='ORDER', action_url=f'/pages/reservations/index?id={booking_id}',
                        recipient_business_cycle=cycle, require_cycle=guarded))
    confirmed = any(seat.status == ReservationSeat.Status.CONFIRMED for seat in seats)
    pending = [seat.confirmation_deadline for seat in seats if seat.status == ReservationSeat.Status.PENDING]
    # Vacant seats remain replaceable until the appointment starts, including
    # when both original invitees have declined or timed out.
    if now >= booking.start_time:
        record_after(
            reservation=booking, action='EXPIRE', deadline=booking.start_time,
            actor_account=actor_account, state_before=booking.status, observed=observed,
        )
        transition_reservation(booking.pk, Reservation.Status.EXPIRED,
                               reason='双陪预约确认超时', now=now)
        return True
    if (timeout_events and getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False)):
        from site_messages.reservation_status import schedule
        for event in timeout_events:
            event.status_notice_intent = {'account_id': booking.customer_account_id,
                'business_cycle': booking.customer_business_cycle, 'user_id': booking.customer_id,
                'title': '预约席位已超时', 'preview': ('已有陪玩确认，请重新选择空出的席位' if confirmed
                                                   else '有陪玩确认超时，请查看预约并重新选择空出的席位')}
            event.save(update_fields=['status_notice_intent'])
            schedule('seat', event.pk)
    booking.confirmation_deadline = min(pending) if pending else booking.start_time
    booking.save(update_fields=['confirmation_deadline', 'updated_at'])
    Reservation.objects.filter(pk=booking.pk).update(updated_at=now)
    return False


def _snapshot_members(booking_id):
    from club_accounts.models import ClubAccount
    ids = list(ReservationSeat.objects.filter(reservation_id=booking_id, provider_account__isnull=False)
               .values_list('provider_account_id', flat=True))
    return list(ClubAccount.objects.filter(pk__in=ids).order_by('pk'))


def decide(booking_id, actor, *, accept, reason='', decision_key=None):
    """One invited provider decides; successful decisions and events are atomic."""
    decision_to_finalize = None
    from .reservation_clock import fingerprint_request
    request_hash = fingerprint_request({'reason': '' if accept else reason})
    members = _snapshot_members(booking_id)
    with transaction.atomic():
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import (ProviderLifecycleError, lock_provider_operations,
                                                          provider_membership_current)
            try:
                lock_provider_operations([*members, actor])
                if accept and not provider_membership_current(actor):
                    raise ReservationError('陪玩门店身份已变化，请重新确认')
            except ProviderLifecycleError as exc:
                raise ReservationError('陪玩状态暂不可核验，请稍后重试') from exc
        from club_accounts.models import ClubAccount
        current_actor = ClubAccount.objects.select_for_update().filter(pk=actor.pk, account_type='PROVIDER').first()
        if (not current_actor or not current_actor.is_active or not current_actor.can_login
                or not current_actor.license_membership_active or current_actor.business_cycle != actor.business_cycle):
            raise ReservationError('陪玩门店身份已变化，请重新登录')
        profiles = _lock_profiles(members)
        booking = Reservation.objects.select_for_update().get(pk=booking_id)
        # The transaction may have an earlier consistent snapshot while it
        # waited for participant locks. Compare the actually locked seats.
        current_ids = set(ReservationSeat.objects.select_for_update().filter(
            reservation=booking, provider_account__isnull=False).values_list('provider_account_id', flat=True))
        if current_ids != {row.pk for row in members}:
            raise ReservationError('预约人员已变化，请刷新后重试')
        if booking.required_provider_count != 2:
            raise ReservationError('预约当前不可确认或拒绝')
        action = 'TEAM_CONFIRM' if accept else 'TEAM_REJECT'
        if decision_key:
            from .reservation_clock import assert_replay, get_receipt, validate_decision_key
            existing = get_receipt(validate_decision_key(decision_key))
            if existing is not None:
                assert_replay(existing, reservation=booking, action=action,
                              seat_number=existing.seat_number, actor_account=actor,
                              deadline=existing.deadline, request_fingerprint=request_hash)
                booking._decision_replayed = True
                return booking, ''
        from .reservation_clock import assert_clock_clear, sample, same_connection, ClockUnavailable
        assert_clock_clear(booking)
        observed = sample()
        if not same_connection(observed):
            raise ClockUnavailable('双陪决定锁内数据库连接发生变化')
        now = observed.instant
        refreshed = False
        if booking.status == Reservation.Status.PENDING and booking.confirmation_deadline <= now:
            # Preserve the real expiry when the invited person is the one
            # whose deadline passed; raising after clearing the seat would
            # roll the timeout event and released slot back together.
            original_seat = ReservationSeat.objects.select_for_update().filter(
                reservation=booking, provider_account_id=actor.pk).first()
            actor_timed_out = bool(original_seat
                and original_seat.provider_business_cycle == current_actor.business_cycle
                and original_seat.status == ReservationSeat.Status.PENDING
                and original_seat.confirmation_deadline and original_seat.confirmation_deadline <= now)
            expired = refresh_pending(booking, now=now, actor_account=actor, observed=observed)
            booking.refresh_from_db()
            refreshed = True
            if expired:
                return booking, '预约确认已超时'
            if actor_timed_out:
                from .reservation_notifier import notify_reservation_after_commit
                notify_reservation_after_commit(booking)
                recovery = (getattr(settings, 'RESERVATION_EXPIRY_NOTICE_RECOVERY_ENABLED', False)
                            and getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False))
                if not recovery:
                    from .reservation_views import _reservation_notice
                    confirmed = booking.seats.filter(status=ReservationSeat.Status.CONFIRMED).exists()
                    preview = ('已有陪玩确认，请重新选择空出的席位' if confirmed
                               else '有陪玩确认超时，请查看预约并重新选择空出的席位')
                    _reservation_notice(booking, 'customer', '预约席位已超时', preview)
                booking._decision_notice_already_handled = True
                return booking, '预约确认已超时'
        elif booking.status == Reservation.Status.CONFIRMED and accept:
            from .reservation_expiration import expire_locked_reservation
            if expire_locked_reservation(booking):
                booking.refresh_from_db()
                booking._decision_notice_already_handled = True
                return booking, '预约付款已超时'
        elif booking.status != Reservation.Status.PENDING:
            raise ReservationError('预约当前不可确认或拒绝')
        seat = ReservationSeat.objects.select_for_update().filter(reservation=booking, provider_account=actor).first()
        if seat and seat.provider_business_cycle != current_actor.business_cycle:
            raise ReservationError('预约邀请属于此前入店周期，请联系老板重新选择')
        if accept and seat and seat.status == ReservationSeat.Status.CONFIRMED:
            if (not seat.confirmed_at or not seat.confirmation_deadline
                    or seat.confirmed_at >= seat.confirmation_deadline or seat.confirmed_at >= booking.start_time):
                raise ReservationError('预约确认记录待核对')
            booking._decision_replayed = True
            booking._decision_refreshed = refreshed
            return booking, ''
        if booking.status != Reservation.Status.PENDING or not seat or seat.status != ReservationSeat.Status.PENDING:
            raise ReservationError('仅受邀陪玩本人可确认或拒绝')
        if now >= seat.confirmation_deadline or now >= booking.start_time:
            refresh_pending(booking, now=now, actor_account=actor, observed=observed)
            booking.refresh_from_db()
            return booking, '预约确认已超时'
        from .reservation_clock import prepare_decision, require_before, reservation_snapshot, seat_snapshot
        decision_deadline = min(seat.confirmation_deadline, booking.start_time)
        clock_decision = prepare_decision(
            reservation=booking, action=action, deadline=decision_deadline,
            actor_account=actor, seat_number=seat.number, decision_key=decision_key,
            state_before=booking.status, request_fingerprint=request_hash,
            before_snapshot={
                'reservation': reservation_snapshot(booking),
                'seat': seat_snapshot(seat),
            },
        )
        if clock_decision.replayed:
            raise ReservationError('原陪玩决定已回执，请勿重复提交')
        if clock_decision.state != 'PENDING':
            require_before(clock_decision)
        if accept:
            if settings.PROVIDER_WECHAT_GATE_ENABLED:
                error = profiles[actor.pk].wechat_acceptance_error()
                if error:
                    raise ReservationError(error)
            seat.status = ReservationSeat.Status.CONFIRMED
            seat.confirmed_at = now
            seat.save(update_fields=['status', 'confirmed_at', 'updated_at'])
            _event(booking, seat.number, 'CONFIRMED', provider=actor, actor=actor, now=now)
            seats = list(ReservationSeat.objects.select_for_update().filter(reservation=booking).order_by('number'))
            if len(seats) != 2:
                raise ReservationError('预约席位资料异常，请联系客服')
            if all(row.status == ReservationSeat.Status.CONFIRMED for row in seats):
                try:
                    rules = required_rules(lock=True)
                except ValueError as exc:
                    raise ReservationError(str(exc)) from exc
                # The receipt's lower bound was sampled under all participant
                # locks; the post-commit upper bound classifies audit-write delay.
                def payment_fields(row):
                    if row.confirmed_at >= min(row.start_time, seat.confirmation_deadline):
                        raise ReservationError('预约确认时限已到，请重新预约')
                    row.payment_deadline = payment_snapshot(row.start_time, row.confirmed_at, rules)
                    row.payment_timeout_minutes = rules['payment_minutes']
                    row.payment_config_version = rules['version']
                booking = transition_reservation(booking.pk, Reservation.Status.CONFIRMED,
                    operator_account=actor, reason='双陪均已确认', side_effect=payment_fields,
                    update_fields=['payment_deadline', 'payment_timeout_minutes', 'payment_config_version'],
                    now=now)
            else:
                refresh_pending(booking, now=now, actor_account=actor, observed=observed)
        else:
            seat.provider_account = None
            seat.provider_business_cycle = None
            seat.provider = None
            seat.provider_name_snapshot = ''
            seat.status = ReservationSeat.Status.VACANT
            seat.confirmation_deadline = None
            seat.save(update_fields=['provider_account', 'provider_business_cycle', 'provider', 'provider_name_snapshot',
                                     'status', 'confirmation_deadline', 'updated_at'])
            event = _event(booking, seat.number, 'REJECTED', provider=actor, actor=actor, reason=reason, now=now)
            from site_messages.reservation_team_declined import enabled, record
            if enabled():
                record(event)
            refresh_pending(booking, now=now, actor_account=actor, observed=observed)
        booking.refresh_from_db()
        decision_to_finalize = clock_decision
        booking._decision_state_after = booking.status

    from .reservation_clock import finalize_decision
    finalize_decision(decision_to_finalize, state_after=booking._decision_state_after)
    return booking, ''


def replace(booking_id, actor, number, new_account, *, client_request_id=None, decision_key=None):
    """Boss fills one vacant seat without disturbing the other seat."""
    decision_to_finalize = None
    clock_decision_key = decision_key or client_request_id
    if number not in (1, 2):
        raise ReservationError('预约席位无效')
    from .reservation_clock import fingerprint_request
    request_hash = fingerprint_request({
        'seat_number': number, 'provider_account_id': new_account.pk,
        'provider_business_cycle': new_account.business_cycle,
    })
    members = _snapshot_members(booking_id)
    accounts = {account.pk: account for account in members}
    accounts[new_account.pk] = new_account
    with transaction.atomic():
        if getattr(settings, 'PROVIDER_LIFECYCLE_INTEGRATION_ENABLED', False):
            from club_accounts.provider_lifecycle import (ProviderLifecycleError, lock_provider_operations,
                                                          provider_membership_current)
            try:
                lock_provider_operations(list(accounts.values()))
                if not provider_membership_current(new_account):
                    raise ReservationError('陪玩门店身份已变化，请重新选择')
            except ProviderLifecycleError as exc:
                raise ReservationError('陪玩状态暂不可核验，请稍后重试') from exc
        # Preserve the service -> ordered providers -> reservation lock order.
        service_id = Reservation.objects.filter(pk=booking_id).values_list('service_id', flat=True).first()
        service = ServiceItem.objects.select_for_update().get(pk=service_id)
        profiles = _lock_profiles(list(accounts.values()))
        booking = Reservation.objects.select_for_update().get(pk=booking_id)
        if booking.customer_account_id != actor.pk:
            raise ReservationError('仅预约老板本人可替换陪玩')
        from club_accounts.models import ClubAccount
        current_actor = ClubAccount.objects.select_for_update().filter(pk=actor.pk, account_type='BOSS').first()
        if (not current_actor or not current_actor.is_active or not current_actor.can_login
                or not current_actor.license_membership_active
                or current_actor.business_cycle != actor.business_cycle
                or booking.customer_business_cycle != current_actor.business_cycle):
            raise ReservationError('预约属于此前入店周期，请重新预约')
        from .reservation_clock import assert_clock_clear
        assert_clock_clear(booking)
        current_ids = set(ReservationSeat.objects.select_for_update().filter(
            reservation=booking, provider_account__isnull=False).values_list('provider_account_id', flat=True))
        if current_ids != {row.pk for row in members}:
            raise ReservationError('预约人员已变化，请刷新后重试')
        if booking.required_provider_count != 2:
            raise ReservationError('预约当前不可替换陪玩')
        if clock_decision_key:
            from .reservation_clock import assert_replay, get_receipt, validate_decision_key, ClockConflict
            existing = get_receipt(validate_decision_key(clock_decision_key))
            if existing is not None:
                if (existing.action != 'REPLACE' or existing.seat_number != number
                        or existing.before_snapshot.get('replacement_provider_account_id') != new_account.pk
                        or existing.before_snapshot.get('replacement_business_cycle') != new_account.business_cycle):
                    raise ClockConflict('原 decision_key 对应的补位内容不同')
                assert_replay(existing, reservation=booking, action='REPLACE',
                              seat_number=number, actor_account=actor,
                              deadline=existing.deadline, request_fingerprint=request_hash)
                booking._replacement_replayed = True
                return booking, ''
        import hashlib
        from wallet.models import IdempotencyKey
        scope = 'reservation.replace'
        fingerprint = hashlib.sha256(
            f'{booking.pk}:{number}:{new_account.pk}:{new_account.business_cycle}:{actor.business_cycle}'.encode()
        ).hexdigest()
        key = (IdempotencyKey.objects.select_for_update().filter(account=current_actor, request_id=client_request_id).first()
               if client_request_id else None)
        if key and (key.scope != scope or key.request_fingerprint != fingerprint):
            raise ReservationError('请求编号已用于其他操作，请重新核对')
        from .reservation_clock import assert_clock_clear, sample, same_connection, ClockUnavailable
        assert_clock_clear(booking)
        observed = sample()
        if not same_connection(observed):
            raise ClockUnavailable('双陪补位锁内数据库连接发生变化')
        now = observed.instant

        def invitation_event():
            return (ReservationSeatEvent.objects.select_for_update().filter(reservation=booking, seat_number=number,
                action='INVITED').order_by('-pk').first())

        def remember(event):
            if not client_request_id:
                return
            receipt, created = IdempotencyKey.objects.get_or_create(account=current_actor, request_id=client_request_id,
                defaults={'scope': scope, 'request_fingerprint': fingerprint, 'response': {'event_id': event.pk}})
            if not created and (receipt.scope != scope or receipt.request_fingerprint != fingerprint
                                or receipt.response != {'event_id': event.pk}):
                raise ReservationError('请求编号已用于其他操作，请重新核对')

        if booking.status == Reservation.Status.CONFIRMED and key:
            from .reservation_deadlines import due
            seat = ReservationSeat.objects.select_for_update().get(reservation=booking, number=number)
            event = invitation_event()
            if (not due(booking, now=now) and seat.status == ReservationSeat.Status.CONFIRMED
                    and seat.provider_account_id == new_account.pk and seat.provider_business_cycle == new_account.business_cycle
                    and event and event.provider_account_id == new_account.pk
                    and event.provider_business_cycle == new_account.business_cycle
                    and key.response == {'event_id': event.pk}):
                booking._replacement_replayed = True
                return booking, ''
            return booking, '原邀请已失效，请刷新预约'
        if booking.status != Reservation.Status.PENDING:
            raise ReservationError('预约当前不可替换陪玩')
        refreshed = False
        if booking.confirmation_deadline <= now:
            expired = refresh_pending(booking, now=now, actor_account=actor, observed=observed)
            refreshed = True
            if expired:
                booking.refresh_from_db()
                return booking, '预约已过期'
        if now >= booking.start_time:
            return booking, '预约已过期'
        seat = ReservationSeat.objects.select_for_update().get(reservation=booking, number=number)
        event = invitation_event()
        if key and (not event or key.response != {'event_id': event.pk}
                    or seat.provider_account_id != new_account.pk
                    or seat.provider_business_cycle != new_account.business_cycle
                    or seat.status not in (ReservationSeat.Status.PENDING, ReservationSeat.Status.CONFIRMED)
                    or (seat.status == ReservationSeat.Status.PENDING and
                        (not seat.confirmation_deadline or now >= seat.confirmation_deadline))):
            booking._replacement_refreshed = refreshed
            return booking, '原邀请已失效，请刷新预约'
        if ((seat.status == ReservationSeat.Status.PENDING or (key and seat.status == ReservationSeat.Status.CONFIRMED))
                and seat.provider_account_id == new_account.pk
                and seat.provider_business_cycle == new_account.business_cycle
                and (seat.status == ReservationSeat.Status.CONFIRMED or
                     (seat.confirmation_deadline and now < seat.confirmation_deadline))):
            # A lost response must not create a second invitation or extend
            # the already promised confirmation deadline.
            if seat.status == ReservationSeat.Status.PENDING and now >= seat.confirmation_deadline:
                refresh_pending(booking, now=now, actor_account=actor, observed=observed)
                booking.refresh_from_db()
                booking._replacement_refreshed = True
                return booking, '原邀请已超时，请刷新预约'
            if client_request_id:
                if (not event or event.provider_account_id != new_account.pk
                        or event.provider_business_cycle != new_account.business_cycle):
                    raise ReservationError('预约邀请记录待核对')
                remember(event)
            booking._replacement_replayed = True
            booking._replacement_refreshed = refreshed
            return booking, ''
        if seat.status != ReservationSeat.Status.VACANT:
            raise ReservationError('只能替换拒绝或超时后空出的席位')
        if service.fulfillment_provider_count != booking.required_provider_count:
            raise ReservationError('服务所需人数已变化，请联系客服核对')
        current_ids = set(ReservationSeat.objects.select_for_update().filter(
            reservation=booking, provider_account__isnull=False).values_list('provider_account_id', flat=True))
        if new_account.pk in current_ids:
            raise ReservationError('两名陪玩不能相同')
        provider = _validate_provider(new_account, profiles[new_account.pk], service)
        validate_reservation_schedule(profiles[new_account.pk], booking.start_time, booking.end_time)
        _check_slot(new_account, booking.start_time, booking.end_time, exclude_id=booking.pk, now=now)
        if now >= booking.start_time:
            return booking, '预约已过期'
        from .reservation_clock import prepare_decision, require_before, reservation_snapshot, seat_snapshot
        clock_decision = prepare_decision(
            reservation=booking, action='REPLACE', deadline=booking.start_time,
            actor_account=actor, seat_number=number, decision_key=clock_decision_key,
            state_before=booking.status, request_fingerprint=request_hash,
            before_snapshot={
                'reservation': reservation_snapshot(booking),
                'seat': seat_snapshot(seat),
                'replacement_provider_account_id': new_account.pk,
                'replacement_business_cycle': new_account.business_cycle,
            },
        )
        if clock_decision.replayed:
            raise ReservationError('原补位邀请已回执，请勿重复提交')
        if clock_decision.state != 'PENDING':
            require_before(clock_decision)
        deadline = min(now + timedelta(minutes=booking.confirmation_timeout_minutes), booking.start_time)
        seat.provider_account = new_account
        seat.provider_business_cycle = new_account.business_cycle
        seat.provider = provider
        seat.provider_name_snapshot = profiles[new_account.pk].display_name or new_account.nickname or new_account.username
        seat.confirmation_deadline = deadline
        seat.status = ReservationSeat.Status.PENDING
        seat.confirmed_at = None
        seat.save(update_fields=['provider_account', 'provider_business_cycle', 'provider', 'provider_name_snapshot',
                                 'confirmation_deadline', 'confirmed_at', 'status', 'updated_at'])
        if number == 1:
            booking.provider_account = new_account
            booking.provider_business_cycle = new_account.business_cycle
            booking.provider = provider
            booking.provider_name_snapshot = seat.provider_name_snapshot
        # Another seat may have been confirmed while this operation waited
        # for provider locks. Calculate from current locked seat rows, not the
        # transaction's earlier consistent-read snapshot.
        deadlines = list(ReservationSeat.objects.select_for_update().filter(
            reservation=booking, status=ReservationSeat.Status.PENDING)
            .values_list('confirmation_deadline', flat=True))
        booking.confirmation_deadline = min(deadlines)
        booking.save(update_fields=['provider_account', 'provider_business_cycle', 'provider', 'provider_name_snapshot',
                                    'confirmation_deadline', 'updated_at'])
        event = _event(booking, number, 'INVITED', provider=new_account, actor=actor, now=now,
               notice_preview=f'预约 {booking.reservation_no} 等待您确认')
        remember(event)
        from site_messages.reservation_team_replaced import enabled, record
        if enabled():
            record(event)
        booking._replacement_refreshed = refreshed
        decision_to_finalize = clock_decision
        booking._replacement_state_after = booking.status

    from .reservation_clock import finalize_decision
    finalize_decision(decision_to_finalize, state_after=booking._replacement_state_after)
    return booking, ''
