"""Rebuild per-person service readiness after a transferred points refund."""
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import Order, OrderSegmentReport
from .points_checkout_test import require_isolated_checkout
from .points_funding_plan import PointsFundingPlanError
from .segment_settlement_plan import PersonAmount, SegmentAmount, SegmentSettlementPlan
from .settlement import SettlementError


def build_refunded_plan(order, *, now=None, allow_in_service=False):
    """Read actual refund terms and report clocks; never settle or credit."""
    from .points_transfer_refund_access import permits, verification_scope
    if any(permits(order, phase=phase) for phase in ('report', 'settlement')):
        with verification_scope(order):
            return _build_refunded_plan(order, now=now, allow_in_service=allow_in_service)
    return _build_refunded_plan(order, now=now, allow_in_service=allow_in_service)


def _build_refunded_plan(order, *, now=None, allow_in_service=False):
    from .points_transfer_refund_access import permits, require_refund_or_isolated
    try:
        require_refund_or_isolated()
    except ValidationError as exc:
        raise SettlementError('转单退款后的逐段结算尚未开放') from exc
    if ((not permits(order, phase='refund') and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_SETTLEMENT_TESTING', False))
            or not transaction.get_connection().in_atomic_block
            or (order.status != Order.Status.COMPLETED and not (
                allow_in_service and order.status == Order.Status.IN_SERVICE))
            or (order.status == Order.Status.COMPLETED and not order.completed_at)
            or order.service_aftersale_hours is None or order.service_freeze_hours is None):
        raise SettlementError('转单退款后的结算条件尚未满足')
    from .points_transfer_refund_continuation import verify_locked
    from .points_transfer_refund_evidence import reconstruct_funding_locked
    from .points_transfer_refunded_funding import plan_refunded_transfer
    try:
        posting = verify_locked(order)
        funding = plan_refunded_transfer(order.pk, allow_finalized=True)
        from .models import PointsPostRefundTransferRevision
        version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
        if version:
            from .points_post_refund_transfer import verify_locked as verify_version
            from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
            verify_version(version)
            facts = version.snapshot['facts']
            history = _reconstruct_from_verified_facts(order.pk, facts,
                refund_id=posting.points_refund.refund_id, through_transfer_id=version.transfer_id)
        else:
            facts = posting.participants['facts']
            history = reconstruct_funding_locked(order.pk, posting.participants,
                refund_id=posting.points_refund.refund_id, through_transfer_id=facts['transfers'][-1]['id'])
    except (ValidationError, PointsFundingPlanError) as exc:
        raise SettlementError('转单退款后的履约或资金凭证待核对') from exc
    plan_funding = funding
    addon_history = (version.snapshot.get('addon_history') if version else
                     getattr(getattr(posting, 'funding_release_evidence', None), 'addon_funding_snapshot', None))
    if version:
        from .points_transfer_addon_events import later_runs
        try:
            later = [row for row in later_runs(order, posting) if row.created_at > version.created_at]
        except ValidationError as exc:
            raise SettlementError('转单后加购资金版本待核对') from exc
        if later:
            snapshot = later[-1].refunded_main_snapshot
            if (not isinstance(snapshot, dict) or snapshot.get('transfer_revision_id') != version.pk
                    or not isinstance(snapshot.get('addon_history'), list)):
                raise SettlementError('转单后加购资金版本待核对')
            addon_history = snapshot['addon_history']
    else:
        from .points_transfer_addon_events import later_runs
        try:
            later = later_runs(order, posting)
        except ValidationError as exc:
            raise SettlementError('主单退款后的加购资金版本待核对') from exc
        if later:
            snapshot = later[-1].refunded_main_snapshot
            if (not isinstance(snapshot, dict) or snapshot.get('transfer_origin_posting_id') != posting.pk
                    or not isinstance(snapshot.get('addon_history'), list)):
                raise SettlementError('主单退款后的加购来源待核对')
            addon_history = snapshot['addon_history']
    if addon_history:
        from dataclasses import replace
        addon_income = sum(item['provider_units'] for item in addon_history)
        if addon_income > funding.provider_income_units:
            raise SettlementError('加购收益超过整单收益')
        plan_funding = replace(funding, provider_income_units=funding.provider_income_units - addon_income)
    plan, _ = build_from_verified_evidence(order, facts, history, [posting], plan_funding, now=now)
    return plan, funding


def build_from_verified_evidence(order, facts, history, postings, funding, *, now=None):
    """Build readiness only after the caller has verified all refund evidence."""
    now = now or timezone.now()
    segments = {row['id']: row for row in facts['segments']}
    outgoing = {row['previous_segment_id'] for row in facts['transfers']}
    grouped = defaultdict(list)
    for line in history.segment_shares:
        segment = segments[line[0]]
        if line[2] and not segment['started_at']:
            raise SettlementError('有收益的转入履约尚未开始')
        if segment['started_at'] and (segment['id'] not in outgoing or segment['original_share'] > 0):
            grouped[line[1]].append(line[0])
    if postings:
        from .models import OrderAddonHandover
        from .addon_handover import verify_handover, current_responsibilities
        posting = postings[0]
        handovers = OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED')\
            .select_related('payment__intent')
        for handover in handovers:
            third_handover = len(postings) >= 3 and handover.accepted_at > postings[2].created_at
            followup_handover = len(postings) >= 2 and handover.accepted_at > postings[1].created_at
            if third_handover:
                from .models import PointsThirdAddonFundingRevision
                revision = PointsThirdAddonFundingRevision.objects.filter(order=order, handover=handover).select_related('run').first()
                if not revision:
                    raise SettlementError('第三轮后的加购接替资金版本缺失')
            elif followup_handover:
                from .models import OrderPointsTransferFollowupFundingRevision
                from .points_transfer_followup_addon_version import latest_run_locked
                revision = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[1]).first()
                try:
                    latest = latest_run_locked(order, revision,
                        before=postings[2].created_at if len(postings) >= 3 else None) if revision else None
                except ValidationError as exc:
                    raise SettlementError('二次退款后的加购接替资金版本待核对') from exc
                if not latest:
                    raise SettlementError('二次退款后的加购接替资金版本缺失')
            elif (handover.accepted_at >= posting.created_at
                    or handover.payment.refund_records.filter(created_at__gte=posting.created_at).exists()):
                from .points_transfer_addon_events import later_runs
                try:
                    later = later_runs(order, posting)
                except ValidationError as exc:
                    raise SettlementError('主单退款后的加购退款资金版本待核对') from exc
                if (not later or (handover.accepted_at >= posting.created_at
                        and handover.pk not in {row.handover_id for row in later if row.handover_id})):
                    raise SettlementError('主单退款后的加购退款资金版本尚未接入')
            try:
                later_refund = handover.payment.refund_records.filter(created_at__gt=handover.accepted_at)\
                    .order_by('created_at', 'pk').first()
                from django.db.models import Q
                from .models import OrderPointsFundingAllocation
                # A transfer's real funding can be registered against either
                # the redemption or this service order. Never borrow a receipt
                # from a different order as a handover witness.
                handover_witness = OrderPointsFundingAllocation.objects.filter(
                    Q(redemption__order=order) | Q(service_order=order)).order_by('pk').first()
                if third_handover:
                    from .points_third_addon_receipt_usage import verify_run_locked
                    from .points_third_addon_responsibilities import current as third_responsibilities
                    verify_run_locked(revision.run)
                elif followup_handover:
                    from .points_transfer_followup_handover_scope import verify_event
                    verify_event(handover.payment, handover,
                        before=later_refund.created_at if later_refund else None,
                        receipt_id=handover_witness.receipt_id if handover_witness else None)
                else:
                    verify_handover(handover.payment, handover,
                        as_of=later_refund.created_at if later_refund else None,
                        receipt_id=handover_witness.receipt_id if handover_witness else None)
                _, addon_parts = (third_responsibilities(handover.payment) if third_handover
                                  else current_responsibilities(handover.payment))
            except ValidationError as exc:
                raise SettlementError('加购接替及实际履约份额待核对') from exc
            for part in addon_parts:
                segment = segments.get(part['segment_id'])
                if (not segment or segment['account_id'] != part['provider_id']
                        or ((part['basis'] or part['income']) and not segment['started_at'])):
                    raise SettlementError('加购接替的履约段待核对')
                if (part['basis'] or part['income']) and part['segment_id'] not in grouped[part['provider_id']]:
                    grouped[part['provider_id']].append(part['segment_id'])
    if not grouped:
        raise SettlementError('转单退款后没有可核验履约人员')
    shares = defaultdict(list)
    starts = {}
    for posting in postings:
        for row in posting.shares.all():
            shares[row.account_id].append(row)
            if posting.termination_in_service:
                starts[row.account_id] = posting.accepted_at
    if not set(shares).issubset(grouped):
        raise SettlementError('转单退款承担人与履约范围不一致')
    reports = {row.segment_id: row for row in OrderSegmentReport.objects.filter(
        segment_id__in=[pk for ids in grouped.values() for pk in ids]).select_related('segment')}
    cases = list(order.aftersales.filter(status='OPEN'))
    from .aftersale_scope import applies_to
    from .addon_main_segment_deduction import verified_deductions
    prior_addon = verified_deductions(order, before=postings[-1].created_at) if postings else {}
    if len(postings) >= 3:
        # The third-round event chain may contain later actual add-on refunds;
        # their main-segment deductions must reach the final payout once.
        prior_addon = verified_deductions(order)
    if len(postings) == 2:
        from .models import OrderPointsTransferFollowupFundingRevision
        from .points_transfer_followup_addon_version import latest_run_locked
        from django.utils.dateparse import parse_datetime
        followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=postings[-1]).first()
        latest = latest_run_locked(order, followup) if followup else None
        if latest:
            boundary = parse_datetime(latest.points_followup_snapshot.get('captured_at', ''))
            if not boundary:
                raise SettlementError('后续加购收益扣减时间待核对')
            prior_addon = verified_deductions(order, before=boundary)
    if len(postings) == 1:
        from .models import PointsPostRefundTransferRevision
        version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
        if version:
            prior_addon = verified_deductions(order, before=version.transfer.created_at)
        from .points_transfer_addon_events import later_runs
        try:
            later = later_runs(order, postings[0])
        except ValidationError as exc:
            raise SettlementError('主单退款后的加购资金版本待核对') from exc
        snapshot = later[-1].refunded_main_snapshot if later else None
        if (isinstance(snapshot, dict) and
                ((version and snapshot.get('transfer_revision_id') == version.pk) or
                 (not version and snapshot.get('transfer_origin_posting_id') == postings[0].pk))):
            from django.utils.dateparse import parse_datetime
            boundary = parse_datetime(snapshot.get('captured_at', ''))
            if not boundary:
                raise SettlementError('加购收益扣减时间待核对')
            prior_addon = verified_deductions(order, before=boundary)
    zero_termination = None
    from .models import PointsZeroFollowupTermination
    candidate = PointsZeroFollowupTermination.objects.filter(order=order, terminated_at__isnull=False).first()
    if candidate and isinstance(candidate.funding_snapshot, dict) and candidate.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1':
        from .points_transfer_zero_followup import verify_record_locked
        try:
            zero_termination = verify_record_locked(candidate)
        except ValidationError as exc:
            raise SettlementError('零退款终止及资金凭证待核对') from exc
    people = []
    for account_id, ids in sorted(grouped.items()):
        # A person's clock starts only once every one of their included
        # segments has ended; unrelated active segments wait for order end.
        endings = []
        for segment_id in ids:
            def participant_segments(posting):
                if isinstance(posting.participants, dict):
                    return {item.get('id') for item in posting.participants.get('facts', {}).get('segments', [])}
                if isinstance(posting.participants, list):
                    return {item.get('segment_id') for item in posting.participants if isinstance(item, dict)}
                return set()
            matching = [row.posting.accepted_at for row in shares.get(account_id, [])
                        if row.posting.termination_in_service and
                        segment_id in participant_segments(row.posting)
                        and
                        (not row.posting.proposal.affected_segment_ids
                         or segment_id in row.posting.proposal.affected_segment_ids)]
            if len(matching) > 1:
                raise SettlementError('同一履约段存在重复协商终止')
            if zero_termination and segment_id == zero_termination.segment_id:
                if matching:
                    raise SettlementError('同一履约段存在重复协商终止')
                matching = [zero_termination.accepted_at]
            endings.append(matching[0] if matching else order.completed_at)
        start = max(endings) if endings and all(endings) else None
        deadline = start + timedelta(hours=order.service_aftersale_hours) if start else None
        blockers = []
        if deadline is None:
            blockers.append('本人履约尚未结束')
        elif now < deadline:
            blockers.append('售后等待期尚未结束')
        if any(applies_to(case, account_id) for case in cases):
            blockers.append('本人相关售后尚未结束')
        submitted = []
        for pk in ids:
            report = reports.get(pk)
            submission = report.submissions.order_by('-version').first() if report else None
            segment = report.segment if report else None
            if (not report or report.status != 'APPROVED' or not submission or submission.status != 'APPROVED'
                    or submission.version != report.version or not submission.submitted_at or not submission.reviewed_at
                    or submission.submitted_at > now or submission.reviewed_at > now
                    or submission.reviewed_at < submission.submitted_at or not submission.reviewer_id
                    or not submission.entry_image or not submission.completion_image or not submission.result_images.exists()
                    or submission.entry_image.name != segment.entry_image.name
                    or submission.completion_image.name != segment.completion_image.name):
                blockers.append('本人履约报单未全部通过审核')
                break
            submitted.append(submission.submitted_at)
        freeze_until = max(submitted) + timedelta(hours=order.service_freeze_hours) if len(submitted) == len(ids) else None
        if freeze_until and now < freeze_until:
            blockers.append('本人报单冻结期尚未结束')
        income = sum(line[4] - prior_addon.get(line[0], 0)
                     for line in history.segment_shares if line[1] == account_id)
        if account_id in shares:
            for row in shares[account_id]:
                # A main refund can also consume confirmed add-on earnings.
                # Those amounts are paid through add-on settlement, not the
                # service segment, and must not be deducted twice.
                terms = getattr(row.posting.proposal, 'points_terms', None)
                addon = (sum(row.addon_deductions.values_list('amount', flat=True))
                         if terms and terms.addon_pending_plan is not None else 0)
                if addon > row.pending_deduction:
                    raise SettlementError('加购承担超过本人待结收益扣减')
                income -= row.pending_deduction - addon
        if income < 0:
            raise SettlementError('已占履约段收益超过本人剩余收益')
        people.append(PersonAmount(account_id, tuple(ids), income, freeze_until, tuple(blockers)))
    if sum(person.income for person in people) != funding.provider_income_units:
        raise SettlementError('退款后逐人收益与资金依据不一致')
    lines = tuple(SegmentAmount(*line) for line in history.segment_shares)
    shortfall = sum(item['fixed_fee_store_shortfall'] or 0 for item in facts['transfers'])
    return SegmentSettlementPlan(facts['deal_amount'], lines, tuple(people), funding.inviter_income_units,
                                 funding.store_income_units, shortfall), funding
