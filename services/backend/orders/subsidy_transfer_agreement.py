"""Capability and terms for a current-segment safety-box agreement."""
from django.conf import settings
from rest_framework.exceptions import ValidationError


def enabled():
    from .subsidy_agreement import enabled as subsidy_enabled
    from .subsidy_segment_settlement import enabled as settlement_enabled
    flags = ('NEGOTIATION_PROPOSAL_ENABLED', 'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_AGREEMENT_TESTING',
             'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PREVIEW_TESTING', 'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EVIDENCE_TESTING',
             'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_PLAN_TESTING', 'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_EXECUTION_TESTING',
             'VIP_SAFE_BOX_TRANSFER_NEGOTIATION_SETTLEMENT_TESTING')
    from .subsidy_transfer_capability import stage_enabled
    if (not subsidy_enabled() or not settlement_enabled()
            or not getattr(settings, 'NEGOTIATION_PROPOSAL_ENABLED', False)
            or not all(stage_enabled(flag) for flag in flags[1:])):
        return False
    from .subsidy_execution import table_available
    from .subsidy_transfer_agreement_evidence import _tables_ready
    return table_available() and _tables_ready()


def applies(order):
    from .models import OrderOriginalShareTransfer, OrderPlatformSubsidy
    return (OrderPlatformSubsidy.objects.filter(order=order).exists()
            and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists())


def calculate(order, case, scope, refund_units, requested_terms):
    if not enabled() or refund_units != 0:
        raise ValidationError('安全箱转单协商尚未开放或金额无效')
    from .subsidy_transfer_negotiation_preview import preview_locked
    detail = preview_locked(order, case, scope)
    if (len(requested_terms) != 1 or requested_terms[0]['account_id'] != detail['account_id']
            or type(requested_terms[0]['retained_units']) is not int
            or not 0 <= requested_terms[0]['retained_units'] <= detail['current_original_income_units']):
        raise ValidationError('当前段保留补贴不能超过该段原预计补贴')
    retained = requested_terms[0]['retained_units']
    original = detail['current_original_income_units']
    return {'refund_units':0, 'provider_terms':[{'account_id':detail['account_id'],
            'original_income_units':original, 'retained_units':retained, 'burden_units':original-retained,
            'additional_units':0}], 'rounding_to_store_units':0}


def display_terms(proposal):
    """Do not display a changed or unbound segment as an agreed amount."""
    from stores.db import transaction
    from .models import Order, SubsidyAgreementExecution, SubsidyTransferAgreementTerms
    from .subsidy_transfer_agreement_evidence import _hash, _tables_ready, verify_draft
    if not _tables_ready():
        raise ValidationError('安全箱转单协商凭证表尚未就绪')
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=proposal.case.order_id)
        from .financial_lock import lock_order_money
        lock_order_money()
        terms = SubsidyTransferAgreementTerms.objects.select_for_update().filter(proposal=proposal).first()
        if not terms or not isinstance(terms.evidence, dict):
            raise ValidationError('安全箱转单协商凭证缺失')
        detail = {'account_id':terms.account_id, 'segment_id':terms.segment_id,
                  'prior_income_units':terms.prior_income, 'current_original_income_units':terms.original_income,
                  'original_total_income_units':terms.prior_income+terms.original_income}
        if (terms.evidence.get('detail') != detail or terms.evidence.get('retained_income') != terms.retained_income
                or terms.evidence_hash != _hash({'proposal_id':proposal.pk, 'terms_hash':proposal.terms_hash,
                                                  'evidence':terms.evidence})):
            raise ValidationError('安全箱转单协商凭证已变化')
        if proposal.case.status == 'OPEN' and proposal.status in ('PENDING', 'ACCEPTED'):
            verify_draft(proposal.pk)
        elif proposal.status == 'ACCEPTED':
            from .subsidy_execution import verify_execution
            execution = SubsidyAgreementExecution.objects.select_for_update().select_related(
                'order', 'proposal__case').filter(order=order, proposal=proposal).first()
            if not execution:
                raise ValidationError('安全箱转单协商执行凭证缺失')
            verify_execution(execution)
        return {**detail, 'current_retained_income_units':terms.retained_income}
