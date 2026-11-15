"""Evidence for a later add-on refund after ordinary individual settlement.

The public refund remains gated until release replay and settlement revision
both consume this version in the same transaction.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .ordinary_addon_paid_refund import digest
from rest_framework.exceptions import ValidationError
from stores.db import transaction

_posting = ContextVar('ordinary_refunded_addon_paid_posting', default=None)


@contextmanager
def posting_scope(refund, snapshot):
    if (not transaction.get_connection().in_atomic_block
            or refund.order_id != snapshot.get('order_id')):
        raise ValidationError('退款资金暂态不属于当前订单事务')
    token = _posting.set((transaction.get_connection().alias, refund.order_id,
                          refund.pk, snapshot['settlement_id']))
    try:
        yield
    finally:
        _posting.reset(token)


def posting_active(run):
    state = _posting.get()
    if not state or not transaction.get_connection().in_atomic_block:
        return False
    return (state[:3] == (transaction.get_connection().alias, run.refund.order_id, run.refund_id)
            and run.refund.order.points_settlement.pk == state[3])


def posting_refund_active(refund):
    state = _posting.get()
    return bool(state and transaction.get_connection().in_atomic_block and refund
                and state[:3] == (transaction.get_connection().alias, refund.order_id, refund.pk))


def capture_locked(order, case, accounts):
    """Capture the current, actually paid reservation before any refund write."""
    from engagement.confirmation import aftersale_deadline
    from .ordinary_funding_continuation import verified_refunded_continuation
    from .ordinary_refunded_addon_settlement import verify_recorded_locked
    from .settlement import SettlementError

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('已付清单快照必须在订单事务内生成')
    deadline = aftersale_deadline(order)
    if (order.status != order.Status.COMPLETED or not order.completed_at or not deadline
            or case.order_id != order.pk or case.status != 'OPEN' or not case.created_at
            or not order.completed_at <= case.created_at < deadline):
        raise ValidationError('售后立案时间或订单状态待核对')
    try:
        with verified_refunded_continuation(order, allow_open_case=True, allow_paid_addons=True):
            settlement, people = verify_recorded_locked(order)
    except SettlementError as exc:
        raise ValidationError('原退款及已付结算记录待核对') from exc
    if not people:
        return None
    if any(row.settled_at <= case.created_at or row.segment.provider_account_id in accounts for row in people):
        raise ValidationError('本次退款不能影响已结算陪玩，售后须先于其结算立案')
    evidence = settlement.ordinary_refund_addon_evidence
    addons = list(settlement.addon_settlements.select_related('segment').order_by('pk'))
    return {
        'order_id': order.pk, 'settlement_id': settlement.pk, 'case_id': case.pk,
        'case_opened_at': case.created_at.isoformat(), 'execution_id': evidence.execution_id,
        'settlement_evidence_id': evidence.pk, 'before_facts': evidence.facts,
        'before_hash': evidence.facts_hash,
        'paid_people': [[row.pk, row.segment_id, row.segment.provider_account_id, row.amount,
                         row.report_id, row.income_transaction_id, row.settled_at.isoformat()] for row in people],
        'paid_addons': [[row.pk, row.payment_id, row.segment_id, row.amount, row.report_id,
                         row.income_transaction_id, row.settled_at.isoformat()] for row in addons],
    }


def record_locked(refund, run, snapshot, after_facts):
    """Persist only a real refund and its real funding run in their transaction."""
    from .models import OrdinaryRefundedAddonPaidRevision
    if (not transaction.get_connection().in_atomic_block or refund.order_id != snapshot.get('order_id')
            or run.refund_id != refund.pk or not isinstance(after_facts, dict)):
        raise ValidationError('已付退款版本须绑定本单真实退款和资金运行')
    data = {**snapshot, 'after_facts': after_facts, 'after_hash': digest(after_facts)}
    return OrdinaryRefundedAddonPaidRevision.objects.create(settlement_id=snapshot['settlement_id'],
        refund=refund, run=run, snapshot=data, evidence_hash=digest(data))


def record_posting_locked(order, refund, run, snapshot):
    """Record the actual post-refund position even when funding is short."""
    from .addon_funding_release import addon_release_totals
    from .models import OrderPointsSettlement
    from .ordinary_refunded_addon_funding import plan_locked
    from .ordinary_refunded_addon_settlement import _digest, _economics_locked, _verify_paid_people
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .settlement import SettlementError

    settlement = OrderPointsSettlement.objects.select_for_update().get(pk=snapshot['settlement_id'], order=order)
    evidence = settlement.ordinary_refund_addon_evidence
    if (settlement.finalized_at or order.settled_at or evidence.pk != snapshot['settlement_evidence_id']
            or evidence.execution_id != snapshot['execution_id'] or evidence.facts != snapshot['before_facts']
            or evidence.facts_hash != snapshot['before_hash'] or evidence.facts_hash != _digest(evidence.facts)):
        raise ValidationError('退款前结算清单已变化')
    plan = plan_locked(order.pk, _allow_settled=True, _allow_unconfirmed_addons=True)
    allocations = _verified_allocations_for_locked_order(order)
    released = addon_release_totals(allocations)
    uses = [(row.pk, row.amount - released.get(row.pk, 0)) for row in allocations
            if row.amount > released.get(row.pk, 0)]
    if plan.releasable or sum(amount for _, amount in uses) != plan.allocated:
        raise ValidationError('退款后整单资金释放待核对')
    execution, economics = _economics_locked(order, plan, uses)
    if execution.pk != evidence.execution_id:
        raise ValidationError('退款后协商执行来源已变化')
    try:
        people = _verify_paid_people(order, settlement, execution, economics)
    except SettlementError as exc:
        raise ValidationError('退款改变已付人员收益或加购流水') from exc
    if [[p.pk, p.segment_id, p.segment.provider_account_id, p.amount, p.report_id,
         p.income_transaction_id, p.settled_at.isoformat()] for p in people] != snapshot['paid_people']:
        raise ValidationError('退款前已付人员记录已变化')
    revision = record_locked(refund, run, snapshot, economics)
    from .addon_funding_release import verify_run
    verify_run(run, _skip_usage=True)
    from .ordinary_refunded_addon_paid_receipt import verify
    verify(run)
    return revision


def pending_locked(order):
    """Verify an unamended paid reservation while later funding is pending."""
    from .models import OrderPointsSettlement, OrdinaryRefundedAddonPaidRevision
    from .ordinary_refunded_addon_paid_receipt import verify
    from .ordinary_refunded_addon_settlement import _digest, _verify_paid_people
    from .settlement import SettlementError
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order).first()
    if not settlement:
        return None
    evidence = getattr(settlement, 'ordinary_refund_addon_evidence', None)
    if not evidence:
        return None
    candidates = [row for row in OrdinaryRefundedAddonPaidRevision.objects.filter(settlement=settlement)
                  .select_related('run', 'refund').order_by('pk') if isinstance(row.snapshot, dict)
                  and row.snapshot.get('before_hash') == evidence.facts_hash
                  and row.snapshot.get('before_facts') == evidence.facts]
    if not candidates:
        return None
    if len(candidates) != 1 or evidence.facts_hash != _digest(evidence.facts):
        raise ValidationError('待补资的已付退款版本不唯一或原清单异常')
    revision = candidates[0]
    from django.conf import settings
    from .ordinary_after_main_addon_receipt import verified_release
    if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True:
        verified_release(revision.run, None, order_locked=True)
    elif not revision.run.allocation_snapshot:
        verified_release(revision.run, None)
    verify(revision.run)
    try:
        people = _verify_paid_people(order, settlement, evidence.execution, evidence.facts)
    except SettlementError as exc:
        raise ValidationError('待补资的已付流水待核对') from exc
    if [[p.pk, p.segment_id, p.segment.provider_account_id, p.amount, p.report_id,
         p.income_transaction_id, p.settled_at.isoformat()] for p in people] != revision.snapshot['paid_people']:
        raise ValidationError('待补资的已付人员已变化')
    return revision


def advance_locked(order, revision):
    """Revise the unpaid reservation only after real funding becomes sufficient."""
    from .addon_funding_release import addon_release_totals
    from .models import OrderPointsSettlement, OrderPointsSettlementFunding
    from .ordinary_refunded_addon_funding import plan_locked
    from .ordinary_refunded_addon_settlement import _digest, _economics_locked, _verify_paid_people
    from .ordinary_refunded_addon_paid_receipt import verify
    from .points_funding_evidence import _verified_allocations_for_locked_order
    from .settlement import SettlementError

    snapshot = revision.snapshot
    settlement = OrderPointsSettlement.objects.select_for_update().get(pk=revision.settlement_id, order=order)
    evidence = settlement.ordinary_refund_addon_evidence
    if (revision.refund.order_id != order.pk or settlement.finalized_at or order.settled_at
            or evidence.pk != snapshot['settlement_evidence_id'] or evidence.execution_id != snapshot['execution_id']
            or evidence.facts != snapshot['before_facts'] or evidence.facts_hash != snapshot['before_hash']
            or evidence.facts_hash != _digest(evidence.facts)):
        raise ValidationError('原结算清单与退款版本不一致')
    verify(revision.run)
    plan = plan_locked(order.pk, _allow_settled=True, _allow_unconfirmed_addons=True)
    if plan.remaining:
        return False
    allocations = _verified_allocations_for_locked_order(order)
    released = addon_release_totals(allocations)
    uses = [(row.pk, row.amount - released.get(row.pk, 0)) for row in allocations
            if row.amount > released.get(row.pk, 0)]
    if plan.releasable or sum(amount for _, amount in uses) != plan.allocated:
        raise ValidationError('退款后整单资金释放待核对')
    from django.conf import settings
    from .ordinary_after_main_addon_receipt import verified_release
    if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is not True:
        verified_release(revision.run, None, order_locked=True)
    elif not revision.run.allocation_snapshot:
        verified_release(revision.run, None)
    else:
        witness_ids = sorted({row.receipt_id for row in allocations
                              if row.pk in {item[0] for item in revision.run.allocation_snapshot}})
        if not witness_ids:
            raise ValidationError('退款时的到账来源缺失')
        for receipt_id in witness_ids:
            verified_release(revision.run, receipt_id)
    execution, economics = _economics_locked(order, plan, uses)
    original_after = snapshot['after_facts']
    if any(economics.get(key) != original_after.get(key) for key in
           ('execution_id', 'order_id', 'people', 'addons', 'addon_history',
            'completed_at', 'clock_version', 'aftersale_hours', 'freeze_hours', 'deposit_percent')):
        raise ValidationError('补资后实际收益与退款版本不一致')
    dynamic = {'allocated_units', 'release_units', 'remaining_units'}
    if ({key: value for key, value in economics['combined'].items() if key not in dynamic}
            != {key: value for key, value in original_after['combined'].items() if key not in dynamic}):
        raise ValidationError('补资后资金构成与退款版本不一致')
    try:
        people = _verify_paid_people(order, settlement, execution, economics)
    except SettlementError as exc:
        raise ValidationError('退款改变已付人员收益或加购流水') from exc
    if [[p.pk, p.segment_id, p.segment.provider_account_id, p.amount, p.report_id,
         p.income_transaction_id, p.settled_at.isoformat()] for p in people] != snapshot['paid_people']:
        raise ValidationError('已付人员记录已变化')
    combined = plan.combined
    (settlement.paid_units, settlement.principal_units, settlement.recovery_units,
     settlement.gift_units, settlement.compensation_units, settlement.funding_units,
     settlement.provider_units, settlement.inviter_units, settlement.store_units) = (
        combined.net_coin_units, combined.net_principal_units, combined.planned_provider_external_units,
        combined.net_gift_units, combined.net_compensation_units, plan.required,
        combined.provider_income_units, combined.inviter_income_units, combined.store_income_units)
    settlement.save(update_fields=['paid_units', 'principal_units', 'recovery_units', 'gift_units',
        'compensation_units', 'funding_units', 'provider_units', 'inviter_units', 'store_units'])
    settlement.funding_uses.all().delete()
    OrderPointsSettlementFunding.objects.bulk_create([
        OrderPointsSettlementFunding(settlement=settlement, allocation_id=pk, amount=amount) for pk, amount in uses])
    evidence.facts, evidence.facts_hash = economics, _digest(economics)
    evidence.save(update_fields=['facts', 'facts_hash', 'updated_at'])
    return True
