"""Transaction and idempotent closure for a real later points refund."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def execute_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_transfer_refund_access import permits
    from .refunds import authorized_refund
    if (not transaction.get_connection().in_atomic_block or not permits(phase='refund')
            or not operator or not authorized_refund(operator) or not operator.is_active or not operator.can_login
            or type(case_id) is not int or case_id <= 0 or type(proposal_id) is not int or proposal_id <= 0
            or not isinstance(request_id, str) or not request_id or len(request_id) > 64
            or request_id != request_id.strip()):
        raise ValidationError('多轮退款执行上下文无效')
    from .points_third_refund_termination import _execute_locked
    with transaction.atomic():
        return _execute_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator,
                               request_id=request_id, _round=True)


def verify_completed_locked(order, case, posting, *, operator, legacy_operator, request_id):
    from .models import PointsRefundRoundEvidence, OrderParticipationSegment
    from .points_refund_round_evidence import _digest
    from .points_negotiation_preview import verified_asset_terms
    from .negotiation_proposals import _verified, _decisions
    from .refunds import request_fingerprint
    from .points_third_refund_debit_facts import verify as verify_debit
    from .points_round_receipt_usage import verify_locked as verify_release
    proposal, proof = posting.proposal, posting.points_refund
    refund = proof.refund
    version = PointsRefundRoundEvidence.objects.select_for_update().filter(order=order, posting=posting).first()
    if (not version or not version.previous_id or version.evidence_hash != _digest(version.snapshot)
            or posting.operator_id != operator.pk or refund.operator_id != getattr(legacy_operator, 'pk', None)
            or refund.operator_account_id != operator.pk or refund.client_request_id != request_id
            or posting.proposal.case_id != case.pk or case.order_id != order.pk
            or not posting.termination_processed_at or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_round_refund_termination_version') != 1
            or refund.request_fingerprint != request_fingerprint(refund.amount, proposal.explanation,
                {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units})
            or case.status not in ('REFUNDED', 'PARTIALLY_REFUNDED') or not case.resolved_at):
        raise ValidationError('多轮退款原请求或结案凭证不一致')
    _verified(case, proposal)
    _decisions(case, proposal, lock=True)
    terms = verified_asset_terms(proposal)
    if not isinstance(terms.get('round_context'), dict):
        raise ValidationError('多轮退款缺少已确认资金前版')
    shares = list(posting.shares.select_related('assignment', 'wallet_transaction__wallet',
        'deposit_deficit__profile', 'deposit_deficit__source_store_transaction__wallet__user'))
    selected = proposal.affected_segment_ids
    if (not shares or not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
            or len({share.account_id for share in shares}) != len(shares)
            or {share.account_id for share in shares} != set(proposal.affected_accounts)):
        raise ValidationError('多轮退款本人履约范围待核对')
    segments = list(OrderParticipationSegment.objects.filter(pk__in=selected, attribution__order=order))
    if (len(segments) != len(selected) or {segment.provider_account_id for segment in segments} !=
            {share.account_id for share in shares} or (posting.termination_in_service and any(
                segment.released_at != posting.accepted_at for segment in segments))):
        raise ValidationError('多轮退款履约终止事实不一致')
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        for share in shares:
            verify_debit(order, posting, share)
        verify_release(version)
    except DjangoValidationError as exc:
        raise ValidationError('多轮退款实际承担或释放待核对') from exc
    return posting
