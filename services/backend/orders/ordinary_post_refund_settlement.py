"""Settle the latest verified post-refund transfer funding version."""
from collections import defaultdict
from dataclasses import asdict
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import NegotiationExecution, OrderAddonPayment, OrderSegmentReport
from .segment_settlement_plan import PersonAmount, SegmentSettlementPlan, build_segment_settlement_plan
from .settlement import SettlementError


def build_locked(order, *, now=None):
    if not transaction.get_connection().in_atomic_block or order.status != order.Status.COMPLETED:
        raise SettlementError('整单尚未完成或订单事务未锁定')
    from .ordinary_post_refund_transfer import current_locked
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .addon_funding_release import addon_release_totals
    try:
        revision = current_locked(order)
        if not revision:
            raise ValidationError('后续转单资金版本缺失')
        allocations = _verified_allocations_for_locked_order(order)
        releases = addon_release_totals(allocations)
    except ValidationError as exc:
        raise SettlementError('后续转单资金版本待核对') from exc
    allocated = sum(item.amount - releases[item.pk] for item in allocations)
    funding = dict(revision.snapshot['funding'])
    if allocated != funding['required_units']:
        raise SettlementError('后续转单门店补资尚未足额或多余资金尚未释放')
    zero_pretransfer_id = revision.snapshot.get('zero_pretransfer_execution_id')
    if zero_pretransfer_id:
        from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
        addon_rounding = addon_rounding_facts(order)
    else:
        addon_rounding = revision.snapshot['addon_rounding']
    funding.update(allocated_units=allocated, remaining_units=0, release_units=0,
                   post_refund_revision_id=revision.pk, addon_history=revision.snapshot['addon_history'],
                   addon_rounding=addon_rounding)
    plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_recorded_refunds=True)
    if [asdict(line) for line in plan.segments] != revision.snapshot['lines']:
        raise SettlementError('转单履约段已变化')
    amounts = defaultdict(int)
    for line in plan.segments:
        amounts[line.account_id] += line.income
    for account, pending in revision.snapshot.get('pending', []):
        amounts[account] -= pending
    from .addon_main_deduction import deductions_at
    for account, amount in deductions_at(order).items():
        amounts[account] -= amount
    receipt_ids = ([zero_pretransfer_id] if zero_pretransfer_id and revision.snapshot.get('pre_main_transfer_v1')
                   else revision.snapshot['receipts'])
    receipts = list(NegotiationExecution.objects.filter(pk__in=receipt_ids).prefetch_related('shares'))
    from .ordinary_transfer_end_scope import endings
    ended_segments = endings(order, receipts)
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    parts = defaultdict(int)
    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).order_by('pk'):
        try:
            assert_no_pending_handover(payment)
            _, responsibilities = current_responsibilities(payment, lock=True)
            for part in responsibilities:
                parts[(payment.pk, part['segment_id'], part['provider_id'])] += part['income']
                if part['is_current'] and part['segment_id'] in ended_segments:
                    receipt = ended_segments[part['segment_id']]
                    if not receipt:
                        raise ValidationError('加购终止凭证缺失')
                    from .addon_completion import verify_accepted
                    verify_accepted(order, payment, receipt, part)
        except ValidationError as exc:
            raise SettlementError('加购完成或交接尚未确认') from exc
    addon_segments = {segment for _, segment, _ in parts}
    grouped = defaultdict(list)
    for segment in order.original_attribution.segments.select_for_update().order_by('pk'):
        if segment.started_at and (not segment.released_at or segment.original_share or segment.pk in addon_segments):
            grouped[segment.provider_account_id].append(segment.pk)
    reports = {report.segment_id: report for report in OrderSegmentReport.objects.filter(
        segment_id__in=[pk for ids in grouped.values() for pk in ids]).select_related('segment')}
    from .aftersale_scope import applies_to
    cases, now, people = list(order.aftersales.filter(status='OPEN')), now or timezone.now(), []
    for account, ids in sorted(grouped.items()):
        segment_ends = [min(ended_segments[pk].accepted_at, order.completed_at)
                        if pk in ended_segments and order.completed_at else
                        ended_segments[pk].accepted_at if pk in ended_segments else order.completed_at for pk in ids]
        start = max(segment_ends) if segment_ends and all(segment_ends) else None
        blockers, times = [], []
        if not start or now < start + timedelta(hours=order.service_aftersale_hours):
            blockers.append('售后等待期尚未结束')
        if any(applies_to(case, account) for case in cases):
            blockers.append('本人相关售后尚未结束')
        for segment_id in ids:
            report = reports.get(segment_id)
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
            times.append(submission.submitted_at)
        freeze = max(times) + timedelta(hours=order.service_freeze_hours) if len(times) == len(ids) else None
        if freeze and now < freeze:
            blockers.append('本人报单冻结期尚未结束')
        if amounts[account] < 0:
            raise SettlementError('陪玩退款后收益异常')
        people.append(PersonAmount(account, tuple(ids), amounts[account], freeze, tuple(blockers)))
    addon_parts = [{'payment_id': payment, 'segment_id': segment, 'provider_account_id': account,
                    'provider_income_units': value} for (payment, segment, account), value in sorted(parts.items())]
    if sum(item.income for item in people) + sum(item['provider_income_units'] for item in addon_parts) != funding['provider_income_units']:
        raise SettlementError('后续转单逐段收益与整单补资不守恒')
    funding['addons'] = addon_parts
    from .models import OrdinaryTransferFollowupRound
    last_round = OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False).order_by('-sequence').first()
    if last_round:
        funding['followup_round_id'] = last_round.pk
    return SegmentSettlementPlan(plan.deal_amount, plan.segments, tuple(people), funding['inviter_income_units'],
                                 funding['store_income_units'], plan.fixed_fee_shortfall), funding
