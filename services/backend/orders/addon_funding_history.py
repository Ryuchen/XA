"""Reconstruct paid add-on cash and obligations at a locked transfer boundary."""
from collections import defaultdict
from dataclasses import asdict

from rest_framework.exceptions import ValidationError

from .addon_payment import verify_receipt
from .models import OrderAddonPayment
from .points_funding_plan import PointsFundingPlanError


def combined_at(order, main, boundary, *, segment_shares=None, exclude_payment_id=None, include_refund_id=None):
    """Use only events committed before the transfer; do not post funds."""
    payments = list(OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__lte=boundary)
                    .exclude(pk=exclude_payment_id) if exclude_payment_id is not None else
                    OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at__lte=boundary))
    payments = sorted(payments, key=lambda item: item.pk)
    if not payments:
        return asdict(main)
    principal = provider = inviter = recovery = 0
    main_deductions = defaultdict(int)
    facts = []
    for payment in payments:
        if payment.buyer_transaction.created_at == boundary:
            raise PointsFundingPlanError('加购与转单时序无法区分')
        try:
            verify_receipt(payment)
        except ValidationError as exc:
            raise PointsFundingPlanError('加购付款或退款凭证待核对') from exc
        rows = {item.pk: item for item in payment.allocations.all()}
        all_refunded = defaultdict(int)
        prior_refunded = defaultdict(int)
        refunds = list(payment.refund_records.select_related('proposal').order_by('pk'))
        prior = []
        payment_main_deductions = defaultdict(int)
        for refund in refunds:
            if refund.created_at == boundary and refund.pk != include_refund_id:
                raise PointsFundingPlanError('加购退款与转单时序无法区分')
            included = refund.created_at < boundary or (refund.created_at == boundary and refund.pk == include_refund_id)
            entries = refund.proposal.source_plan
            if not isinstance(entries, list) or sum(item.get('amount_raw', 0) for item in entries) != refund.amount:
                raise PointsFundingPlanError('加购退款来源快照待核对')
            for item in entries:
                source = rows.get(item.get('allocation_id'))
                amount = item.get('amount_raw')
                if not source or source.kind != item.get('kind') or type(amount) is not int or amount <= 0:
                    raise PointsFundingPlanError('加购退款来源快照待核对')
                all_refunded[source.pk] += amount
                if included:
                    prior_refunded[source.pk] += amount
            if included:
                if refund.main_pending_deduction:
                    participants = list(refund.proposal.participants.filter(main_pending_deduction__gt=0))
                    if sum(person.main_pending_deduction for person in participants) != refund.main_pending_deduction:
                        raise PointsFundingPlanError('加购退款的主单扣减凭证待核对')
                    for person in participants:
                        main_deductions[person.account_id] += person.main_pending_deduction
                        payment_main_deductions[person.account_id] += person.main_pending_deduction
                prior.append(refund)
        if any(all_refunded[row.pk] != row.refunded_amount or all_refunded[row.pk] > row.amount for row in rows.values()):
            raise PointsFundingPlanError('加购退款来源累计不一致')
        sources = defaultdict(int)
        for row in rows.values():
            sources[row.kind] += row.amount - prior_refunded[row.pk]
        handovers = payment.handovers.filter(status='ACCEPTED', accepted_at__lte=boundary).order_by('-version')
        handover = handovers.first()
        if handover and handover.accepted_at == boundary:
            raise PointsFundingPlanError('加购换人与转单时序无法区分')
        if handover:
            shares = list(handover.shares.select_related('segment').order_by('position'))
            expected = {(payment.intent.buyer_id, 'BOSS', True),
                        (handover.source_segment.provider_account_id, 'PROVIDER', True),
                        (handover.target_segment.provider_account_id, 'PROVIDER', True)}
            decisions = set(handover.decisions.values_list('account_id', 'role', 'accepted'))
            before_handover = sum(item.amount for item in prior if item.created_at < handover.accepted_at)
            if (decisions != expected or any(item.created_at == handover.accepted_at for item in prior)
                    or sum(row.basis for row in shares) != payment.intent.amount - before_handover
                    or sum(row.is_current for row in shares) != 1
                    or any(row.segment.attribution.order_id != order.pk for row in shares)):
                raise PointsFundingPlanError('加购换人历史凭证待核对')
            income = sum(row.income for row in shares)
            from django.db.models import Q
            cutoff = Q(refund__created_at__gt=handover.accepted_at, refund__created_at__lt=boundary)
            if include_refund_id is not None:
                cutoff |= Q(refund__created_at__gt=handover.accepted_at,
                            refund_id=include_refund_id, refund__created_at=boundary)
            deductions = payment.refund_earning_deductions.filter(cutoff)
        else:
            income = payment.provider_income
            from django.db.models import Q
            cutoff = Q(refund__created_at__lt=boundary)
            if include_refund_id is not None:
                cutoff |= Q(refund_id=include_refund_id, refund__created_at=boundary)
            deductions = payment.refund_earning_deductions.filter(cutoff)
        deductions = list(deductions.select_related('refund'))
        income -= sum(row.amount for row in deductions)
        net_coin = payment.intent.amount - sum(row.amount for row in prior)
        if income < 0 or income > net_coin or sum(sources.values()) != net_coin:
            raise PointsFundingPlanError('加购历史收益与付款来源不一致')
        addon_inviter = 0  # Referral commission is charged on the main service order only.
        external = sum(item.wallet_deduction + item.deposit_deduction for item in prior)
        principal += sources['PREPAID']
        provider += income
        inviter += addon_inviter
        recovery += external
        facts.append({'payment_id': payment.pk, 'handover_id': handover.pk if handover else None,
                      'refund_ids': [item.pk for item in prior], 'paid_units': net_coin,
                      'principal_units': sources['PREPAID'], 'gift_units': sources['GIFT'],
                      'compensation_units': sources['COMPENSATION'], 'provider_units': income,
                      'main_deductions': [list(item) for item in sorted(payment_main_deductions.items())],
                      'inviter_units': addon_inviter, 'recovery_units': external})
    if main_deductions:
        if segment_shares is None:
            raise PointsFundingPlanError('转单时主单收益份额缺失')
        incomes = defaultdict(int)
        for _, account_id, _, _, income in segment_shares:
            incomes[account_id] += income
        if any(amount > incomes[account_id] for account_id, amount in main_deductions.items()):
            raise PointsFundingPlanError('转单后原陪玩保留收益不足以承担已确认退款')
        # The deduction was committed against a particular service segment;
        # another stint of the same account cannot silently absorb it.
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                                                      created_at__lte=boundary).exists():
            from .addon_main_segment_deduction import verified_deductions
            segment_income = {segment_id: income for segment_id, _, _, _, income in segment_shares}
            used = verified_deductions(order, before=boundary, include_refund_id=include_refund_id)
            if (sum(used.values()) != sum(main_deductions.values())
                    or any(amount > segment_income.get(segment_id, 0) for segment_id, amount in used.items())):
                raise PointsFundingPlanError('转单后已占用的履约段收益不足')
    # Refund funding uses `required_units`; initial funding uses
    # `additional_store_funding_units`. Both represent the same net funding
    # requirement at this boundary. Preserve the actual margin before adding
    # the add-on rather than deriving it from either component alone.
    from .points_negotiation_preview import RefundedFunding
    main_required = (main.required_units if isinstance(main, RefundedFunding)
                     else main.additional_store_funding_units)
    margin = (main.store_income_units - main_required + principal + recovery
              - provider - inviter + sum(main_deductions.values()))
    result = asdict(main)
    reserved = getattr(main, 'rounding_reserved_units', 0)
    funding = max(reserved - margin, 0)
    result.update(additional_store_funding_units=funding, store_income_units=margin + funding,
                  addon_history=facts, main_deductions=[list(item) for item in sorted(main_deductions.items())])
    return result
