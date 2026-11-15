"""Persist payment and participation evidence for future original-price metrics.

Never backfill historical orders from mutable catalog or settlement shares.
All mutating helpers are invoked while the caller holds the order transaction.
"""
from django.conf import settings
from stores.db import transaction

from .models import OrderOriginalAttribution, OrderParticipationSegment, OrderOriginalShareTransfer
from .original_share_rules import agreed_transfer, initial_shares


def enabled():
    from .principal_segments import enabled as principal_segments_enabled
    return bool(getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False) or principal_segments_enabled())


def _require_transaction():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('原价参与记录必须在订单事务内处理')


def payment_created(order):
    if not enabled() or order.is_gift_snapshot:
        return
    if not getattr(settings, 'ORIGINAL_ATTRIBUTION_ENABLED', False) and order.amount <= 0:
        return
    _require_transaction()
    amount = order.original_amount
    valid = type(amount) is int and amount >= order.amount and amount >= 0 and amount % 10 == 0
    attribution = OrderOriginalAttribution.objects.create(order=order, original_amount=max(amount, 0),
        status='PENDING' if valid else 'REVIEW', review_reason='' if valid else '付款原价不符合整数店币规则')
    from .segment_financial_capture import payment_created as capture_payment
    capture_payment(attribution, order)
    if not valid:
        from .segment_financial_capture import mark_review as financial_review
        financial_review(attribution, '付款原价待核对')


def _attribution(order):
    if not enabled():
        from .principal_segments import enabled_for_order
        from .ordinary_transfer_capability import permits as refunded_transfer_permits
        if not enabled_for_order(order) and not refunded_transfer_permits(order):
            from .ordinary_transfer_capability import enabled as ordinary_transfer_enabled
            from .models import OrderOriginalShareTransfer
            if (not ordinary_transfer_enabled() or not order.refund_records.exists()
                    or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            from django.utils import timezone
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
            try:
                if not verify_positive_pretransfer(order, before=timezone.now()):
                    return None
            except DjangoValidationError:
                return None
    _require_transaction()
    return OrderOriginalAttribution.objects.select_for_update().filter(order=order).first()


def _review(row, reason):
    if row and row.status != 'REVIEW':
        row.status = 'REVIEW'; row.review_reason = reason[:255]
        row.save(update_fields=['status', 'review_reason'])
    if row:
        from .segment_financial_capture import mark_review as financial_review
        financial_review(row, reason)


def mark_review(order, reason):
    row = _attribution(order)
    _review(row, reason)


def _seat_cycle(order, account_id):
    """Use the stored seat source; never infer it from today's account."""
    from .models import OrderProvider
    return OrderProvider.objects.filter(order=order, provider_account_id=account_id).values_list(
        'provider_business_cycle', flat=True).first()


def assigned(order, *, at):
    row = _attribution(order)
    if not row or row.status == 'REVIEW':
        return
    if row.status != 'PENDING' or row.segments.exists():
        _review(row, '订单重新分配需要核对原价参与记录')
        return
    from .team_rules import required_team_size
    participants = list(order.providers.filter(released_at__isnull=True).order_by('pk')
                        .values_list('provider_account_id', flat=True))
    if not participants and order.provider_account_id:
        participants = [order.provider_account_id]
    if len(participants) != required_team_size(order):
        _review(row, '已分配人员与购买人数不一致')
        return
    try:
        allocated = initial_shares(row.original_amount, participants)
    except ValueError:
        _review(row, '原价或分配人员无法核对')
        return
    for slot, (account_id, amount) in enumerate(allocated.shares, start=1):
        OrderParticipationSegment.objects.create(attribution=row, provider_account_id=account_id,
            provider_business_cycle=_seat_cycle(order, account_id), slot=slot, original_share=amount, accepted_at=at)
    row.rounding_surplus = allocated.rounding_surplus
    row.status = 'ACTIVE'; row.save(update_fields=['rounding_surplus', 'status'])
    from .segment_financial_capture import assigned as capture_assigned
    capture_assigned(row, order)


def started(order, account_ids, *, at, source):
    row = _attribution(order)
    if not row or row.status == 'REVIEW':
        return
    ids = set(account_ids)
    if not ids or any(type(pk) is not int or pk < 1 for pk in ids):
        _review(row, '开始履约时参与账号不完整')
        return
    segments = list(row.segments.select_for_update().filter(provider_account_id__in=ids, released_at__isnull=True))
    if not ids or row.status != 'ACTIVE' or len(segments) != len(ids) or {s.provider_account_id for s in segments} != ids:
        _review(row, '开始履约时参与人员记录不完整')
        return
    for segment in segments:
        if segment.started_at is None:
            segment.started_at = at; segment.start_source = source
            # The start action already stored the owner's entry image on the
            # legacy report. Reference that exact file for this segment;
            # never infer it from an unowned historical report.
            from wallet.models import ProviderReport
            image = ProviderReport.objects.filter(order=order, provider_account_id=segment.provider_account_id).values_list('entry_image', flat=True).first()
            fields = ['started_at', 'start_source']
            if image and not segment.entry_image:
                segment.entry_image = image
                fields.append('entry_image')
            segment.save(update_fields=fields)


def completion_evidence(order, account_id, image_name):
    """Attach the current member's verified order-action image to one segment."""
    if not image_name:
        return
    row = _attribution(order)
    if not row or row.status == 'REVIEW':
        return
    from .state_machine import IllegalTransitionError
    segments = list(row.segments.select_for_update().filter(provider_account_id=account_id,
                                                            released_at__isnull=True))
    if row.status != 'ACTIVE' or len(segments) != 1 or segments[0].started_at is None:
        raise IllegalTransitionError('本次履约记录不完整，请先核对结单截图')
    segment = segments[0]
    segment.completion_image = image_name
    segment.save(update_fields=['completion_image'])


def prestart_reassigned(order, old_account_id, new_account_id, *, at, commission_plan=None):
    row = _attribution(order)
    if not row or row.status == 'REVIEW':
        return
    matches = list(row.segments.select_for_update().filter(provider_account_id=old_account_id, released_at__isnull=True))
    if (row.status != 'ACTIVE' or len(matches) != 1 or matches[0].started_at is not None or
            row.segments.filter(provider_account_id=new_account_id, released_at__isnull=True).exists()):
        _review(row, '服务前改派的原价参与记录不完整')
        return
    previous = matches[0]
    previous.released_at = at; previous.save(update_fields=['released_at'])
    incoming = OrderParticipationSegment.objects.create(attribution=row, provider_account_id=new_account_id,
        provider_business_cycle=_seat_cycle(order, new_account_id),
        slot=previous.slot, original_share=previous.original_share, accepted_at=at)
    from .segment_financial_capture import prestart_reassigned as capture_reassigned
    capture_reassigned(row, previous, incoming, order, commission_plan or {})


def service_transferred(order, commands, *, operator, at):
    """Save an authorized, explicit original-price agreement with a transfer."""
    row = _attribution(order)
    if not row or row.status == 'REVIEW':
        return
    from .state_machine import IllegalTransitionError
    if row.status != 'ACTIVE':
        _review(row, '服务中转单的原价参与记录不完整')
        return
    if not operator or operator.account_type != 'STAFF':
        raise IllegalTransitionError('仅授权后台人员可登记原价份额')
    for cmd in commands:
        data = cmd.get('original_share')
        if not isinstance(data, dict):
            raise IllegalTransitionError('请填写双方协商的原价份额和依据')
        retained, transferred, agreement = data.get('retained'), data.get('transferred'), data.get('agreement')
        if not isinstance(agreement, str) or not 3 <= len(agreement.strip()) <= 500:
            raise IllegalTransitionError('请填写双方协商原价份额的依据')
        previous_rows = list(row.segments.select_for_update().filter(
            provider_account_id=cmd['old_provider_account_id'], released_at__isnull=True))
        if len(previous_rows) != 1:
            raise IllegalTransitionError('原陪玩的原价参与记录不完整，请先核对')
        previous = previous_rows[0]
        incoming_id = cmd['new_provider_account'].pk
        if row.segments.filter(provider_account_id=incoming_id, released_at__isnull=True).exists():
            raise IllegalTransitionError('接替陪玩已参与本订单，请先核对原价份额')
        try:
            agreed_transfer(previous.original_share, ((1, retained), (2, transferred)))
        except ValueError as exc:
            raise IllegalTransitionError(str(exc)) from exc
        if previous.started_at is None and retained:
            raise IllegalTransitionError('原陪玩未开始履约，不能保留原价份额')
        old_share = previous.original_share
        previous.original_share = retained; previous.released_at = at
        previous.save(update_fields=['original_share', 'released_at'])
        incoming = OrderParticipationSegment.objects.create(attribution=row, provider_account_id=incoming_id,
            provider_business_cycle=_seat_cycle(order, incoming_id),
            slot=previous.slot, original_share=transferred, accepted_at=at)
        event = OrderOriginalShareTransfer.objects.create(previous_segment=previous, next_segment=incoming,
            original_share_before=old_share, retained_share=retained, transferred_share=transferred,
            operator=operator, agreement=agreement.strip(), created_at=at)
        from .segment_financial_capture import service_transferred as capture_transfer
        capture_transfer(row, order, event, game_plan=cmd.get('_segment_game_plan'),
                         commission_plan=cmd.get('_segment_commission_plan'))
