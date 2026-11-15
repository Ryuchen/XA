"""Verify a prior add-on refund and all paid add-ons before a main refund."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from stores.db import transaction
from .models import OrderAddonFundingReleaseRun, OrderAddonPayment, OrderPointsFundingAllocation


def verify_cumulative_sources(payment, sources):
    """Verify current source counters without treating later refunds as prior events."""
    totals = {row.pk: 0 for row in sources}
    kinds = {row.pk: row.kind for row in sources}
    for refund in payment.refund_records.select_related('proposal').all():
        plan = refund.proposal.source_plan
        if not isinstance(plan, list) or not plan:
            raise ValidationError('加购退款来源快照待核对')
        seen, total = set(), 0
        for item in plan:
            if not isinstance(item, dict):
                raise ValidationError('加购退款来源快照待核对')
            source_id, amount = item.get('allocation_id'), item.get('amount_raw')
            if (source_id not in totals or source_id in seen or item.get('kind') != kinds[source_id]
                    or type(amount) is not int or amount <= 0):
                raise ValidationError('加购退款来源快照待核对')
            seen.add(source_id)
            totals[source_id] += amount
            total += amount
        if total != refund.amount:
            raise ValidationError('加购退款来源金额待核对')
    if any(totals[row.pk] != row.refunded_amount or totals[row.pk] > row.amount for row in sources):
        raise ValidationError('加购退款来源累计待核对')


def verify_locked(order, *, boundary=None):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购退款历史须在订单资金事务内核对')
    boundary = boundary or timezone.now()
    if OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=boundary).exists():
        raise ValidationError('加购付款与主单退款时序待核对')
    payments = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order,
        buyer_transaction__created_at__lt=boundary).select_related('intent', 'buyer_transaction__wallet'))
    if not payments:
        raise ValidationError('加购付款记录待核对')
    from .addon_payment import verify_receipt
    refunded = []
    for payment in payments:
        if (not payment.buyer_transaction.created_at or payment.buyer_transaction.created_at >= boundary
                or payment.intent.order_id != order.pk):
            raise ValidationError('加购付款与主单退款时序待核对')
        verify_receipt(payment)
        refunds = list(payment.refund_records.select_for_update().filter(created_at__lt=boundary)
                       .select_related('proposal__case', 'buyer_transaction'))
        refunded.extend((payment, refund) for refund in refunds)
    if refunded and (len(refunded) > 1 or order.addon_intents.count() >= 2
                     or any(payment.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at).exists()
                            for payment, refund in refunded)):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .ordinary_multi_prior_addon_facts import verify
        try:
            verify(order, boundary)
        except DjangoValidationError as exc:
            raise ValidationError('主单前多笔加购退款凭证待核对') from exc
        return refunded[-1][1]
    if len(refunded) != 1:
        raise ValidationError('加购退款次数待核对')
    payment, refund = refunded[0]
    proposal, case = refund.proposal, refund.proposal.case
    if (refund.order_id != order.pk or not 0 < refund.amount <= payment.intent.amount
            or refund.created_at >= boundary or proposal.status != 'ACCEPTED'
            or proposal.amount != refund.amount or proposal.payment_id != payment.pk or case.order_id != order.pk
            or not proposal.case_request_id or not proposal.accepted_at or proposal.accepted_at > refund.created_at
            or proposal.created_at < proposal.case_opened_at):
        raise ValidationError('加购退款及原协商时序待核对')
    people = list(proposal.participants.all())
    decisions = list(proposal.decisions.all())
    if (len(people) != 1 or people[0].account_id != payment.intent.provider_id
            or len(decisions) != 2 or any(not row.decided_at or row.decided_at < proposal.created_at
                or row.decided_at > proposal.accepted_at for row in decisions)
            or proposal.accepted_at != max(row.decided_at for row in decisions)):
        raise ValidationError('加购退款双方确认凭证待核对')
    if refund.deposit_deduction:
        from wallet.models import RefundDepositDeficit, get_platform_wallet
        from wallet.refund_deposit import _verify_direct_deficit
        deficits = list(RefundDepositDeficit.objects.select_for_update().select_related('profile')
                        .filter(addon_refund=refund))
        if (len(deficits) != 1 or deficits[0].account_id != payment.intent.provider_id
                or deficits[0].amount != refund.deposit_deduction):
            raise ValidationError('加购退款押金凭证待核对')
        try:
            _verify_direct_deficit(deficits[0], get_platform_wallet(for_update=True))
        except ValueError as exc:
            raise ValidationError('加购退款押金凭证待核对') from exc
    sources = list(payment.allocations.select_for_update().order_by('pk'))
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    try:
        planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, 0) for row in sources], refund.amount)
    except FundingPlanError as exc:
        raise ValidationError('加购退款原付款构成待核对') from exc
    expected = [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
                for row in sources if planned[row.pk]]
    if proposal.source_plan != expected:
        raise ValidationError('加购退款原付款构成待核对')
    verify_cumulative_sources(payment, sources)
    if not isinstance(case.history, list):
        raise ValidationError('加购退款原售后凭证待核对')
    entries = [entry for entry in case.history if isinstance(entry, dict)
        and entry.get('created_at') == proposal.case_opened_at.isoformat()
        and entry.get('client_request_id') == proposal.case_request_id]
    if (len([row for row in entries if row.get('status') == 'OPEN']) != 1
            or len([row for row in entries if row.get('status') == 'RESOLVED']) != 1
            or any(row.get('affected_provider_account_ids') != [payment.intent.provider_id] for row in entries)):
        raise ValidationError('加购退款原售后凭证待核对')
    from console.models import AdminAuditLog
    proposal_path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/refund-proposals/'
    audit = list(AdminAuditLog.objects.filter(resource='addon-refund-proposal', object_id=str(proposal.pk),
                                               method='POST', path=proposal_path, status_code=200))
    posted = list(AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk),
        method='POST', path=f'{proposal_path}{proposal.pk}/execute/', status_code=200))
    if (len(audit) != 1 or audit[0].operator_account_id != proposal.created_by_id
            or audit[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                'amount': refund.amount, 'explanation': proposal.explanation}
            or len(posted) != 1 or posted[0].operator_account_id != refund.operator_id
            or posted[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                'proposal_id': proposal.pk, 'amount': refund.amount}):
        raise ValidationError('加购退款客服审计凭证待核对')

    # Reconstruct only allocations already present at the refund. Later
    # allocations have their own platform operation and are verified by the
    # normal order funding path.
    all_allocations = list(OrderPointsFundingAllocation.objects.select_for_update().filter(service_order=order).order_by('pk'))
    if any(row.created_at == refund.created_at for row in all_allocations):
        raise ValidationError('加购退款与补资分配时序待核对')
    allocations = [row for row in all_allocations if row.created_at < refund.created_at]
    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if bool(allocations) != bool(run):
        raise ValidationError('加购退款补资释放版本缺失')
    if run:
        from .addon_funding_release import verify_run
        verify_run(run)
        if ([row[0] for row in run.allocation_snapshot] != [row.pk for row in allocations]
                or any(len(row) != 4 or row[3] for row in run.allocation_snapshot)):
            raise ValidationError('加购退款之前的补资版本暂不支持')
        from .models import OrderAddonFundingRelease
        prior_releases = OrderAddonFundingRelease.objects.filter(
            allocation__in=allocations, run_id__lt=run.pk).select_related('run')
        # A prior amount alone is insufficient: replay every distinct source
        # with its immutable historical obligation, not today's total.
        prior_runs = {row.run_id: row.run for row in prior_releases}
        for previous in prior_runs.values():
            if not previous.ordinary_prestart_snapshot or not previous.ordinary_prestart_hash:
                raise ValidationError('加购退款之前的补资释放依据待核对')
            verify_run(previous)
        from .points_funding_plan import PointsFundingPlanError, plan_service_funding
        from .settlement import ProviderShare
        assignments = list(order.providers.select_for_update().order_by('pk'))
        from .settlement import compute_provider_income
        from engagement.funding_wallet import validate_order_funding_sources
        original = list(order.funding_allocations.select_for_update().select_related('lot'))
        validate_order_funding_sources(order, original)
        if (sum(row.amount for row in original) != order.amount
                or len(assignments) != order.required_provider_count
                or len({row.provider_account_id for row in assignments}) != len(assignments)
                or payment.intent.provider_id not in {row.provider_account_id for row in assignments}
                or sum(row.settlement_base for row in assignments) != order.amount
                or sum(row.provider_income for row in assignments) != order.provider_income
                or any(row.provider_income != compute_provider_income(row.settlement_base,
                    row.commission_type, row.commission_rate, row.commission_fixed) for row in assignments)):
            raise ValidationError('主单原收益或付款构成待核对')
        sources_by_kind = {kind: sum(row.amount for row in original if row.kind == kind)
                           for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
        try:
            main = plan_service_funding(deal_units=order.amount, point_discount_units=0,
                shares=[ProviderShare(row.settlement_base, row.commission_type, row.commission_rate,
                    row.commission_fixed, row.provider_income) for row in assignments],
                inviter_rate=rate, principal_units=sources_by_kind['PREPAID'], gift_units=sources_by_kind['GIFT'],
                compensation_units=sources_by_kind['COMPENSATION'])
            if main.inviter_income_units != order.inviter_commission:
                raise ValidationError('主单推荐佣金快照待核对')
            from .addon_funding_history import combined_at
            facts = combined_at(order, main, run.created_at, segment_shares=[(row.pk, row.provider_account_id, 0, 0, row.provider_income)
                                                                  for row in assignments])
            margin = facts['store_income_units'] - facts['additional_store_funding_units']
            rounding = sum(order.addon_refunds.filter(created_at__lt=run.created_at)
                           .values_list('rounding_surplus', flat=True))
            facts['additional_store_funding_units'] = max(rounding - margin, 0)
            facts['store_income_units'] = margin + facts['additional_store_funding_units']
        except PointsFundingPlanError as exc:
            raise ValidationError('加购退款后的历史补资需求待核对') from exc
        if (facts['additional_store_funding_units'] != run.required_amount
                or run.created_at <= refund.created_at or run.created_at >= boundary):
            raise ValidationError('加购退款后的历史补资需求待核对')
    return refund
