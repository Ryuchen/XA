"""Rebuild later ordinary team handovers from the previous actual shares."""
from django.core.exceptions import ValidationError


def verify(handover):
    from console.models import AdminAuditLog
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .ordinary_team_pre_main_handover import verify as verify_prior
    payment, order = handover.payment, handover.payment.intent.order
    mains = list(order.refund_records.filter(created_at__lte=handover.accepted_at).order_by('created_at', 'pk')) if handover.accepted_at else []
    if (handover.status != 'ACCEPTED' or not handover.previous_id or not handover.created_at
            or not handover.accepted_at or handover.created_at > handover.accepted_at
            or len(mains) > 1 or (mains and mains[0].created_at >= handover.created_at)
            or order.addon_refunds.filter(created_at__gte=handover.created_at,
                                          created_at__lte=handover.accepted_at).exists()
            or order.providers.filter(settled_at__lte=handover.accepted_at).exists()):
        raise ValidationError('后续普通加购接替时序异常')
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    if (not previous or previous.pk != handover.previous_id
            or previous.accepted_at >= handover.created_at):
        raise ValidationError('后续普通加购接替的前次版本异常')
    if order.refund_records.filter(created_at__lt=previous.accepted_at).exists():
        from .ordinary_refunded_team_handover import verify as verify_prior_refunded
        verify_prior_refunded(previous)
    else:
        verify_prior(previous)
    from .ordinary_pending_handover_refund import verify_retry
    if not verify_retry(payment, before_version=handover.version, before=handover.created_at):
        raise ValidationError('后续普通加购接替的历史版本异常')
    pre_boundary = mains[0].created_at if mains else handover.created_at
    if order.addon_refunds.filter(created_at__lt=pre_boundary).exists():
        from .ordinary_multi_prior_addon_facts import verify as verify_refunds
        verify_refunds(order, pre_boundary)
    if mains:
        from .models import NegotiationExecution, OrderAddonFundingReleaseRun, PointsHandoverRefundInvalidation
        from .ordinary_refund_receipt_evidence import verified_shares
        from .ordinary_addon_after_main_release import _digest
        execution = NegotiationExecution.objects.filter(refund=mains[0], termination_processed_at__isnull=False).first()
        if (not execution or execution.termination_processed_at >= handover.created_at
                or len(verified_shares(execution)) != 1
                or verified_shares(execution)[0].account_id != handover.source_segment.provider_account_id):
            raise ValidationError('后续接替的主单终止来源异常')
        for refund in order.addon_refunds.filter(created_at__gt=mains[0].created_at, created_at__lt=handover.created_at):
            event = PointsHandoverRefundInvalidation.objects.filter(refund=refund, handover__payment=payment,
                handover__version__lt=handover.version).first()
            run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
            if (not event or not run or run.created_at >= handover.created_at
                    or not isinstance(run.ordinary_refund_snapshot, dict)
                    or run.ordinary_refund_snapshot.get('kind') != 'ordinary_addon_after_main_refund_v1'
                    or run.ordinary_refund_hash != _digest(run, run.ordinary_refund_snapshot)):
                raise ValidationError('后续接替前退款资金版本异常')
    active = [row for row in shares if row['is_current']]
    source, target = handover.source_segment, handover.target_segment
    if (len(active) != 1 or active[0]['segment_id'] != source.pk or active[0]['basis'] != handover.source_basis_before
            or source.attribution.order_id != order.pk or target.attribution.order_id != order.pk
            or source.provider_account_id == target.provider_account_id or not source.started_at or not target.started_at
            or target.started_at > handover.created_at or (not mains and source.released_at and source.released_at < handover.accepted_at)
            or (target.released_at and target.released_at < handover.accepted_at)
            or not handover.transferred_basis or handover.retained_basis + handover.transferred_basis != active[0]['basis']
            or not handover.remaining_work or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('后续普通加购接替本人份额异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected_parties = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                        (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, accepted) for a, role, accepted, _ in decisions} != expected_parties
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('后续普通加购接替三方确认异常')
    try:
        commission = _commission(target, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('后续普通加购接替抽成异常') from exc
    source_row = active[0]
    expected = [row for row in shares if not row['is_current']]
    if handover.retained_basis:
        full = _income(source_row['basis'], source_row['commission_snapshot'])
        income = (_income(handover.retained_basis, source_row['commission_snapshot']) if source_row['income'] == full
                  else source_row['income'] * handover.retained_basis // source_row['basis'])
        expected.append({**source_row, 'basis': handover.retained_basis, 'income': income, 'is_current': False})
    expected.append({'segment_id': target.pk, 'basis': handover.transferred_basis,
                     'income': _income(handover.transferred_basis, commission),
                     'commission_snapshot': commission, 'is_current': True})
    rows = list(handover.shares.order_by('position'))
    if ([row.position for row in rows] != list(range(len(rows)))
            or [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows]
               != [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], row['is_current'])
                   for row in expected]):
        raise ValidationError('后续普通加购接替逐段收益异常')
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/', method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': source.provider_account_id,
            'target_provider': target.provider_account_id, 'retained_basis': handover.retained_basis,
            'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('后续普通加购接替审计异常')
    return handover


def can_start(payment):
    from .ordinary_pending_handover_refund import verify_retry
    return verify_retry(payment)
