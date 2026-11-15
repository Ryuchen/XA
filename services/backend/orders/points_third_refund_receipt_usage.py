"""Receipt-local source replay for the bounded third points refund.

No order, allocation, or other receipt locks are acquired here. The receipt
writer owns its receipt lock and the READ COMMITTED current-read requirement.
This source does not authorize a refund and is not connected to the API yet.
"""
from dataclasses import asdict
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum


def facts(row, *, read_receipt_id=None):
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads
    from .models import (PointsRefundRoundEvidence, OrderPointsFundingAllocation, OrderPointsFundingRelease,
        OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
        OrderAddonFundingRelease, OrderAddonPayment)
    from .points_refund_round_evidence import _digest as version_digest
    from .points_followup_transfer_receipt_usage import facts as transfer_facts
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .points_negotiation_preview import funding_after_refund, asset_digest
    from .points_negotiation_execution import participant_digest
    from .negotiation_proposals import _digest as proposal_digest
    from .refunds import request_fingerprint
    from .refund_math import payment_refund, wallet_source_refund, RefundCalculationError
    from engagement.funding_wallet import validate_order_funding_sources

    allocation, posting = row.allocation, row.posting
    receipt_id = read_receipt_id or allocation.receipt_id
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    version = PointsRefundRoundEvidence.objects.select_related(
        'transfer_revision__order', 'transfer_revision__transfer__next_segment',
        'transfer_revision__first_posting__points_refund',
        'transfer_revision__second_posting__points_refund').filter(posting=posting).first()
    if not version or not isinstance(version.snapshot, dict) or version.evidence_hash != version_digest(version.snapshot):
        raise ValidationError('第三轮退款资金版本缺失')
    transfer, order = version.transfer_revision, version.order
    rounds = version.snapshot.get('rounds')
    if (allocation.funding_order_id != order.pk or posting.proposal.case.order_id != order.pk
            or transfer.order_id != order.pk or transfer.created_at >= posting.created_at
            or version.snapshot.get('transfer_revision_id') != transfer.pk
            or not isinstance(rounds, list) or len(rounds) != 3 or rounds[-1].get('posting_id') != posting.pk
            or [item.get('posting_id') for item in rounds[:2]] != [transfer.first_posting_id, transfer.second_posting_id]):
        raise ValidationError('第三轮退款与本单转单历史不一致')
    transfer_row = transfer.releases.select_related('allocation').order_by('pk').first()
    if transfer_row is None:
        # Later receipts witness a current read; they are not transfer history.
        transfer_allocations = transfer.snapshot.get('allocations', [])
        historical = (OrderPointsFundingAllocation.objects.filter(pk=transfer_allocations[0][0]).first()
                      if transfer_allocations else None)
        transfer_row = SimpleNamespace(revision=transfer, allocation=historical or allocation, pk=0, amount=0)
    transfer_row.read_receipt_id = receipt_id
    previous_source = transfer_facts(transfer_row)
    addon_terms = posting.proposal.points_terms.addon_pending_plan
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=posting.created_at).select_related('intent').order_by('pk'))
    if OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=posting.created_at).exists():
        raise ValidationError('加购付款与第三轮退款时间无法区分')
    if bool(addons) != (addon_terms is not None):
        raise ValidationError('第三轮加购付款与确认方案不一致')
    old_funding = transfer.snapshot['funding']
    if addons:
        from .points_transfer_followup_addon_version import latest_run_locked
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            latest = latest_run_locked(order, transfer.followup_revision, before=posting.created_at,
                                       receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('第三轮加购历史资金版本待核对') from exc
        if latest and latest.created_at > transfer.created_at:
            if (latest.created_at >= posting.created_at
                    or latest.points_followup_snapshot.get('post_followup_transfer_id') != transfer.pk
                    or latest.points_followup_snapshot.get('post_followup_transfer_hash') != transfer.evidence_hash):
                raise ValidationError('第三轮加购资金版本与实际转单不一致')
            old_funding = latest.points_followup_snapshot['funding']
    source = rounds[-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise ValidationError('第三轮释放计划缺失')
    current = source[1]
    if set(current) != {'allocations', 'releases', 'funding'}:
        raise ValidationError('第三轮释放计划异常')
    allocations = current['allocations']
    if not isinstance(allocations, list):
        raise ValidationError('第三轮分配快照异常')
    owner = Q(redemption__order=order) | Q(service_order=order)
    persisted = list(OrderPointsFundingAllocation.objects.filter(owner, created_at__lte=posting.created_at).order_by('pk'))
    actual_before = []
    for item in persisted:
        prior = sum(value or 0 for value in (
            OrderPointsFundingRelease.objects.filter(allocation=item, created_at__lt=posting.created_at).aggregate(v=Sum('amount'))['v'],
            OrderPointsTransferFundingRelease.objects.filter(allocation=item, created_at__lt=posting.created_at).aggregate(v=Sum('amount'))['v'],
            PointsPostRefundTransferRelease.objects.filter(allocation=item, revision__created_at__lt=posting.created_at).aggregate(v=Sum('amount'))['v'],
            PointsFollowupTransferRelease.objects.filter(allocation=item, revision__created_at__lt=posting.created_at).aggregate(v=Sum('amount'))['v'],
            OrderAddonFundingRelease.objects.filter(allocation=item, run__created_at__lt=posting.created_at).aggregate(v=Sum('amount'))['v']))
        actual_before.append([item.pk, item.amount, prior])
    later_witness = (row.pk == 0 and row.amount == 0 and allocation.created_at > posting.created_at)
    if allocations != actual_before or (not any(item.pk == allocation.pk for item in persisted) and not later_witness):
        raise ValidationError('第三轮到账分配或历史释放不一致')
    proof, proposal = posting.points_refund, posting.proposal
    prior_proofs = [transfer.first_posting.points_refund, transfer.second_posting.points_refund]
    paid = list(order.funding_allocations.select_related('lot').order_by('pk'))
    from rest_framework.exceptions import ValidationError as RestValidationError
    try:
        validate_order_funding_sources(order, paid)
    except RestValidationError as exc:
        raise ValidationError('第三轮原付款来源异常') from exc
    totals = {kind: sum(item.amount for item in paid if item.kind == kind)
              for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    try:
        expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
            paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
            refund_equivalent_units=proposal.refund_units,
            previously_refunded_equivalent_units=sum(item.equivalent_units for item in prior_proofs),
            previously_refunded_coin_units=sum(item.coin_units for item in prior_proofs),
            previously_refunded_point_hundredths=sum(item.points_hundredths for item in prior_proofs))
        expected_sources = wallet_source_refund(coin_units=expected.coin_units,
            paid_prepaid_units=totals['PREPAID'], paid_gift_units=totals['GIFT'],
            paid_compensation_units=totals['COMPENSATION'],
            refunded_prepaid_units=sum(item.prepaid_units for item in prior_proofs),
            refunded_gift_units=sum(item.gift_units for item in prior_proofs),
            refunded_compensation_units=sum(item.compensation_units for item in prior_proofs))
    except RefundCalculationError as exc:
        raise ValidationError('第三轮原路退款金额异常') from exc
    if (proof.redemption.order_id != order.pk or proof.refund.order_id != order.pk
            or proof.refund.amount != proof.coin_units or proof.equivalent_units != proposal.refund_units
            or (proof.coin_units, proof.points_hundredths, proof.is_final, proof.prepaid_units, proof.gift_units,
                proof.compensation_units) != (expected.coin_units, expected.point_hundredths, expected.is_final,
                expected_sources.prepaid_units, expected_sources.gift_units, expected_sources.compensation_units)):
        raise ValidationError('第三轮原路退款不一致')
    coin, points, payment = proof.coin_transaction, proof.points_ledger, proof.redemption.payment
    if (bool(coin) != bool(proof.coin_units) or bool(points) != bool(proof.points_hundredths)
            or (coin and (coin.order_id != order.pk or coin.wallet_id != payment.wallet_id or coin.tx_type != 'REFUND'
                or coin.status != 'SUCCESS' or coin.amount != proof.coin_units or coin.reversal_of_id or coin.reversals.exists()
                or coin.operator_id != proof.refund.operator_id or coin.operator_account_id != posting.operator_id
                or coin.balance_after != coin.balance_before + coin.amount))
            or (points and (points.order_id != order.pk or points.account_id != order.customer_account_id
                or points.kind != 'ORDER_POINTS_RETURN' or points.amount != proof.points_hundredths
                or points.operator_id != posting.operator_id
                or points.event_key != f'order:{order.pk}:points-return:{proof.refund_id}'))):
        raise ValidationError('第三轮原路退款流水异常')
    shares = list(posting.shares.select_related('assignment').order_by('pk'))
    target = transfer.transfer.next_segment
    incomes = transfer.snapshot.get('segment_income')
    if (not isinstance(incomes, list) or any(not isinstance(item, list) or len(item) != 2 for item in incomes)
            or len({item[0] for item in incomes}) != len(incomes)):
        raise ValidationError('第三轮转入段收益版本异常')
    income = dict(incomes).get(target.pk)
    addon_income = 0
    if addons:
        from .addon_handover import current_responsibilities
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            for payment in addons:
                _, responsibilities = current_responsibilities(payment, before=posting.created_at, with_share_id=True)
                addon_income += sum(part['income'] for part in responsibilities
                    if part['segment_id'] == target.pk and part['provider_id'] == target.provider_account_id)
        except RestValidationError as exc:
            raise ValidationError('第三轮加购历史份额待核对') from exc
    total_income = income + addon_income if type(income) is int else None
    if (len(shares) != 1 or shares[0].account_id != target.provider_account_id
            or type(income) is not int or income < 0 or shares[0].original_income != total_income
            or shares[0].burden != proposal.refund_units
            or shares[0].retained_income != total_income - shares[0].pending_deduction
            or posting.rounding_to_store_units != 0 or not target.started_at or not proposal.case_opened_at
            or target.started_at > proposal.case_opened_at
            or (target.released_at is not None and target.released_at != posting.accepted_at)
            or shares[0].assignment.order_id != order.pk or shares[0].assignment.provider_account_id != shares[0].account_id
            or proposal.affected_segment_ids != [target.pk] or proposal.affected_accounts != [target.provider_account_id]
            or list(shares[0].segment_deductions.values_list('segment_id', 'amount')) != (
                [(target.pk, min(shares[0].pending_deduction, income))] if min(shares[0].pending_deduction, income) else [])):
        raise ValidationError('第三轮本人逐段承担异常')
    from .points_third_refund_debit_facts import verify as verify_debits
    verify_debits(order, posting, shares[0])
    decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected_decisions = {(order.customer_account_id, 'BOSS', True), (target.provider_account_id, 'PROVIDER', True)}
    from copy import copy
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    terms = proposal.points_terms
    assets = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
        'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
        'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units, 'is_final': terms.is_final,
        'prior_refund_id': terms.prior_refund_id, 'segment_pending_plan': terms.segment_pending_plan}
    if terms.addon_pending_plan is not None:
        assets['addon_pending_plan'] = terms.addon_pending_plan
    if (len(decisions) != len(expected_decisions) or {(a, b, c) for a, b, c, _ in decisions} != expected_decisions
            or any(not at for _, _, _, at in decisions) or proposal.status != 'ACCEPTED'
            or proposal.accepted_at != max(at for _, _, _, at in decisions) or posting.accepted_at != proposal.accepted_at
            or proposal.terms_hash != proposal_digest(episode, proposal.version, proposal.affected_accounts,
                proposal.refund_units, proposal.provider_terms, proposal.explanation, proposal.affected_segment_ids)
            or posting.terms_hash != proposal.terms_hash or posting.participants_hash != participant_digest(proposal, posting.participants)
            or terms.evidence_hash != asset_digest(proposal, assets) or terms.redemption_id != proof.redemption_id
            or terms.prior_refund_id != prior_proofs[-1].pk
            or (terms.coin_units, terms.points_hundredths, terms.prepaid_units, terms.gift_units, terms.compensation_units, terms.is_final)
                != (proof.coin_units, proof.points_hundredths, proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final)
            or proof.refund.operator_account_id != posting.operator_id
            or proof.refund.recovered_amount != shares[0].wallet_deduction + shares[0].deposit_deduction
            or terms.segment_pending_plan != ([{'account_id': target.provider_account_id, 'segment_id': target.pk,
                                               'amount': min(shares[0].pending_deduction, income)}]
                                              if min(shares[0].pending_deduction, income) else [])
            or proof.refund.reason != proposal.explanation or proof.refund.request_fingerprint != request_fingerprint(
                proof.coin_units, proposal.explanation, {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash,
                                                         'equivalent_units': proposal.refund_units})):
        raise ValidationError('第三轮方案或双方确认异常')
    old = old_funding
    calculated = funding_after_refund(principal_units=old['net_principal_units'], gift_units=old['net_gift_units'],
        compensation_units=old['net_compensation_units'], returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=old['provider_income_units'], pending_deduction_units=shares[0].pending_deduction,
        provider_external_units=(old['planned_provider_external_units']
                                 + shares[0].wallet_deduction + shares[0].deposit_deduction),
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        allocated_units=sum(amount - released for _, amount, released in allocations),
        rounding_reserved_units=old['rounding_reserved_units'] + posting.rounding_to_store_units)
    try:
        parts = plan_release(allocations=allocations, amount=calculated.release_units)
    except PointsFundingAllocationError as exc:
        raise ValidationError('第三轮释放金额异常') from exc
    expected_parts = sorted([[part.allocation_id, part.amount] for part in parts])
    actual = sorted([list(item) for item in posting.funding_releases.values_list('allocation_id', 'amount')])
    witness = row.pk == 0 and row.amount == 0
    if (asdict(calculated) != current['funding'] or current['releases'] != expected_parts or actual != expected_parts
            or (witness and expected_parts)
            or (not witness and [allocation.pk, row.amount] not in expected_parts)):
        raise ValidationError('第三轮实际释放或资金不守恒')
    return ('RELEASE', f'order:{allocation.pk}', f'points-third-refund:{row.pk}', row.amount,
            _digest(['points-third-refund', row.pk, allocation.pk, allocation.receipt_id, row.amount,
                     version.pk, version.evidence_hash, previous_source]))
