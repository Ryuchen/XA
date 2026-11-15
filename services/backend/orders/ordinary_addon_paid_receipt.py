"""Read-only historical evidence for a paid person's add-on refund."""
from rest_framework.exceptions import ValidationError


def verify_run(run):
    """Verify the paid snapshot without acquiring an order or wallet lock."""
    if not run.refund_id:
        return
    from .models import OrdinaryAddonPaidRefundEvidence, OrderPointsSettlementPerson, OrderAddonSettlement
    from .ordinary_addon_paid_refund import digest, posting_active
    refund = run.refund
    evidence = OrdinaryAddonPaidRefundEvidence.objects.filter(run=run).select_related(
        'settlement', 'refund__proposal__case', 'refund__order').first()
    if not evidence:
        paid = OrderPointsSettlementPerson.objects.filter(settlement__order_id=refund.order_id,
            settlement__redemption__isnull=True, settled_at__lt=refund.created_at).exists()
        if paid and not posting_active(run):
            raise ValidationError('已付加购退款缺少历史结算凭证')
        return
    snapshot = evidence.snapshot
    if (not isinstance(snapshot, dict) or digest(snapshot) != evidence.evidence_hash
            or evidence.refund_id != refund.pk or evidence.settlement.order_id != refund.order_id
            or snapshot.get('order_id') != refund.order_id or snapshot.get('settlement_id') != evidence.settlement_id
            or snapshot.get('case_id') != refund.proposal.case_id or not refund.proposal.case_opened_at
            or snapshot.get('case_opened_at') != refund.proposal.case_opened_at.isoformat()
            or not refund.created_at or run.created_at <= refund.created_at):
        raise ValidationError('已付加购退款历史凭证不一致')
    people = list(OrderPointsSettlementPerson.objects.filter(settlement=evidence.settlement,
        settled_at__lt=refund.created_at).select_related('segment', 'report', 'income_transaction__wallet').order_by('pk'))
    addons = list(OrderAddonSettlement.objects.filter(order_settlement=evidence.settlement,
        settled_at__lt=refund.created_at).select_related('payment__intent', 'segment', 'report',
        'income_transaction__wallet').order_by('pk'))
    if (not people or snapshot.get('paid_people') != [[p.pk, p.segment_id, p.segment.provider_account_id,
            p.amount, p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in people]
            or snapshot.get('paid_addons') != [[p.pk, p.payment_id, p.segment_id, p.amount,
            p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in addons]
            or set(refund.proposal.participants.values_list('account_id', flat=True)).intersection(
                p.segment.provider_account_id for p in people)):
        raise ValidationError('已付陪玩及其加购收益与历史凭证不一致')
    from .models import OrderSegmentFinancialSnapshot
    from .points_settlement_test import _verify_person
    from .settlement import SettlementError
    from wallet.models import Transaction
    order = refund.order
    financial = {p.segment_id: p for p in OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution__order=order).select_related('segment')}
    assignments = {p.provider_account_id: p for p in order.providers.all()}
    by_report = {p.report_id: p for p in people}
    try:
        for person in people:
            fact = financial.get(person.segment_id)
            assignment = assignments.get(person.segment.provider_account_id)
            if not fact or not assignment or fact.income_at_entry < person.amount:
                raise SettlementError('已付收益缺少真实履约依据')
            _verify_person(order, person, fact, assignment, deduction=fact.income_at_entry - person.amount)
        for item in addons:
            person, tx = by_report.get(item.report_id), item.income_transaction
            if (not person or person.segment_id != item.segment_id or item.payment.intent.order_id != order.pk
                    or item.report.order_id != order.pk or item.report.provider_account_id != person.segment.provider_account_id
                    or (item.amount == 0 and tx) or (item.amount > 0 and (not tx or tx.order_id != order.pk
                    or tx.wallet.account_id != person.segment.provider_account_id
                    or tx.tx_type != Transaction.TxType.INCOME or tx.status != Transaction.Status.SUCCESS
                    or tx.amount != item.amount or tx.balance_after != tx.balance_before + tx.amount))):
                raise SettlementError('已付加购收入流水不一致')
    except SettlementError as exc:
        raise ValidationError('已付陪玩历史收入待核对') from exc
    allocations = run.allocation_snapshot
    if not isinstance(allocations, list):
        raise ValidationError('退款前到账分配缺失')
    try:
        uses = [[row[0], row[1] - row[2]] for row in allocations if row[1] > row[2]]
    except (TypeError, IndexError):
        raise ValidationError('退款前到账分配异常')
    if sorted(uses) != snapshot.get('funding_uses'):
        raise ValidationError('退款前到账分配与已付清单不一致')
    # Rebuild the pre-refund terms, not today's mutable settlement aggregate.
    from .points_funding_plan import PointsFundingPlan, PointsFundingPlanError
    from .addon_funding_history import combined_at
    try:
        frozen = run.ordinary_prestart_snapshot
        main = PointsFundingPlan(**frozen['main'])
        before = combined_at(order, main, refund.created_at, segment_shares=frozen['shares'])
        history = before.get('addon_history', [])
        rounding = sum(order.addon_refunds.filter(created_at__lt=refund.created_at)
                       .values_list('rounding_surplus', flat=True))
        margin = before['store_income_units'] - before['additional_store_funding_units']
        required = max(rounding - margin, 0)
        expected = [main.customer_coin_units + sum(x['paid_units'] for x in history),
            main.principal_units + sum(x['principal_units'] for x in history),
            sum(x['recovery_units'] for x in history),
            main.gift_units + sum(x['gift_units'] for x in history),
            main.compensation_units + sum(x['compensation_units'] for x in history), required,
            main.provider_income_units - sum(x[1] for x in before['main_deductions'])
                + sum(x['provider_units'] for x in history),
            main.inviter_income_units + sum(x['inviter_units'] for x in history), margin + required]
    except (TypeError, KeyError, ValueError, PointsFundingPlanError) as exc:
        raise ValidationError('退款前资金历史无法核实') from exc
    if expected != snapshot.get('settlement_facts'):
        raise ValidationError('退款前结算金额与真实资金历史不一致')
    return snapshot
