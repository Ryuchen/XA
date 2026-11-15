"""Pure segment-income calculation for already verified transfer facts.

This function does not authorize, query, or lock an order. Callers must first
validate the tenant, source snapshot, refund proofs and event-time boundary.
"""
from rest_framework.exceptions import ValidationError


def segment_lines(facts, *, through_transfer_id):
    if not isinstance(facts, dict) or type(through_transfer_id) is not int or through_transfer_id < 0:
        raise ValidationError('转单履约事实无效')
    events, rows = facts['transfers'], facts['segments']
    ids = {event['id'] for event in events}
    if not events or (through_transfer_id and through_transfer_id not in ids):
        raise ValidationError('转单资金版本不存在')
    if through_transfer_id:
        boundary = next(event for event in events if event['id'] == through_transfer_id)
        if any(event['id'] > through_transfer_id and event['created_at'] == boundary['created_at'] for event in events):
            raise ValidationError('请选择整次转单完成后的资金版本')
    incoming = {event['next_segment_id']: event for event in events}
    outgoing = {event['previous_segment_id']: event for event in events}
    if len(incoming) != len(events) or len(outgoing) != len(events):
        raise ValidationError('转单履约关联重复')
    lines = []
    for row in rows:
        arrived, left = incoming.get(row['id']), outgoing.get(row['id'])
        if arrived and arrived['id'] > through_transfer_id:
            continue
        ended = left is not None and left['id'] <= through_transfer_id
        basis = left['retained_basis'] if ended else 0 if row['pre_service_released'] else row['incoming_basis']
        fee = (((row['fixed_rule_total'] * basis // facts['deal_amount']) if ended else
                0 if row['pre_service_released'] else row['commission_fixed'])
               if row['commission_type'] == 'FIXED' else basis * row['commission_rate'] // 100)
        if fee > basis:
            raise ValidationError('转单历史抽成超过成交份额')
        lines.append((row['id'], row['account_id'], basis, fee, basis - fee))
    return lines
