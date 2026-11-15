"""A real add-on refund invalidating a third-round transfer handover."""
import hashlib
import json
from datetime import timedelta

from rest_framework.exceptions import ValidationError


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def permits_locked(order, handover):
    return order.refund_records.count() == 3 and permits_for_round_locked(order, handover)


def permits_for_round_locked(order, handover):
    """Identify actual frozen STANDARD liability without authorizing a write."""
    from stores.db import transaction
    from .models import PointsThirdTransferRevision
    if (not transaction.get_connection().in_atomic_block or order.refund_records.count() < 3
            or handover.payment.intent.order_id != order.pk or handover.kind != 'STANDARD'
            or handover.status != 'PENDING' or handover.accepted_at
            or handover.target_segment.attribution.order_id != order.pk
            or not handover.target_segment.started_at or handover.target_segment.released_at
            or not order.providers.filter(provider_account_id=handover.target_segment.provider_account_id,
                                          released_at__isnull=True, settled_at__isnull=True).exists()):
        return False
    versions = PointsThirdTransferRevision.objects.filter(order=order,
        transfer__previous_segment_id=handover.source_segment_id,
        transfer__created_at__lt=handover.created_at).order_by('-created_at', '-pk')
    return any(version.evidence_hash == digest(version.snapshot) and any(
        len(row) == 3 and row[0] == handover.payment_id and row[1] == handover.source_segment_id
        and 0 < handover.source_basis_before <= row[2] for row in version.snapshot.get('pending_handover', []))
        for version in versions)


def capture(handover, refund):
    from .models import PointsThirdTransferRevision, OrderAddonFundingReleaseRun
    from .points_third_addon_responsibilities import current
    payment, order = handover.payment, handover.payment.intent.order
    if (handover.kind != 'STANDARD' or handover.status != 'SUPERSEDED' or handover.accepted_at
            or refund.order_id != order.pk or refund.created_at <= handover.created_at
            or OrderAddonFundingReleaseRun.objects.filter(handover=handover).exists()):
        raise ValidationError('第三轮待交接替失效时序异常')
    versions = PointsThirdTransferRevision.objects.filter(order=order,
        transfer__previous_segment_id=handover.source_segment_id,
        transfer__created_at__lt=handover.created_at).order_by('-created_at', '-pk')
    version = next((item for item in versions if any(row[0] == payment.pk and row[1] == handover.source_segment_id
        for row in item.snapshot.get('pending_handover', []))), None)
    if not version:
        raise ValidationError('失效接替缺少真实转单待交责任')
    previous, before = current(payment, before=refund.created_at, with_share_id=True,
                               exclude_refund_id=refund.pk)
    _, after = current(payment, before=refund.created_at + timedelta(microseconds=1), with_share_id=True)
    source = [part for part in before if part['is_current']]
    if ((previous.pk if previous else None) != handover.previous_id or len(source) != 1
            or source[0]['segment_id'] != handover.source_segment_id
            or source[0]['basis'] != handover.source_basis_before):
        raise ValidationError('退款前接替原责任不一致')
    def rows(items):
        return [[part.get('share_id'), part['segment_id'], part['provider_id'], part['basis'], part['income'],
                 part['commission_snapshot'], part['is_current']] for part in items]
    before_rows, after_rows = rows(before), rows(after)
    if before_rows == after_rows:
        raise ValidationError('退款未改变该接替方案的实际责任')
    allocations = [list(row) for row in refund.share_allocations.filter(payment=payment).order_by('pk')
                   .values_list('pk', 'participant_id', 'share_id', 'refund_basis', 'earning_deduction')]
    deductions = [list(row) for row in refund.earning_deductions.filter(payment=payment).order_by('pk')
                  .values_list('pk', 'participant_id', 'segment_id', 'amount')]
    if not allocations and not deductions:
        raise ValidationError('接替失效缺少实际逐份额退款承担')
    return {'kind': 'POINTS_THIRD_TRANSFER_INVALIDATION_V1', 'handover_id': handover.pk,
        'payment_id': payment.pk, 'refund_id': refund.pk, 'transfer_revision_id': version.pk,
        'transfer_revision_hash': version.evidence_hash, 'before_shares': before_rows, 'after_shares': after_rows,
        'allocations': allocations, 'deductions': deductions}
