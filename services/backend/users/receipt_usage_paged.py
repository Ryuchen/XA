"""Reconcile every receipt-local source in bounded query pages."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F


def _pages(query):
    last = 0
    while True:
        page = list(query.filter(pk__gt=last).order_by('pk')[:256])
        if not page:
            return
        yield from page
        last = page[-1].pk


def verify_sources(receipt, state):
    from . import receipt_usage_sources as source
    from .models import PassRefundRepaymentAllocation
    from orders.models import (OrderAddonFundingRelease, OrderPointsFundingAllocation,
        OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease,
        PointsFollowupTransferRelease, PointsThirdTransferRelease,
        ReferralRefundFunding, ReferralRefundFundingRelease, ReservationPointsCancelFundingRelease,
        PointsPrestartCancelFundingRelease, OrderPointsTransferFollowupFundingRevision)
    from orders.points_followup_receipt_usage import facts as followup_facts
    from orders.points_transfer_followup_receipt_usage import facts as transfer_followup_facts
    from orders.points_transfer_refund_receipt_usage import facts as transfer_facts
    from orders.points_transfer_receipt_usage import facts as transfer_revision_facts
    from orders.points_post_refund_transfer_receipt_usage import facts as post_refund_transfer_facts
    from orders.points_followup_transfer_receipt_usage import facts as followup_transfer_facts

    ordinary = (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
                and getattr(settings, 'ORDINARY_SERVICE_FUNDING_ENABLED', False) is True)
    points = (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
              and getattr(settings, 'SERVICE_POINTS_MYSQL_ENABLED', False) is True)
    seen = set()
    def match(facts):
        kind, allocation, request, amount, digest = facts
        if request in seen:
            raise ValidationError('到账用途来源请求编号重复')
        seen.add(request)
        event = state.events.filter(request_key=request).first()
        if not event or (event.kind, event.allocation_key, event.amount, event.source_digest) != (
                kind, allocation, amount, digest):
            raise ValidationError('到账用途与实际分配凭证不一致，请先核对')

    for row in _pages(PassRefundRepaymentAllocation.objects.filter(receipt=receipt)):
        match(source.pass_facts(row))
    for row in _pages(OrderPointsFundingAllocation.objects.filter(receipt=receipt)):
        match(source.order_facts(row))
    for row in _pages(ReferralRefundFunding.objects.filter(receipt=receipt).select_related('refund')):
        match(source.referral_facts(row))
    for row in _pages(ReferralRefundFundingRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__refund')):
        match(source.referral_release_facts(row))

    # Load order snapshots only for actual transfer sources, once per page.
    for row in _pages(OrderPointsTransferFundingRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__redemption__payment', 'allocation__receipt', 'revision')
                      .prefetch_related('revision__order')):
        if not (getattr(settings, 'SERVICE_POINTS_TRANSFER_FUNDING_REVISION_TESTING', False) or points):
            raise ValidationError('转单到账释放尚未开放')
        match(transfer_revision_facts(row))
    for row in _pages(PointsPostRefundTransferRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__redemption', 'allocation__receipt', 'revision__transfer')
                      .prefetch_related('revision__order')):
        if not points:
            raise ValidationError('退款后转单到账释放尚未开放')
        match(post_refund_transfer_facts(row))
    for row in _pages(PointsFollowupTransferRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__redemption', 'allocation__receipt',
                                      'revision__transfer__previous_segment__attribution',
                                      'revision__first_posting', 'revision__second_posting',
                                      'revision__followup_revision')
                      .prefetch_related('revision__order')):
        if not points:
            raise ValidationError('二次退款后转单到账释放尚未开放')
        match(followup_transfer_facts(row))
    for row in _pages(PointsThirdTransferRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__receipt', 'revision')
                      .prefetch_related('revision__order')):
        if not points:
            raise ValidationError('第三轮后转单到账释放尚未开放')
        from orders.points_third_addon_receipt_usage import third_transfer_facts
        match(third_transfer_facts(row))
    for row in _pages(ReservationPointsCancelFundingRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation', 'evidence__points_refund__redemption__payment',
                                      'evidence__points_refund__refund', 'evidence__points_refund__coin_transaction',
                                      'evidence__points_refund__points_ledger')):
        if not getattr(settings, 'SERVICE_POINTS_RESERVATION_CANCEL_USAGE_TESTING', False):
            raise ValidationError('预约取消到账用途试验尚未开放')
        match(source.reservation_release_facts(row))
    for row in _pages(PointsPrestartCancelFundingRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__redemption', 'evidence__points_refund__redemption__payment',
                                      'evidence__points_refund__refund', 'evidence__points_refund__coin_transaction',
                                      'evidence__points_refund__points_ledger')):
        match(source.prestart_cancel_release_facts(row))
    for row in _pages(OrderPointsFundingRelease.objects.filter(allocation__receipt=receipt)
                      .annotate(source_prior_refund_id=F('posting__proposal__points_terms__prior_refund_id'))
                      .select_related('allocation__redemption', 'posting__proposal__points_terms',
                                      'posting__points_refund__redemption__payment', 'posting__points_refund__refund',
                                      'posting__points_refund__coin_transaction', 'posting__points_refund__points_ledger')):
        if row.source_prior_refund_id:
            if not (getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False) or points):
                raise ValidationError('后续退款到账用途试验尚未开放')
            from orders.models import PointsRefundRoundEvidence
            version = PointsRefundRoundEvidence.objects.filter(posting_id=row.posting_id).first()
            if version:
                if version.previous_id:
                    from orders.points_round_receipt_usage import facts as round_facts
                    match(round_facts(row))
                else:
                    from orders.points_third_refund_receipt_usage import facts as third_facts
                    match(third_facts(row))
                continue
            transfer = OrderPointsTransferFollowupFundingRevision.objects.filter(posting_id=row.posting_id).exists()
            match((transfer_followup_facts if transfer else followup_facts)(row))
        elif isinstance(row.posting.participants, dict):
            if not (getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False) or points):
                raise ValidationError('转单退款到账用途试验尚未开放')
            match(transfer_facts(row))
        else:
            if not (getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False) or points):
                raise ValidationError('首次积分退款到账用途试验尚未开放')
            match(source.first_negotiation_release_facts(row))
    for row in _pages(OrderAddonFundingRelease.objects.filter(allocation__receipt=receipt)
                      .select_related('allocation__redemption', 'run__refund__payment__intent', 'run__refund__proposal')):
        run = row.run
        is_ordinary = bool(row.allocation.service_order_id and (run.ordinary_prestart_snapshot is not None
            or (run.refund_id and isinstance(run.ordinary_refund_snapshot, dict)
                and run.ordinary_refund_snapshot.get('kind') == 'ordinary_addon_after_main_refund_v1')
            or (run.negotiation_id and isinstance(run.ordinary_refund_snapshot, dict)
                and 'combined' in run.ordinary_refund_snapshot and 'addon_history' in run.ordinary_refund_snapshot)))
        if not (ordinary if is_ordinary else
                (getattr(settings, 'SERVICE_POINTS_ADDON_REFUND_USAGE_TESTING', False) or points)):
            raise ValidationError('加购退款到账用途试验尚未开放')
        match(source._addon_release_facts(row))
    if len(seen) != state.version:
        raise ValidationError('到账用途与实际分配凭证不一致，请先核对')
    return state
