"""Read a funded, partly paid points-transfer manifest for operating ROI."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def verified_partial(order, manifest, start, end):
    from orders.models import PointsVoluntaryPaidRefundEvidence
    from orders.points_transfer_followup_settlement import verify_manifest_locked
    from orders.points_transfer_refund_evidence import _prior_settlement
    from orders.points_voluntary_paid_evidence import verify_current_read
    from orders.segment_addon_settlement_revision import verify_revisions
    from orders.segment_settlement import _hash, _points_transfer_terms, _terms, _validate_records

    if (order.settled_at or manifest.finalized_at or manifest.order_id != order.pk
            or not isinstance(manifest.plan, dict) or manifest.plan_hash != _hash(manifest.plan)):
        raise ValidationError('部分逐段结算清单状态待核对')
    verify_revisions(manifest)
    plan, funding = _points_transfer_terms(order)
    if funding is None:
        raise ValidationError('部分逐段结算补资尚未核实')
    terms = _terms(order, plan, points_funding=funding)
    if terms != manifest.plan or manifest.plan_hash != _hash(terms):
        raise ValidationError('部分逐段结算资金版本待核对')
    if 'followup_posting_id' in funding:
        from orders.models import OrderPointsTransferFollowupFundingRevision
        revision = OrderPointsTransferFollowupFundingRevision.objects.get(posting_id=funding['followup_posting_id'])
        verify_manifest_locked(order, manifest, terms, revision.prior_manifest)
    paid = _validate_records(order, manifest, plan)
    if not paid or len(paid) >= len(plan.persons):
        raise ValidationError('部分逐段结算人员待核对')
    snapshot = _prior_settlement(order)
    if not snapshot or {row['account_id'] for row in snapshot['people']} != set(paid):
        raise ValidationError('部分逐段结算实付快照待核对')
    first_paid = min(row.settled_at for row in paid.values())
    for refund in order.addon_refunds.filter(created_at__gt=first_paid):
        evidence = PointsVoluntaryPaidRefundEvidence.objects.filter(refund=refund, manifest=manifest).select_related(
            'funding_run__refund__order').first()
        if not evidence or not verify_current_read(evidence.funding_run):
            raise ValidationError('部分结算后的加购退款缺少已付凭证')
    addon_records = list(manifest.addon_settlements.select_related('income_transaction'))
    txs = [row.income_transaction for row in paid.values() if row.income_transaction_id]
    txs += [row.income_transaction for row in addon_records if row.income_transaction_id]
    pending = [person for person in plan.persons if person.account_id not in paid]
    parts = funding.get('addons', [])
    pending_segments = sorted({segment for person in pending for segment in person.segment_ids})
    return plan, {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
        'status': 'PARTIALLY_SETTLED',
        'settled_segment_ids': sorted({segment for person in paid.values() for segment in person.segment_ids}),
        'income_posted_in_period_units': sum(tx.amount for tx in txs if start <= tx.created_at < end),
        'pending_segment_id': pending_segments[0] if len(pending_segments) == 1 else None,
        'pending_segment_ids': pending_segments,
        'pending_main_units': sum(person.income for person in pending),
        'pending_addon_units': sum(part['provider_income_units'] for part in parts
                                   if part['provider_account_id'] not in paid)}
