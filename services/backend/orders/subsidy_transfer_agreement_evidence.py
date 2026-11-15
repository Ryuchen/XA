"""Isolated draft evidence for later safety-box transfer agreements.

This module does not publish proposals, accept consent or post money. Callers
must not expose it as an executable agreement until termination and settlement
can verify the same evidence end to end.
"""
import hashlib
import json
from datetime import datetime

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import SubsidyTransferAgreementTerms


def enabled():
    from .subsidy_transfer_capability import stage_enabled
    return stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING')


def _tables_ready():
    connection = transaction.get_connection()
    with connection.cursor() as cursor:
        return SubsidyTransferAgreementTerms._meta.db_table in connection.introspection.table_names(cursor)


def _hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _normalized(rows):
    return [{key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()}
            for row in rows]


def _facts(order, case, detail, retained):
    from .models import OrderParticipationSegment, OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot, OrderSegmentTransferBasis
    segments = OrderParticipationSegment.objects.filter(attribution__order=order).order_by('pk').values(
        'id', 'provider_account_id', 'slot', 'original_share', 'accepted_at', 'started_at', 'released_at')
    snapshots = OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order).order_by('segment_id').values(
        'id', 'segment_id', 'incoming_basis', 'retained_basis', 'commission_type', 'commission_rate',
        'commission_fixed', 'fixed_rule_total', 'commission_source', 'income_at_entry', 'game_commission_snapshot')
    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('pk').values(
        'id', 'previous_segment_id', 'next_segment_id', 'original_share_before', 'retained_share',
        'transferred_share', 'operator_id', 'agreement', 'created_at')
    allocations = OrderSegmentTransferBasis.objects.filter(transfer__previous_segment__attribution__order=order).order_by('transfer_id').values(
        'id', 'transfer_id', 'basis_before', 'retained_basis', 'transferred_basis', 'remainder_numerator', 'fixed_fee_store_shortfall')
    return {'version':1, 'order_id':order.pk, 'case_id':case.pk, 'case_request_id':case.client_request_id,
            'customer_account_id':order.customer_account_id, 'detail':detail, 'retained_income':retained,
            'segments':_normalized(segments), 'snapshots':_normalized(snapshots),
            'transfers':_normalized(transfers), 'allocations':_normalized(allocations)}


def _evidence(order, case, scope, retained):
    from .subsidy_transfer_negotiation_preview import preview_locked
    if type(retained) is not int or retained < 0:
        raise ValidationError('当前段保留补贴无效')
    detail = preview_locked(order, case, scope)
    if retained > detail['current_original_income_units']:
        raise ValidationError('当前段核定补贴不能超过该段原预计补贴')
    return _facts(order, case, detail, retained)


def _locked_proposal(proposal_id, *, before_consent=False):
    if not enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('安全箱转单协商凭证尚未开放')
    if not _tables_ready():
        raise ValidationError('安全箱转单协商凭证表尚未就绪')
    from .models import NegotiationProposal
    from .negotiation_proposals import _decisions, _lock_case, _scope, _verified
    case_id = NegotiationProposal.objects.filter(pk=proposal_id).values_list('case_id', flat=True).first()
    if not case_id:
        raise ValidationError('协商方案不存在')
    order, case = _lock_case(case_id)
    from .financial_lock import lock_order_money
    lock_order_money()
    proposal = NegotiationProposal.objects.select_for_update().filter(pk=proposal_id, case=case).first()
    if not proposal or proposal.status not in ('PENDING', 'ACCEPTED') or proposal.refund_units:
        raise ValidationError('安全箱转单草稿状态已变化')
    _verified(case, proposal)
    decisions = _decisions(case, proposal, lock=True)
    if (NegotiationProposal.objects.filter(case=case).order_by('-version').values_list('pk', flat=True).first() != proposal.pk
            or (before_consent and (proposal.status != 'PENDING' or any(row.accepted is not None for row in decisions)))):
        raise ValidationError('安全箱转单方案已更新或已开始确认')
    scope = _scope(order, case)
    if scope != proposal.affected_accounts or len(proposal.provider_terms) != 1:
        raise ValidationError('安全箱转单协商范围已变化')
    term = proposal.provider_terms[0]
    evidence = _evidence(order, case, scope, term['retained_units'])
    detail = evidence['detail']
    if (term['account_id'] != detail['account_id'] or term['original_income_units'] != detail['current_original_income_units']
            or term['burden_units'] != term['original_income_units'] - term['retained_units'] or term['additional_units']):
        raise ValidationError('安全箱当前段方案金额已变化')
    payload = {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash, 'evidence':evidence}
    values = {'segment_id':detail['segment_id'], 'account_id':detail['account_id'],
              'prior_income':detail['prior_income_units'], 'original_income':detail['current_original_income_units'],
              'retained_income':term['retained_units'], 'evidence':evidence, 'evidence_hash':_hash(payload)}
    return proposal, values


@transaction.atomic
def bind_draft(proposal_id):
    """Attach economics once; exact retries return the same evidence."""
    proposal, values = _locked_proposal(proposal_id)
    existing = SubsidyTransferAgreementTerms.objects.select_for_update().filter(proposal=proposal).first()
    if existing:
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise ValidationError('安全箱转单方案凭证已变化')
        return existing
    _locked_proposal(proposal_id, before_consent=True)
    return SubsidyTransferAgreementTerms.objects.create(proposal=proposal, **values)


@transaction.atomic
def verify_draft(proposal_id):
    """Check the saved snapshot against current facts before any consent."""
    proposal, values = _locked_proposal(proposal_id)
    existing = SubsidyTransferAgreementTerms.objects.select_for_update().filter(proposal=proposal).first()
    if not existing or any(getattr(existing, key) != value for key, value in values.items()):
        raise ValidationError('安全箱转单方案凭证缺失或已变化')
    return existing


def verify_committed(execution):
    """Recheck original economics after termination, without inferring consent."""
    if not enabled() or not transaction.get_connection().in_atomic_block or not _tables_ready():
        raise ValidationError('安全箱转单协商凭证尚不可核对')
    proposal, order = execution.proposal, execution.order
    terms = SubsidyTransferAgreementTerms.objects.select_for_update().filter(proposal=proposal).first()
    if not terms or not isinstance(terms.evidence, dict):
        raise ValidationError('安全箱转单协商凭证缺失')
    detail = {'account_id':terms.account_id, 'segment_id':terms.segment_id, 'prior_income_units':terms.prior_income,
              'current_original_income_units':terms.original_income,
              'original_total_income_units':terms.prior_income + terms.original_income}
    expected = _facts(order, proposal.case, detail, terms.retained_income)
    if (terms.evidence != expected or terms.evidence_hash != _hash(
            {'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash, 'evidence':expected})
            or terms.account_id != order.provider_account_id or not 0 <= terms.retained_income <= terms.original_income
            or proposal.affected_accounts != [terms.account_id] or len(proposal.provider_terms) != 1
            or proposal.provider_terms[0] != {'account_id':terms.account_id, 'original_income_units':terms.original_income,
                'retained_units':terms.retained_income, 'burden_units':terms.original_income-terms.retained_income,
                'additional_units':0}):
        raise ValidationError('安全箱转单协商凭证已变化')
    from .segment_settlement_plan import build_segment_settlement_plan
    from .settlement import SettlementError
    try:
        plan = build_segment_settlement_plan(order, allow_recorded=True, _allow_subsidy=True,
                                             _preview_unstarted=order.status == order.Status.IN_SERVICE)
    except SettlementError as exc:
        raise ValidationError('安全箱转单履约和抽成依据待核对') from exc
    current = [line for line in plan.segments if line.segment_id == terms.segment_id]
    prior = sum(line.income for line in plan.segments if line.account_id == terms.account_id
                and line.segment_id != terms.segment_id)
    if len(current) != 1 or current[0].account_id != terms.account_id or current[0].income != terms.original_income or prior != terms.prior_income:
        raise ValidationError('安全箱协商与原逐段收益不一致')
    from .models import OrderParticipationSegment
    released = set(OrderParticipationSegment.objects.filter(attribution__order=order, released_at__isnull=False)
                   .values_list('pk', flat=True))
    entries = execution.shares
    if (not isinstance(entries, list) or any(not isinstance(row, dict) or type(row.get('account_id')) is not int
            or type(row.get('original')) is not int or type(row.get('original_locked')) is not int for row in entries)
            or len({row['account_id'] for row in entries}) != len(entries)):
        raise ValidationError('安全箱转单补贴参与人凭证异常')
    accounts = {row['account_id'] for row in entries}
    if accounts != {line.account_id for line in plan.segments}:
        raise ValidationError('安全箱转单补贴参与人凭证不一致')
    for row in entries:
        lines = [line for line in plan.segments if line.account_id == row['account_id']]
        if (row['original'] != sum(line.income for line in lines)
                or row['original_locked'] != sum(line.income for line in lines if line.segment_id in released)):
            raise ValidationError('安全箱转单历史补贴份额不一致')
    return terms
