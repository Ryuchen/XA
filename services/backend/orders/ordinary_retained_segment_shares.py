"""Resolve a person's retained earnings only when segment amounts are proven."""


def retained(selected, income, previous, share, *, pending=None):
    if not selected or len(selected) != len(set(selected)) or any(item not in income for item in selected):
        raise ValueError('本人终止履约段不存在或重复')
    if len(selected) == 1 and pending is None:
        return {selected[0]: share.retained_income}
    if pending is None:
        if share.pending_deduction:
            raise ValueError('多段正退款扣减尚无逐段凭证')
        pending = {}
    if (not isinstance(pending, dict) or any(segment not in selected or type(amount) is not int or amount <= 0
                                              for segment, amount in pending.items())
            or sum(pending.values()) != share.pending_deduction):
        raise ValueError('逐段扣减与本人实际承担不一致')
    result = {segment: income[segment] - previous.get(segment, 0) - pending.get(segment, 0)
              for segment in selected}
    if (any(amount < 0 for amount in result.values()) or sum(result.values()) != share.original_income
            - share.pending_deduction or sum(result.values()) != share.retained_income):
        raise ValueError('多段保留收益与本人已确认金额不一致')
    return result


def pending_for(facts, account_id, selected):
    plan = facts.get('segment_pending_plan')
    if not isinstance(plan, list):
        raise ValueError('逐段扣减计划缺失')
    result = {}
    for item in plan:
        if (not isinstance(item, dict) or set(item) != {'account_id', 'segment_id', 'amount'}
                or type(item['account_id']) is not int or type(item['segment_id']) is not int
                or type(item['amount']) is not int or item['amount'] <= 0):
            raise ValueError('逐段扣减计划格式异常')
        if item['account_id'] == account_id:
            segment_id = item['segment_id']
            if segment_id not in selected or segment_id in result:
                raise ValueError('本人逐段扣减归属异常')
            result[segment_id] = item['amount']
    return result


def verify_plan(facts, shares):
    segments = facts.get('segments')
    plan = facts.get('segment_pending_plan')
    if not isinstance(segments, dict) or not isinstance(plan, list):
        raise ValueError('协商逐段承担计划缺失')
    accounts = {share.account_id for share in shares}
    if any(not isinstance(item, dict) or item.get('account_id') not in accounts for item in plan):
        raise ValueError('逐段承担计划包含未确认人员')
    for share in shares:
        selected = segments.get(str(share.account_id))
        if not isinstance(selected, list) or not selected:
            raise ValueError('本人协商履约段缺失')
        pending = pending_for(facts, share.account_id, selected)
        if sum(pending.values()) != share.pending_deduction:
            raise ValueError('逐段承担与实际主单扣款不一致')


def verify_execution_plan(execution):
    from .models import OrderParticipationSegment
    plan = execution.proposal.ordinary_segment_pending_plan
    selected = execution.proposal.affected_segment_ids
    if not isinstance(plan, list) or not isinstance(selected, list) or len(selected) != len(set(selected)):
        raise ValueError('已确认逐段承担计划缺失')
    rows = list(OrderParticipationSegment.objects.filter(pk__in=selected,
        attribution__order_id=execution.proposal.case.order_id).values_list('pk', 'provider_account_id'))
    if len(rows) != len(selected):
        raise ValueError('已确认履约段来源缺失')
    segments = {}
    for segment_id, account_id in rows:
        segments.setdefault(str(account_id), []).append(segment_id)
    verify_plan({'segments': segments, 'segment_pending_plan': plan}, list(execution.shares.all()))
