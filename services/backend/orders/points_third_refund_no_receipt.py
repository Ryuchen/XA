"""Order-local replay for a third refund with no receipt allocation."""
from dataclasses import asdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def verify_locked(order, posting, transfer, current):
    from .models import OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderAddonPayment
    from .points_transfer_refund_evidence import _facts_without_receipt_locked
    from .points_followup_transfer_revision import verify_locked as verify_transfer
    from .points_negotiation_execution import _verify_posting_core_locked, participant_digest
    from .points_negotiation_preview import funding_after_refund
    from django.db.models import Q

    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('无到账退款必须在本单资金事务中核对')
    if (OrderPointsFundingAllocation.objects.filter(Q(redemption__order=order) | Q(service_order=order)).exists()
            or OrderPointsFundingRelease.objects.filter(posting=posting).exists()
            or not isinstance(current, dict) or set(current) != {'allocations', 'releases', 'funding'}
            or current['allocations'] != [] or current['releases'] != [] or not isinstance(current['funding'], dict)):
        raise ValidationError('无到账退款存在分配或释放记录')
    _facts_without_receipt_locked(order)
    transfer = verify_transfer(transfer)
    posting = _verify_posting_core_locked(posting)
    target = transfer.transfer.next_segment
    shares = list(posting.shares.select_related('assignment').order_by('pk'))
    incomes = transfer.snapshot.get('segment_income')
    if (not isinstance(incomes, list) or any(not isinstance(item, list) or len(item) != 2 for item in incomes)
            or len({item[0] for item in incomes}) != len(incomes)):
        raise ValidationError('无到账退款逐段收益版本异常')
    income = dict(incomes).get(target.pk)
    proposal, proof = posting.proposal, posting.points_refund
    terms = proposal.points_terms
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=posting.created_at).select_related('intent').order_by('pk'))
    if (bool(addons) != (terms.addon_pending_plan is not None)
            or OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at=posting.created_at).exists()):
        raise ValidationError('无到账退款的加购付款与确认方案不一致')
    old = transfer.snapshot['funding']
    addon_income = 0
    if addons:
        from .points_transfer_followup_addon_version import latest_run_locked
        from .addon_handover import current_responsibilities
        latest = latest_run_locked(order, transfer.followup_revision, before=posting.created_at)
        if latest and latest.created_at > transfer.created_at:
            if (latest.created_at >= posting.created_at
                    or latest.points_followup_snapshot.get('post_followup_transfer_id') != transfer.pk
                    or latest.points_followup_snapshot.get('post_followup_transfer_hash') != transfer.evidence_hash):
                raise ValidationError('无到账退款的加购资金版本与转单不一致')
            old = latest.points_followup_snapshot['funding']
        for payment in addons:
            _, responsibilities = current_responsibilities(payment, before=posting.created_at, with_share_id=True)
            addon_income += sum(part['income'] for part in responsibilities
                if part['segment_id'] == target.pk and part['provider_id'] == target.provider_account_id)
    if (transfer.order_id != order.pk or transfer.created_at >= posting.created_at
            or not target.started_at or not proposal.case_opened_at or target.started_at > proposal.case_opened_at
            or (target.released_at is not None and target.released_at != posting.accepted_at)
            or proposal.affected_segment_ids != [target.pk] or proposal.affected_accounts != [target.provider_account_id]
            or len(shares) != 1 or type(income) is not int or shares[0].account_id != target.provider_account_id
            or shares[0].original_income != income + addon_income or shares[0].burden != proposal.refund_units
            or posting.rounding_to_store_units != 0
            or posting.participants_hash != participant_digest(proposal, posting.participants)):
        raise ValidationError('无到账退款实际履约或承担异常')
    parts = list(shares[0].segment_deductions.values_list('segment_id', 'amount'))
    main_pending = min(shares[0].pending_deduction, income)
    if parts != ([(target.pk, main_pending)] if main_pending else []):
        raise ValidationError('无到账退款的逐段承担异常')
    from .points_third_refund_debit_facts import verify as verify_debits
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        verify_debits(order, posting, shares[0])
    except DjangoValidationError as exc:
        raise ValidationError('无到账退款本人承担凭证异常') from exc
    expected = funding_after_refund(principal_units=old['net_principal_units'], gift_units=old['net_gift_units'],
        compensation_units=old['net_compensation_units'], returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=old['provider_income_units'], pending_deduction_units=shares[0].pending_deduction,
        provider_external_units=(old['planned_provider_external_units']
                                 + shares[0].wallet_deduction + shares[0].deposit_deduction),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=0, rounding_reserved_units=old['rounding_reserved_units'] + posting.rounding_to_store_units)
    if asdict(expected) != current['funding'] or expected.release_units:
        raise ValidationError('无到账退款资金不守恒')
    return expected
