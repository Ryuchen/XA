"""Build the current segment settlement after later ordinary refunds."""
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import (NegotiationExecution, OrderAddonPayment, OrderSegmentReport,
                     OrdinaryTransferFollowupRound)
from .settlement import SettlementError
from .segment_settlement_plan import PersonAmount, SegmentSettlementPlan, build_segment_settlement_plan


def build_locked(order, *, now=None):
    if not transaction.get_connection().in_atomic_block or order.status != order.Status.COMPLETED:
        raise SettlementError('整单尚未完成或订单事务未锁定')
    row = (OrdinaryTransferFollowupRound.objects.select_for_update().filter(
        order=order, execution__termination_processed_at__isnull=False).select_related('execution')
        .order_by('-sequence', '-pk').first())
    if not row:
        raise SettlementError('追加协商逐轮凭证缺失')
    from .ordinary_transfer_followup_revision import verify_locked as verify_revision
    try:
        verify_revision(row.settlement_revision)
        from .points_funding_evidence import _verified_allocations_for_locked_order
        from .addon_funding_release import addon_release_totals, verify_run
        allocations = _verified_allocations_for_locked_order(order)
        from .models import (OrderAddonFundingReleaseRun, OrderPointsFundingRelease,
                             OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
        for refund in order.addon_refunds.filter(created_at__gt=row.execution.termination_processed_at):
            event = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if (not event or not isinstance(event.ordinary_refund_snapshot, dict)
                    or event.ordinary_refund_snapshot.get('kind') != 'ordinary_followup_addon_refund_v1'):
                raise ValidationError('追加协商后加购退款资金版本缺失')
            verify_run(event)
            from .ordinary_followup_addon_receipt import verify as verify_refund_receipt
            allocation_by_id = {item.pk: item for item in allocations}
            if event.allocation_snapshot:
                witness = allocation_by_id.get(event.allocation_snapshot[0][0])
                if not witness:
                    raise ValidationError('加购退款历史到账分配缺失')
                verify_refund_receipt(event, witness.receipt_id)
            else:
                verify_refund_receipt(event, None)
        releases = addon_release_totals(allocations)
        allocated = sum(item.amount - releases[item.pk]
            - sum(OrderPointsFundingRelease.objects.filter(allocation=item).values_list('amount', flat=True))
            - sum(OrderPointsTransferFundingRelease.objects.filter(allocation=item).values_list('amount', flat=True))
            - sum(PointsPostRefundTransferRelease.objects.filter(allocation=item).values_list('amount', flat=True))
            for item in allocations)
        from .ordinary_transfer_followup_funding import plan_locked
        funding, history = plan_locked(row.pk, allocated=allocated)
    except (ValidationError, ValueError) as exc:
        raise SettlementError('追加协商资金或原结算清单待核对') from exc
    if funding.remaining_units or funding.release_units:
        raise SettlementError('整单门店补资尚未足额或多余分配尚未释放')
    plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_recorded_refunds=True)
    if [asdict(line) for line in plan.segments] != row.facts['segment_lines']:
        raise SettlementError('追加协商后履约段已变化，请先核对新转单事实')
    accounts = {int(key): value for key, value in row.facts['outstanding_income'].items()}
    for share in row.execution.shares.all():
        accounts[share.account_id] -= share.pending_deduction
    from .addon_main_deduction import deductions_at
    recorded = {int(key): value for key, value in row.facts['addon_main_deductions'].items()}
    for account, amount in deductions_at(order).items():
        extra = amount - recorded.get(account, 0)
        if account not in accounts or extra < 0:
            raise SettlementError('加购退款的主单收益归属待核对')
        accounts[account] -= extra
    receipts = list(NegotiationExecution.objects.filter(pk__in=[*row.facts['prior_execution_ids'], row.execution_id])
                    .prefetch_related('shares'))
    from .ordinary_transfer_end_scope import endings
    ended_segments = endings(order, receipts)
    addon_parts = defaultdict(int)
    from .addon_handover import assert_no_pending_handover, current_responsibilities
    for payment in OrderAddonPayment.objects.select_for_update().filter(intent__order=order).order_by('pk'):
        try:
            assert_no_pending_handover(payment)
            _, responsibilities = current_responsibilities(payment, lock=True)
            for item in responsibilities:
                addon_parts[(payment.pk, item['segment_id'], item['provider_id'])] += item['income']
                if item['is_current'] and item['segment_id'] in ended_segments:
                    from .addon_completion import verify_accepted
                    execution = ended_segments[item['segment_id']]
                    if not execution:
                        raise ValidationError('加购负责人终止凭证缺失')
                    verify_accepted(order, payment, execution, item)
        except ValidationError as exc:
            raise SettlementError('加购完成或交接尚未确认') from exc
    addon_segments = {segment for _, segment, _ in addon_parts}
    segments = list(order.original_attribution.segments.select_for_update().order_by('pk'))
    grouped = defaultdict(list)
    for segment in segments:
        if segment.started_at and (not segment.released_at or segment.original_share or segment.pk in addon_segments):
            grouped[segment.provider_account_id].append(segment.pk)
    reports = {report.segment_id: report for report in OrderSegmentReport.objects.filter(
        segment_id__in=[pk for ids in grouped.values() for pk in ids]).select_related('segment')}
    from .aftersale_scope import applies_to
    cases, now, people = list(order.aftersales.filter(status='OPEN')), now or timezone.now(), []
    for account, ids in sorted(grouped.items()):
        segment_ends = [ended_segments[pk].accepted_at if pk in ended_segments else order.completed_at for pk in ids]
        start = max(segment_ends) if segment_ends and all(segment_ends) else None
        blockers, timestamps = [], []
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
            timestamps.append(submission.submitted_at)
        freeze = max(timestamps) + timedelta(hours=order.service_freeze_hours) if len(timestamps) == len(ids) else None
        if freeze and now < freeze:
            blockers.append('本人报单冻结期尚未结束')
        if account not in accounts or accounts[account] < 0:
            raise SettlementError('追加协商后的陪玩收益待核对')
        people.append(PersonAmount(account, tuple(ids), accounts[account], freeze, tuple(blockers)))
    parts = [{'payment_id': payment, 'segment_id': segment, 'provider_account_id': account,
              'provider_income_units': amount} for (payment, segment, account), amount in sorted(addon_parts.items())]
    if sum(item.income for item in people) + sum(item['provider_income_units'] for item in parts) != funding.provider_income_units:
        raise SettlementError('逐段收益与整单补资不守恒')
    plan = SegmentSettlementPlan(plan.deal_amount, plan.segments, tuple(people), funding.inviter_income_units,
                                 funding.store_income_units, plan.fixed_fee_shortfall)
    details = asdict(funding)
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
    try:
        addon_rounding = addon_rounding_facts(order)
    except DjangoValidationError as exc:
        raise SettlementError('追加协商加购退款取整凭证异常') from exc
    details.update(addons=parts, addon_history=history.get('addon_history', []), followup_round_id=row.pk,
                   addon_rounding=addon_rounding)
    return plan, details
