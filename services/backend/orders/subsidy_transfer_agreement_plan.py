"""Reconcile accepted transfer subsidy terms without posting any money."""
from dataclasses import asdict

from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction


@transaction.atomic
def calculate(proposal_id):
    """Preserve original economics and show the separate negotiated reduction.

    This internal plan cannot itself settle an order or authorize a payout.
    Its order, proposal and funding reads remain locked for its transaction.
    """
    from .subsidy_transfer_capability import stage_enabled
    if not stage_enabled('VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING'):
        raise ValidationError('安全箱转单协商结算计算尚未开放')
    from .subsidy_transfer_agreement_evidence import verify_draft
    terms = verify_draft(proposal_id)
    proposal = terms.proposal
    if proposal.status != 'ACCEPTED' or not proposal.accepted_at:
        raise ValidationError('须先由老板及受影响陪玩分别确认方案')
    order = proposal.case.order
    from .segment_settlement_plan import build_segment_settlement_plan
    from .subsidies import _locked_subsidy_shares, subsidy_funds
    from .settlement import SettlementError
    from wallet.models import Wallet
    try:
        shares = _locked_subsidy_shares(order)
        original = build_segment_settlement_plan(order, _allow_subsidy=True,
                                                 _preview_unstarted=order.status == order.Status.IN_SERVICE)
    except SettlementError as exc:
        raise ValidationError('安全箱原补贴或履约依据待核对') from exc
    if any(share.paid_at or share.assignment.settled_at for share in shares):
        raise ValidationError('已支付补贴须先核对')
    if len({row.assignment.provider_account_id for row in shares}) != len(shares):
        raise ValidationError('安全箱参与人身份待核对')
    by_account = {row.assignment.provider_account_id:row.amount for row in shares}
    originals = {account_id:sum(row.income for row in original.segments if row.account_id == account_id)
                 for account_id in by_account}
    if originals != by_account:
        raise ValidationError('安全箱原补贴与逐段金额不一致')
    current = [row for row in original.segments if row.segment_id == terms.segment_id]
    if (len(current) != 1 or current[0].account_id != terms.account_id
            or current[0].income != terms.original_income
            or originals.get(terms.account_id) != terms.prior_income + terms.original_income):
        raise ValidationError('安全箱当前段收益依据不一致')
    reduction = terms.original_income - terms.retained_income
    if reduction < 0:
        raise ValidationError('当前段核定补贴不能超过原预计补贴')
    people = [{'account_id':account_id, 'original_income_units':amount,
               'retained_income_units':amount - (reduction if account_id == terms.account_id else 0)}
              for account_id, amount in sorted(originals.items())]
    segments = [{**asdict(row), 'retained_income':terms.retained_income if row.segment_id == terms.segment_id else row.income}
                for row in original.segments]
    if (any(row['retained_income_units'] < 0 for row in people)
            or sum(row['retained_income_units'] for row in people) != sum(row['retained_income'] for row in segments)
            or sum(row['original_income_units'] - row['retained_income_units'] for row in people) != reduction):
        raise ValidationError('安全箱协商补贴合计不一致')
    required = sum(row['retained_income_units'] for row in people)
    platform = Wallet.objects.select_for_update().filter(user__username=settings.PLATFORM_SYSTEM_USERNAME).first()
    if not platform:
        raise ValidationError('平台补贴账户尚未就绪')
    available = subsidy_funds(platform)['available']
    return {'proposal_id':proposal.pk, 'segment_id':terms.segment_id, 'account_id':terms.account_id,
            'segments':segments, 'people':people, 'negotiated_reduction_units':reduction,
            'required_subsidy_units':required, 'available_subsidy_units':available,
            'funding_shortfall_units':max(required-available, 0), 'fully_funded':available >= required}
