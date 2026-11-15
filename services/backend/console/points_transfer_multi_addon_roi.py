"""Reconcile multi-purchase handover events against finalized segment facts."""
from rest_framework.exceptions import ValidationError


def period_costs(order, manifest, history, addons, start, end, *, main_event_at):
    from orders.addon_funding_release import verify_run
    from orders.addon_handover import current_responsibilities
    from orders.models import OrderAddonFundingReleaseRun
    from .roi_addon_handover import period_delta
    from .roi_period import _verified_addon_costs

    funding = manifest.plan.get('points_funding') if isinstance(manifest.plan, dict) else None
    if not isinstance(funding, dict) or not isinstance(history, list) or len(history) != len(addons):
        raise ValidationError('积分转单加购资金清单待核对')
    parts, totals = funding.get('addons'), funding.get('addon_totals')
    if (not isinstance(parts, list) or not isinstance(totals, dict)
            or len({item['payment_id'] for item in history}) != len(history)
            or {item['payment_id'] for item in history} != {addon.pk for addon in addons}):
        raise ValidationError('积分转单加购付款范围待核对')
    actual_parts, refunds_in_manifest = {}, []
    total_values = {key: 0 for key in ('paid_units', 'principal_units', 'gift_units', 'compensation_units',
                                        'provider_units', 'inviter_units', 'recovery_units')}
    for addon in addons:
        row = next(item for item in history if item['payment_id'] == addon.pk)
        refunds = list(addon.refund_records.select_related('buyer_transaction').order_by('created_at', 'pk'))
        if any(refund.created_at in main_event_at for refund in refunds):
            raise ValidationError('加购与主单退款时间无法区分')
        if row['refund_ids'] != sorted(item.pk for item in refunds):
            raise ValidationError('加购退款历史待核对')
        refunds_in_manifest.extend(item.pk for item in refunds)
        for refund in refunds:
            tx = refund.buyer_transaction
            if tx.reversal_of_id or tx.reversals.exists():
                raise ValidationError('加购退款钱包流水的期间待核对')
        handovers = list(addon.handovers.filter(status='ACCEPTED').order_by('version', 'pk'))
        if any(handover.accepted_at in main_event_at for handover in handovers):
            raise ValidationError('加购接替与主单退款时间无法区分')
        current, responsibilities = current_responsibilities(addon, lock=True)
        if (row['handover_id'] != (current.pk if current else None)
                or (handovers and (not current or current.pk != handovers[-1].pk))
                or sum(item['basis'] for item in responsibilities) != row['paid_units']
                or sum(item['income'] for item in responsibilities) != row['provider_units']):
            raise ValidationError('加购当前履约和资金版本待核对')
        for handover in handovers:
            run = OrderAddonFundingReleaseRun.objects.filter(handover=handover).first()
            if not run:
                raise ValidationError('加购接替资金版本缺失')
            verify_run(run)
        for item in responsibilities:
            key = (addon.pk, item['segment_id'], item['provider_id'])
            # A confirmed handover may return this payment to an earlier
            # segment; the actual settlement posts the sum for that segment.
            actual_parts[key] = actual_parts.get(key, 0) + item['income']
        for key in total_values:
            if type(row.get(key)) is not int or row[key] < 0:
                raise ValidationError('加购来源或收益数据异常')
            total_values[key] += row[key]
    recorded_parts = {(row['payment_id'], row['segment_id'], row['provider_account_id']): row['provider_income_units']
                      for row in parts}
    if (len(recorded_parts) != len(parts) or actual_parts != recorded_parts
            or any(totals.get(key) != value for key, value in total_values.items())
            or funding.get('addon_refund_ids') != refunds_in_manifest):
        raise ValidationError('加购逐段收益或退款清单不一致')
    costs = _verified_addon_costs(addons, start, end)
    if costs is None:
        raise ValidationError('加购付款或退款来源待核对')
    costs['provider'] += period_delta(order, manifest, addons, start, end)
    return costs
