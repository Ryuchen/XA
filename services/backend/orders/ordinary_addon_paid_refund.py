"""Preserve actual ordinary service payouts across an add-on refund."""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction

_posting = ContextVar('ordinary_addon_paid_posting', default=None)


@contextmanager
def posting_scope(refund, snapshot):
    if not transaction.get_connection().in_atomic_block or refund.order_id != snapshot.get('order_id'):
        raise ValidationError('已付退款暂态不属于当前事务')
    token = _posting.set((transaction.get_connection().alias, refund.order_id, refund.pk, snapshot['settlement_id']))
    try:
        yield
    finally:
        _posting.reset(token)


def posting_active(run):
    return bool(transaction.get_connection().in_atomic_block and run.refund_id and _posting.get() == (
        transaction.get_connection().alias, run.refund.order_id, run.refund_id,
        getattr(getattr(run.refund.order, 'points_settlement', None), 'pk', None)))


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _snapshot(order, case, settlement):
    people = list(settlement.persons.select_for_update().select_related('segment').order_by('pk'))
    addons = list(settlement.addon_settlements.select_for_update().order_by('pk'))
    fields = ('paid_units', 'principal_units', 'recovery_units', 'gift_units', 'compensation_units',
              'funding_units', 'provider_units', 'inviter_units', 'store_units')
    return {'order_id': order.pk, 'settlement_id': settlement.pk, 'case_id': case.pk,
        'case_opened_at': case.created_at.isoformat(),
        'paid_people': [[p.pk, p.segment_id, p.segment.provider_account_id, p.amount,
                         p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in people],
        'paid_addons': [[p.pk, p.payment_id, p.segment_id, p.amount, p.report_id,
                         p.income_transaction_id, p.settled_at.isoformat()] for p in addons],
        'funding_uses': [list(row) for row in settlement.funding_uses.order_by('allocation_id')
                         .values_list('allocation_id', 'amount')],
        'settlement_facts': [getattr(settlement, field) for field in fields]}


def capture_locked(order, case, accounts):
    from .models import OrderPointsSettlement, OrderSegmentFinancialSnapshot
    from .ordinary_funding_continuation import verified_continuation
    from .service_funding_plan import plan_verified_ordinary_order
    from .addon_settlement import combined_terms, settlement_facts, verify_addon_records
    from .points_funding_evidence import verify_order_funding
    from .points_settlement_test import _verify_person
    from .settlement import SettlementError
    from engagement.confirmation import aftersale_deadline
    settlement = OrderPointsSettlement.objects.select_for_update().filter(order=order, redemption__isnull=True).first()
    if not settlement or not settlement.persons.exists():
        return None
    deadline = aftersale_deadline(order)
    if (not transaction.get_connection().in_atomic_block or order.status != order.Status.COMPLETED
            or order.settled_at or settlement.finalized_at or case.order_id != order.pk or case.status != 'OPEN'
            or not order.completed_at or not case.created_at or not deadline
            or not order.completed_at <= case.created_at < deadline or case.created_at > timezone.now()
            or set(accounts).intersection(settlement.persons.values_list('segment__provider_account_id', flat=True))):
        raise ValidationError('已付陪玩的原订单、售后或承担范围异常')
    with verified_continuation(order, allow_open_case=True, allow_paid_addons=True):
        main = plan_verified_ordinary_order(order.pk)
        addon = combined_terms(order, main)
        funding = verify_order_funding(order.pk)
        if not addon or not funding.fully_funded:
            raise ValidationError('退款前整单加购或补资尚未核实足额')
        facts = settlement_facts(main, addon)
        snapshot = _snapshot(order, case, settlement)
        if list(facts) != snapshot['settlement_facts']:
            raise ValidationError('退款前已付清单与真实资金不一致')
        from .models import OrderPointsFundingAllocation
        from .addon_funding_release import addon_release_totals
        from django.db.models import Q
        allocations = list(OrderPointsFundingAllocation.objects.filter(Q(service_order=order) | Q(redemption__order=order)).order_by('pk'))
        released = addon_release_totals(allocations)
        uses = [[item.pk, item.amount - released[item.pk]] for item in allocations if item.amount > released[item.pk]]
        if uses != snapshot['funding_uses']:
            raise ValidationError('退款前已付清单的到账分配不一致')
        people = list(settlement.persons.select_related('segment', 'report', 'income_transaction__wallet').order_by('pk'))
        snapshots = {item.segment_id: item for item in OrderSegmentFinancialSnapshot.objects.select_for_update()
                     .filter(segment__attribution__order=order).select_related('segment')}
        assignments = {item.provider_account_id: item for item in order.providers.select_for_update()}
        deductions = dict(addon.main_deductions)
        try:
            for person in people:
                snap = snapshots.get(person.segment_id)
                assignment = assignments.get(person.segment.provider_account_id)
                if not snap or not assignment:
                    raise SettlementError('已付陪玩履约身份缺失')
                _verify_person(order, person, snap, assignment, deduction=deductions.get(assignment.provider_account_id, 0))
            actual = verify_addon_records(order, settlement, addon.addon_parts, people)
        except SettlementError as exc:
            raise ValidationError('已付陪玩主单或加购流水待核对') from exc
        expected = {(part.payment_id, part.segment_id) for part in addon.addon_parts
                    if part.segment_id in {person.segment_id for person in people}}
        if actual != expected:
            raise ValidationError('已付陪玩加购收益凭证不完整')
    return snapshot


def record_locked(refund, snapshot):
    from .models import OrdinaryAddonPaidRefundEvidence, OrderAddonFundingReleaseRun
    if not transaction.get_connection().in_atomic_block or refund.order_id != snapshot['order_id']:
        raise ValidationError('已付退款凭证须与退款同事务')
    run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
    if not run:
        raise ValidationError('已付退款缺少真实资金版本')
    return OrdinaryAddonPaidRefundEvidence.objects.create(refund=refund, settlement_id=snapshot['settlement_id'],
        run=run, snapshot=snapshot, evidence_hash=digest(snapshot))


def verify_recorded(evidence):
    """Reconcile this refund with its original paid rows without rewriting them."""
    from .models import OrderPointsSettlementPerson, OrderAddonSettlement
    snapshot, refund, run = evidence.snapshot, evidence.refund, evidence.run
    if (not isinstance(snapshot, dict) or evidence.evidence_hash != digest(snapshot)
            or snapshot.get('settlement_id') != evidence.settlement_id
            or snapshot.get('order_id') != refund.order_id or evidence.settlement.order_id != refund.order_id
            or run.refund_id != refund.pk or not refund.created_at or run.created_at < refund.created_at
            or snapshot.get('case_id') != refund.proposal.case_id or not refund.proposal.case_opened_at
            or snapshot.get('case_opened_at') != refund.proposal.case_opened_at.isoformat()):
        raise ValidationError('已付加购退款凭证或真实资金版本不一致')
    people = list(OrderPointsSettlementPerson.objects.filter(settlement=evidence.settlement,
        settled_at__lt=refund.created_at).select_related('segment').order_by('pk'))
    addons = list(OrderAddonSettlement.objects.filter(order_settlement=evidence.settlement,
        settled_at__lt=refund.created_at).order_by('pk'))
    actual_people = [[p.pk, p.segment_id, p.segment.provider_account_id, p.amount,
                      p.report_id, p.income_transaction_id, p.settled_at.isoformat()] for p in people]
    actual_addons = [[p.pk, p.payment_id, p.segment_id, p.amount, p.report_id,
                      p.income_transaction_id, p.settled_at.isoformat()] for p in addons]
    if (not people or actual_people != snapshot.get('paid_people') or actual_addons != snapshot.get('paid_addons')
            or set(refund.proposal.participants.values_list('account_id', flat=True)).intersection(
                p.segment.provider_account_id for p in people)):
        raise ValidationError('退款改变了已付陪玩或其加购收益')
    from .models import OrderSegmentFinancialSnapshot
    from .points_settlement_test import _verify_person
    from .addon_settlement import verify_addon_records
    from .addon_funding import AddonFundingPart
    from .settlement import SettlementError
    facts = {item.segment_id: item for item in OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution__order=refund.order).select_related('segment')}
    assignments = {item.provider_account_id: item for item in refund.order.providers.all()}
    try:
        for person in people:
            snap = facts.get(person.segment_id)
            assignment = assignments.get(person.segment.provider_account_id)
            if not snap or not assignment or snap.income_at_entry < person.amount:
                raise SettlementError('已付陪玩的原收入不一致')
            _verify_person(refund.order, person, snap, assignment, deduction=snap.income_at_entry - person.amount)
        account_by_segment = {person.segment_id: person.segment.provider_account_id for person in people}
        parts = [AddonFundingPart(row.payment_id, account_by_segment[row.segment_id], row.segment_id, row.amount)
                 for row in addons]
        if verify_addon_records(refund.order, evidence.settlement, parts, people,
                                segment_ids=list(account_by_segment)) != {(row.payment_id, row.segment_id) for row in addons}:
            raise SettlementError('已付加购流水不完整')
    except (SettlementError, KeyError) as exc:
        raise ValidationError('已付陪玩原报单或收入流水待核对') from exc
    from .addon_funding_release import verify_run
    verify_run(run)
    return snapshot
