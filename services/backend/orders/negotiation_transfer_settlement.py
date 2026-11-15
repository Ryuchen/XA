"""Test-only net segment plan for a verified ordinary transfer refund."""
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import Order, OrderSegmentReport
from .negotiation_execution import verify_execution
from .negotiation_settlement import execution_for_order
from .negotiation_termination import verify_termination_evidence
from .negotiation_transfer_evidence import verify_locked
from .negotiation_transfer_preview import _require_isolated
from .segment_settlement_plan import PersonAmount, SegmentAmount, SegmentSettlementPlan
from .settlement import SettlementError


def build_locked(order, *, now=None):
    try:
        _require_isolated()
    except ValidationError as exc:
        raise SettlementError('普通转单退款收益尚未开放') from exc
    if (not transaction.get_connection().in_atomic_block or order.status != Order.Status.COMPLETED
            or not order.completed_at or order.service_aftersale_hours is None or order.service_freeze_hours is None):
        raise SettlementError('转单退款后的结算条件尚未满足')
    execution = execution_for_order(order, lock=True)
    if not execution or not execution.refund_id or not execution.termination_processed_at:
        raise SettlementError('转单退款协商凭证缺失')
    try:
        shares = verify_execution(execution)
        verify_termination_evidence(execution)
        evidence = verify_locked(execution)
    except ValidationError as exc:
        raise SettlementError('转单退款协商凭证待核对') from exc
    if not evidence:
        raise SettlementError('转单退款凭证缺失')
    snapshot, now = evidence.snapshot, now or timezone.now()
    # A subsequent transfer has its own economics. Never pay the old frozen
    # segment plan as if that later assignment had not happened.
    current_segments = set(order.original_attribution.segments.values_list('pk', flat=True))
    if current_segments != {item['id'] for item in snapshot['segments']}:
        raise SettlementError('后续转单已产生新履约段，须按新逐段资金版本结算')
    from .ordinary_transfer_capability import permits as production_permits
    if production_permits(order):
        from dataclasses import asdict
        from .ordinary_transfer_refund_funding import plan_locked
        from .points_funding_plan import PointsFundingPlanError
        try:
            from .models import OrderAddonPayment
            if OrderAddonPayment.objects.filter(intent__order=order).exists():
                from .ordinary_transfer_addon_funding import plan_locked as addon_plan_locked
                addon = addon_plan_locked(order.pk, allow_finalized=bool(order.settled_at))
                main_funding, current_funding = addon.main, addon.combined
            else:
                addon = None
                main_funding = current_funding = plan_locked(order.pk, allow_finalized=bool(order.settled_at))
        except PointsFundingPlanError as exc:
            raise SettlementError('转单退款后的补资凭证待核对') from exc
        if current_funding.release_units or current_funding.remaining_units:
            raise SettlementError('转单退款后的门店补资尚未结清')
        funding = asdict(current_funding)
        if addon:
            funding['addons'] = list(addon.addon_parts)
            funding['addon_history'] = list(addon.addon_history)
            funding['main_deductions'] = list(addon.main_deductions)
            funding['main_provider_income_units'] = main_funding.provider_income_units
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .ordinary_transfer_addon_rounding import facts as addon_rounding_facts
            try:
                funding['addon_rounding'] = addon_rounding_facts(order)
            except DjangoValidationError as exc:
                raise SettlementError('转单加购退款取整事实待核对') from exc
    else:
        addon = None
        funding = snapshot['funding_if_recovered']
        if funding['required_units'] or funding['allocated_units']:
            raise SettlementError('转单退款后的资金来源或补资尚待核对')
    main_provider_income = funding.get('main_provider_income_units', funding['provider_income_units'])
    main_external = (main_funding.planned_provider_external_units if addon else funding['planned_provider_external_units'])
    if (main_external != sum(row.wallet_deduction + row.deposit_deduction for row in shares)
            or sum(row.pending_deduction for row in shares) + main_provider_income !=
               sum(row['income'] for row in snapshot['lines'])):
        raise SettlementError('转单退款后的资金来源或补资尚待核对')
    rows_by_kind = defaultdict(int)
    for row in order.funding_allocations.all():
        rows_by_kind[row.kind] += row.refunded_amount
    if any(rows_by_kind[key] != amount for key, amount in snapshot['return_sources'].items()):
        raise SettlementError('原付款退款来源已变化')
    segments = {row['id']: row for row in snapshot['segments']}
    outgoing = {row['previous_segment_id'] for row in snapshot['transfers']}
    addon_segments = {part['segment_id'] for part in funding.get('addons', ())}
    grouped = defaultdict(list)
    for segment in segments.values():
        if segment['started_at'] and (segment['id'] not in outgoing or segment['original_share'] > 0
                                      or segment['id'] in addon_segments):
            grouped[segment['account_id']].append(segment['id'])
    affected = {row.account_id: row for row in shares}
    if not grouped or not set(affected).issubset(grouped):
        raise SettlementError('转单退款履约人员待核对')
    reports = {row.segment_id: row for row in OrderSegmentReport.objects.filter(
        segment_id__in=[pk for ids in grouped.values() for pk in ids]).select_related('segment')}
    cases = list(order.aftersales.filter(status='OPEN'))
    from .aftersale_scope import applies_to
    people = []
    for account_id, ids in sorted(grouped.items()):
        selected = set(execution.proposal.affected_segment_ids or [])
        # A service already completed before the agreement keeps its original
        # after-sale clock. A service terminated earlier uses that actual time.
        start = (min(execution.accepted_at, order.completed_at)
                 if ids and all(pk in selected for pk in ids) else order.completed_at)
        blockers, timestamps = [], []
        if now < start + timedelta(hours=order.service_aftersale_hours):
            blockers.append('售后等待期尚未结束')
        if any(applies_to(case, account_id) for case in cases):
            blockers.append('本人相关售后尚未结束')
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
            timestamps.append(submission.submitted_at)
        freeze = max(timestamps) + timedelta(hours=order.service_freeze_hours) if len(timestamps) == len(ids) else None
        if freeze and now < freeze:
            blockers.append('本人报单冻结期尚未结束')
        income = sum(row['income'] for row in snapshot['lines'] if row['account_id'] == account_id)
        income -= snapshot.get('prior_addon_deductions', {}).get(str(account_id), 0)
        if account_id in affected:
            income -= affected[account_id].pending_deduction
        later_deductions = dict(funding.get('main_deductions', ())).get(account_id, 0)
        income -= max(later_deductions - snapshot.get('prior_addon_deductions', {}).get(str(account_id), 0), 0)
        if income < 0:
            raise SettlementError('加购退款扣减超过本人主单保留收益')
        people.append(PersonAmount(account_id, tuple(ids), income, freeze, tuple(blockers)))
    addon_income = sum(part['provider_income_units'] for part in funding.get('addons', ()))
    if sum(person.income for person in people) + addon_income != funding['provider_income_units']:
        raise SettlementError('退款后逐人收益与资金依据不一致')
    lines = tuple(SegmentAmount(**row) for row in snapshot['lines'])
    plan = SegmentSettlementPlan(order.amount, lines, tuple(people), funding['inviter_income_units'],
                                 funding['store_income_units'], 0)
    return plan, funding
