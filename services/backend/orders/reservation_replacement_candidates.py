"""Pure read projection of eligible replacement providers for one future seat."""
from django.conf import settings
from django.db.models import (
    Case, Count, Exists, ExpressionWrapper, F, FloatField, OuterRef, Prefetch, Q, Value, When,
)
from django.db.models import IntegerField

from club_accounts.models import ClubAccount, WechatIdentity
from common.catalog_images import bounded_public_image_url
from common.media import build_media_url
from stores.context import current_store
from users.models import EscortProfile, EscortSchedule, ProviderQualification
from users.qualifications import (
    qualification_rows, scope_matches, serialize_qualification, service_requirement,
)

from .models import Reservation, ReservationSeat, ReservationSeatEvent, ServiceItem
from .reservation_deadlines import due
from .reservation_services import ACTIVE_STATUS_LIST, ReservationError, find_conflicting_reservations
from .reservation_team import _validate_provider


def _profiles(booking, occupied, now):
    queryset = EscortProfile.objects.filter(
        is_verified=True, user__is_active=True,
        account__account_type=ClubAccount.AccountType.PROVIDER,
        account__is_active=True, account__can_login=True, account__can_view=True,
        account__license_membership_active=True,
    ).exclude(account_id__in=occupied).select_related(
        'account', 'user', 'level',
    ).prefetch_related(
        'game_categories',
        Prefetch('service_items', queryset=ServiceItem.objects.select_related('game_category')),
        Prefetch('qualifications', queryset=ProviderQualification.objects.select_related('game_category')),
    )
    if settings.PROVIDER_WECHAT_GATE_ENABLED:
        queryset = queryset.annotate(has_wechat_identity=Exists(
            WechatIdentity.objects.filter(account_id=OuterRef('account_id')),
        ))
    queryset = queryset.annotate(
        review_count=Count(
            'user__received_evaluations',
            filter=Q(user__received_evaluations__voided_at__isnull=True), distinct=True,
        ),
        positive_review_count=Count(
            'user__received_evaluations',
            filter=Q(user__received_evaluations__score__gte=4,
                     user__received_evaluations__voided_at__isnull=True),
            distinct=True,
        ),
        pass_priority=Case(
            When(pass_expires_at__gt=now, pass_tier=EscortProfile.PassTier.BLACK, then=Value(4)),
            When(pass_expires_at__gt=now, pass_tier=EscortProfile.PassTier.GOLD, then=Value(3)),
            When(pass_expires_at__gt=now, pass_tier=EscortProfile.PassTier.SILVER, then=Value(2)),
            When(pass_expires_at__gt=now, pass_tier=EscortProfile.PassTier.BRONZE, then=Value(1)),
            default=Value(0), output_field=IntegerField(),
        ),
    ).annotate(
        favorable_rate=Case(
            When(review_count=0, then=Value(0.0)),
            default=ExpressionWrapper(
                Value(100.0) * F('positive_review_count') / F('review_count'),
                output_field=FloatField(),
            ),
        ),
    )
    return queryset.order_by(
        '-pass_priority', '-favorable_rate', '-rating_avg', '-rating_count',
        '-completed_order_count', 'id',
    )


def _has_conflict(account, booking, now):
    start, end = booking.start_time, booking.end_time
    single = Reservation.objects.filter(
        required_provider_count=1, provider_account=account,
        status__in=ACTIVE_STATUS_LIST,
        start_time__lt=end, end_time__gt=start,
    ).exclude(pk=booking.pk).order_by('pk')
    if any(not due(row, now=now) for row in single):
        return True
    seats = ReservationSeat.objects.filter(
        provider_account=account, reservation__status__in=ACTIVE_STATUS_LIST,
        reservation__start_time__lt=end, reservation__end_time__gt=start,
    ).exclude(reservation_id=booking.pk).select_related('reservation').order_by('reservation_id', 'number')
    for seat in seats:
        current = seat.reservation
        if (current.status == Reservation.Status.PENDING and (
                (seat.status == ReservationSeat.Status.PENDING and seat.confirmation_deadline
                 and seat.confirmation_deadline > now)
                or (seat.status == ReservationSeat.Status.CONFIRMED and current.start_time > now))):
            return True
        if (current.status == Reservation.Status.CONFIRMED
                and seat.status == ReservationSeat.Status.CONFIRMED
                and current.payment_deadline and current.payment_deadline > now):
            return True
    return find_conflicting_reservations(
        account, start, end, exclude_id=booking.pk, now=now,
    ).exists()


def _eligible(profile, booking, now):
    try:
        # This is the exact present-eligibility predicate used by replace().
        _validate_provider(profile.account, profile, booking.service)
    except ReservationError:
        return False
    # A future reservation checks its complete window, not today's shift or
    # active-order capacity. This is provider_covers_window's non-locking read.
    if not EscortSchedule.provider_covers_window(
            profile.user, booking.start_time, booking.end_time, lock=False):
        return False
    return not _has_conflict(profile.account, booking, now)


def _candidate_payload(profile, request, service):
    account, user = profile.account, profile.user
    identity = account or user
    nickname = profile.display_name or identity.nickname or identity.username
    requirement = service_requirement(service)
    qualified = [row for row in qualification_rows(profile) if row.is_active]
    if requirement:
        qualified = [row for row in qualified if scope_matches(row, requirement)]
    reference = (qualified[0].price_per_hour
                 if len(qualified) == 1 and qualified[0].price_per_hour is not None
                 else profile.price_per_hour)
    store = current_store()
    prefix = getattr(store, 'provider_name_prefix', '') if store else ''
    from stores.display import provider_display_name
    avatar = bounded_public_image_url(identity.avatar_url or '')
    return {
        'id': profile.account_id or profile.user_id,
        'account_id': profile.account_id,
        'nickname': nickname,
        'display_name': provider_display_name(nickname),
        'store_prefix': prefix,
        'gender': profile.gender,
        'avatar': avatar,
        'avatar_url': avatar,
        'rank': profile.rank_tier,
        'level_name': profile.level.name if profile.level_id and profile.level.is_active else '',
        'rating': float(profile.rating_avg),
        'rating_count': profile.rating_count,
        'favorable_rate': round(float(profile.favorable_rate or 0), 1),
        'order_count': profile.completed_order_count,
        'win_rate': float(profile.win_rate),
        'price_per_hour': reference or 0,
        'qualifications': [serialize_qualification(row) for row in qualified],
        'has_reference_hourly_price': reference is not None,
        'bio': profile.bio,
        'city': profile.city,
        'status': profile.status,
        'can_order': True,
        'is_verified': profile.is_verified,
        'voice_card_url': build_media_url(request, profile.voice_card),
        'game_category_ids': [row.pk for row in profile.game_categories.all()],
        'game_category_names': [row.name for row in profile.game_categories.all() if row.is_active],
        'service_item_ids': [row.pk for row in profile.service_items.all()],
    }


def original_provider(booking, seat_number):
    """Return only an event-proven local identity; never infer from stale tokens."""
    events = sorted(
        (row for row in booking.seat_events.all() if row.seat_number == seat_number),
        key=lambda row: row.pk,
    )
    if not events:
        return None
    released = events[-1]
    if released.action not in ('REJECTED', 'TIMEOUT') or not released.provider_account_id:
        return None
    invited = next((row for row in reversed(events[:-1]) if row.action == 'INVITED'), None)
    if (invited is None or invited.provider_account_id != released.provider_account_id
            or type(invited.provider_business_cycle) is not int or invited.provider_business_cycle < 1):
        return None
    account = ClubAccount.objects.filter(
        pk=released.provider_account_id, account_type=ClubAccount.AccountType.PROVIDER,
    ).first()
    if account is None:
        return None
    profile = EscortProfile.objects.filter(account_id=account.pk).first()
    name = ((profile.display_name if profile else '') or account.nickname or account.username).strip()
    if not name:
        return None
    from stores.display import provider_display_name
    return {'account_id': account.pk, 'display_name': provider_display_name(name)}


def read_candidates(booking, seat_number, request):
    from .reservation_clock import sample, same_connection, ClockUnavailable
    observed = sample()
    if not same_connection(observed):
        raise ClockUnavailable('补位候选读取期间数据库连接发生变化')
    now = observed.instant
    if now >= booking.start_time:
        raise ReservationError('预约已过期')
    occupied = {seat.provider_account_id for seat in booking.seats.all() if seat.provider_account_id}
    candidates = []
    if booking.service.fulfillment_provider_count == booking.required_provider_count:
        for profile in _profiles(booking, occupied, now):
            if _eligible(profile, booking, now):
                candidates.append(_candidate_payload(profile, request, booking.service))
    from .reservation_serializers import ReservationReplacementCandidatesSerializer
    return ReservationReplacementCandidatesSerializer({
        'seat_number': seat_number,
        'service_id': booking.service_id,
        'start_time': booking.start_time,
        'end_time': booking.end_time,
        'original_provider': original_provider(booking, seat_number),
        'list': candidates,
    }).data
