"""Verified add-on responsibility changes as period cost events."""
from rest_framework.exceptions import ValidationError


def period_delta(order, manifest, addons, start, end):
    from orders.addon_handover import current_responsibilities
    from orders.models import OrderAddonFundingReleaseRun

    if not isinstance(manifest.plan, dict):
        raise ValidationError('加购换人结算清单缺失')
    funding = manifest.plan.get('points_funding') or manifest.plan.get('ordinary_transfer_funding') or {}
    parts = funding.get('addons')
    total = 0
    from orders.models import OrderTransferNegotiationEvidence, OrdinaryPostRefundTransferRevision
    first = (OrderTransferNegotiationEvidence.objects.filter(order=order)
             .select_related('execution').first())
    cutoff = first.execution.termination_processed_at if first else None

    def verify_event_version(kind, event_id, happened_at):
        if cutoff and happened_at > cutoff:
            versions = list(OrdinaryPostRefundTransferRevision.objects.filter(
                order=order, source_kind=kind, source_id=event_id).select_related('transfer__previous_segment__attribution', 'previous'))
            if len(versions) != 1:
                raise ValidationError('加购事件资金版本缺失')
            from orders.ordinary_post_refund_transfer import verify_locked
            verify_locked(versions[0])
        elif cutoff and happened_at == cutoff:
            raise ValidationError('加购事件与主单结案时序待核对')

    for addon in addons:
        refunds = list(addon.refund_records.order_by('created_at', 'pk'))
        for refund in refunds:
            verify_event_version('ADDON_REFUND', refund.pk, refund.created_at)
        accepted = list(addon.handovers.filter(status='ACCEPTED')
                        .select_related('source_segment__attribution', 'target_segment__attribution')
                        .order_by('version', 'pk'))
        if not accepted:
            continue
        if not isinstance(parts, list):
            raise ValidationError('加购结算清单尚待核对')
        current, shares = current_responsibilities(addon, lock=True)
        from orders.addon_payment import verify_receipt
        verify_receipt(addon)
        own_parts = [part for part in parts if isinstance(part, dict) and part.get('payment_id') == addon.pk]
        history = funding.get('addon_history')
        historical = ([row for row in history if isinstance(row, dict) and row.get('payment_id') == addon.pk]
                      if isinstance(history, list) else [])
        recorded_refunds = (historical[0].get('refund_ids') if len(historical) == 1
                            else funding.get('addon_refund_ids'))
        share_map = {}
        for item in shares:
            key = (item['segment_id'], item['provider_id'])
            share_map[key] = share_map.get(key, 0) + item['income']
        part_map = {(item['segment_id'], item['provider_account_id']): item['provider_income_units']
                    for item in own_parts}
        if (not current or current.pk != accepted[-1].pk
                or sum(item['basis'] for item in shares) != addon.intent.amount - sum(row.amount for row in refunds)
                or len(part_map) != len(own_parts)
                or part_map != share_map
                or not isinstance(recorded_refunds, list)
                or any(row.pk not in recorded_refunds for row in refunds)):
            raise ValidationError('加购换人确认或逐段收益凭证待核对')
        prior_id, prior_at, prior_income = None, addon.buyer_transaction.created_at, addon.provider_income
        deductions = list(addon.refund_earning_deductions.select_related('refund__payment__intent').order_by('refund__created_at', 'pk'))
        for handover in accepted:
            initial = list(handover.shares.select_related('segment__attribution').order_by('position'))
            decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
            expected = {(addon.intent.buyer_id, 'BOSS', True),
                        (handover.source_segment.provider_account_id, 'PROVIDER', True),
                        (handover.target_segment.provider_account_id, 'PROVIDER', True)}
            if (len(expected) != 3 or len(decisions) != 3 or not handover.accepted_at
                    or handover.previous_id != prior_id or handover.accepted_at <= prior_at
                    or set((account, role, yes) for account, role, yes, _ in decisions) != expected
                    or any(not at for _, _, _, at in decisions)
                    or max(at for _, _, _, at in decisions) != handover.accepted_at
                    or handover.source_segment.attribution.order_id != order.pk
                    or handover.target_segment.attribution.order_id != order.pk
                    or not initial or any(row.segment.attribution.order_id != order.pk for row in initial)
                    or sum(row.basis for row in initial) != addon.intent.amount - sum(
                        row.amount for row in refunds if row.created_at < handover.accepted_at)
                    or sum(row.is_current for row in initial) != 1
                    or any(row.created_at == handover.accepted_at for row in refunds)
                    or any(row.refund.created_at in (prior_at, handover.accepted_at) for row in deductions)):
                raise ValidationError('加购换人确认或历史份额凭证待核对')
            used = sum(row.amount for row in deductions if prior_at < row.refund.created_at < handover.accepted_at)
            if (any(row.refund.payment.intent.order_id != order.pk for row in deductions)
                    or used > prior_income):
                raise ValidationError('加购换人前收益扣减待核对')
            before, after = prior_income - used, sum(row.income for row in initial)
            verify_event_version('ADDON_HANDOVER', handover.pk, handover.accepted_at)
            run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
            if run:
                from orders.addon_funding_release import verify_run
                verify_run(run)
            if start <= handover.accepted_at < end:
                total += after - before
            prior_id, prior_at, prior_income = handover.pk, handover.accepted_at, after
    return total
