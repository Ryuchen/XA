"""Read-only settlement evidence for an initial negotiated points refund.

Deposit refunds already hold provider and wallet locks. Do not acquire order
or funding locks here: the settlement writers take those in the other order.
"""
from copy import copy

from rest_framework.exceptions import ValidationError


def _followup_assets_without_locks(proposal, proof):
    """Reconstruct the second refund from its original, already-posted payment."""
    from engagement.funding_wallet import validate_order_funding_sources
    from orders.models import OrderPointsRefund
    from orders.points_negotiation_preview import asset_digest
    from orders.refund_math import payment_refund, wallet_source_refund

    terms = proposal.points_terms
    prior = terms.prior_refund
    order, receipt = proposal.case.order, terms.redemption
    if (not prior or prior.redemption_id != receipt.pk or proof.redemption_id != receipt.pk
            or prior.refund.order_id != order.pk or proof.refund.order_id != order.pk
            or prior.created_at > terms.created_at or prior.refund_id >= proof.refund_id
            or not 0 < prior.equivalent_units < receipt.deal_units
            or OrderPointsRefund.objects.filter(redemption=receipt, refund_id__lt=prior.refund_id).exists()
            or OrderPointsRefund.objects.filter(redemption=receipt, refund_id__lt=proof.refund_id).count() != 1
            or order.refund_records.filter(pk__lt=proof.refund_id).count() != 1
            or not verified_unaffected_points_refund(prior.refund, None)):
        raise ValidationError('积分退款历史待核对')
    sources = list(order.funding_allocations.select_related('lot'))
    validate_order_funding_sources(order, sources)
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    if sum(paid.values()) != receipt.paid_units:
        raise ValidationError('积分退款原支付来源待核对')
    expected = payment_refund(paid_coin_units=receipt.paid_units,
        paid_point_hundredths=receipt.points_hundredths, point_value_units=receipt.discount_units,
        refund_equivalent_units=proposal.refund_units, previously_refunded_equivalent_units=prior.equivalent_units,
        previously_refunded_coin_units=prior.coin_units,
        previously_refunded_point_hundredths=prior.points_hundredths)
    source = wallet_source_refund(coin_units=expected.coin_units, paid_prepaid_units=paid['PREPAID'],
        paid_gift_units=paid['GIFT'], paid_compensation_units=paid['COMPENSATION'],
        refunded_prepaid_units=prior.prepaid_units, refunded_gift_units=prior.gift_units,
        refunded_compensation_units=prior.compensation_units)
    values = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
        'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
        'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units,
        'is_final': terms.is_final, 'prior_refund_id': terms.prior_refund_id}
    if (terms.evidence_hash != asset_digest(proposal, values)
            or (terms.coin_units, terms.points_hundredths, terms.is_final, terms.prepaid_units,
                terms.gift_units, terms.compensation_units) != (expected.coin_units,
                expected.point_hundredths, expected.is_final, source.prepaid_units,
                source.gift_units, source.compensation_units)):
        raise ValidationError('积分退款构成待核对')
    return values


def _verify_followup_proposal_without_locks(episode, proposal):
    from orders.negotiation_proposals import _digest, _episode, _snapshot_terms

    scope = proposal.affected_accounts
    if (not isinstance(scope, list) or not scope or any(type(item) is not int or item <= 0 for item in scope)
            or scope != sorted(set(scope)) or _snapshot_terms(proposal.provider_terms, scope) != proposal.provider_terms
            or type(proposal.refund_units) is not int or proposal.refund_units <= 0 or proposal.refund_units % 10
            or not _episode(episode, proposal) or proposal.terms_hash != _digest(
                episode, proposal.version, scope, proposal.refund_units, proposal.provider_terms,
                proposal.explanation, proposal.affected_segment_ids)):
        raise ValidationError('积分协商方案待核对')


def verified_unaffected_points_refund(refund, account_id):
    """Prove an initial or second points refund concerned another provider."""
    from django.core.exceptions import ObjectDoesNotExist
    from orders.models import OrderOriginalShareTransfer, PointsNegotiationPosting
    from orders.negotiation_proposals import _decisions, _verified
    from orders.points_negotiation_execution import verify_participants
    from orders.points_negotiation_preview import verified_asset_terms
    from orders.refunds import request_fingerprint
    from orders.settlement import SettlementError

    posting = PointsNegotiationPosting.objects.filter(points_refund__refund=refund).select_related(
        'proposal__case__order', 'proposal__points_terms', 'points_refund__redemption',
        'points_refund__coin_transaction__wallet', 'points_refund__points_ledger').first()
    if not posting:
        return False
    try:
        proposal, proof = posting.proposal, posting.points_refund
        case, order = proposal.case, proposal.case.order
        if (not isinstance(posting.participants, list)
                or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                or account_id in proposal.affected_accounts):
            return False
        episode = copy(case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        if proposal.points_terms.prior_refund_id:
            _verify_followup_proposal_without_locks(episode, proposal)
            assets = _followup_assets_without_locks(proposal, proof)
        else:
            _verified(episode, proposal)
            assets = verified_asset_terms(proposal)
        _decisions(episode, proposal)
        verify_participants(posting)
        if (proposal.status != 'ACCEPTED' or posting.terms_hash != proposal.terms_hash
                or posting.accepted_at != proposal.accepted_at or posting.operator_id != refund.operator_account_id
                or refund.order_id != order.pk or refund.reason != proposal.explanation or not refund.client_request_id
                or proof.equivalent_units != proposal.refund_units or refund.amount != assets['coin_units']
                or (proof.redemption_id, proof.coin_units, proof.points_hundredths, proof.prepaid_units,
                    proof.gift_units, proof.compensation_units, proof.is_final) != (
                        assets['redemption_id'], assets['coin_units'], assets['points_hundredths'],
                        assets['prepaid_units'], assets['gift_units'], assets['compensation_units'], assets['is_final'])
                or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                    {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash,
                     'equivalent_units': proposal.refund_units})):
            return False
        coin, ledger = proof.coin_transaction, proof.points_ledger
        if bool(coin) != bool(proof.coin_units) or bool(ledger) != bool(proof.points_hundredths):
            return False
        if coin and (coin.order_id != order.pk or coin.wallet.account_id != order.customer_account_id
                or coin.wallet.user_id != order.customer_id or coin.tx_type != 'REFUND' or coin.status != 'SUCCESS'
                or coin.amount != proof.coin_units or coin.balance_after != coin.balance_before + coin.amount
                or coin.operator_account_id != posting.operator_id or coin.operator_id != refund.operator_id
                or coin.reversal_of_id or coin.reversals.exists()):
            return False
        if ledger and (ledger.order_id != order.pk or ledger.account_id != order.customer_account_id
                or ledger.kind != 'ORDER_POINTS_RETURN' or ledger.amount != proof.points_hundredths
                or ledger.event_key != f'order:{order.pk}:points-return:{refund.pk}'
                or ledger.operator_id != posting.operator_id):
            return False
        shares = list(posting.shares.select_related('assignment').order_by('account_id'))
        terms = proposal.provider_terms
        return (len(shares) == len(terms)
            and all(share.account_id == term['account_id'] and share.assignment.order_id == order.pk
                and share.assignment.provider_account_id == share.account_id
                and (share.original_income, share.burden, share.retained_income) == (
                    term['original_income_units'], term['burden_units'], term['retained_units'])
                and share.burden == share.pending_deduction + share.wallet_deduction + share.deposit_deduction
                for share, term in zip(shares, terms))
            and posting.rounding_to_store_units == sum(share.burden for share in shares) - proposal.refund_units
            and refund.recovered_amount == sum(share.wallet_deduction + share.deposit_deduction for share in shares))
    except (ValidationError, SettlementError, ObjectDoesNotExist,
            TypeError, ValueError, AttributeError, KeyError):
        return False


def _matches_initial_snapshot(order, settlement, snapshot, assignment):
    """Compare the paid assignment and the pre-points provider share."""
    from orders.commission import fixed_specs
    from orders.points_service_payment import MARKER
    from orders.settlement import compute_provider_income, split_settlement_bases
    from orders.team_rules import required_team_size

    if not settlement.redemption_id:
        return (snapshot.incoming_basis == assignment.settlement_base
                and snapshot.income_at_entry == assignment.provider_income
                and snapshot.commission_type == assignment.commission_type
                and snapshot.commission_rate == assignment.commission_rate
                and snapshot.commission_fixed == assignment.commission_fixed
                and snapshot.game_commission_snapshot == assignment.game_commission_snapshot)

    receipt = settlement.redemption
    payment, ledger = receipt.payment, receipt.ledger
    count = required_team_size(order)
    slot = snapshot.segment.slot - 1
    if (receipt.order_id != order.pk or receipt.paid_units != order.amount
            or receipt.deal_units != receipt.paid_units + receipt.discount_units
            or receipt.deal_units != (order.original_amount - order.boss_discount
                                      - order.promo_discount - order.coupon_discount)
            or not isinstance(order.funding_terms, dict)
            or order.funding_terms.get(MARKER) != {'receipt_id': receipt.pk}
            or payment.order_id != order.pk or payment.wallet.account_id != order.customer_account_id
            or payment.wallet.user_id != order.customer_id or payment.tx_type != 'PAY'
            or payment.status != 'SUCCESS' or payment.amount != -receipt.paid_units
            or payment.balance_after != payment.balance_before + payment.amount
            or payment.reversal_of_id or payment.reversals.exists()
            or ledger.order_id != order.pk or ledger.account_id != order.customer_account_id
            or ledger.kind != 'ORDER_POINTS_REDEEM' or ledger.amount != -receipt.points_hundredths
            or ledger.event_key != f'order:{order.pk}:points-redemption'
            or count not in (1, 2) or not 0 <= slot < count
            or snapshot.segment.provider_account_id != assignment.provider_account_id
            or snapshot.commission_type != assignment.commission_type
            or snapshot.commission_rate != assignment.commission_rate
            or snapshot.game_commission_snapshot != assignment.game_commission_snapshot):
        return False
    deal = split_settlement_bases(receipt.deal_units, count)[slot]
    paid = split_settlement_bases(receipt.paid_units, count)[slot]
    if snapshot.commission_type == 'PERCENT':
        if snapshot.commission_fixed or assignment.commission_fixed or snapshot.fixed_rule_total is not None:
            return False
        deal_fee = paid_fee = 0
    elif snapshot.commission_type == 'FIXED':
        if type(snapshot.fixed_rule_total) is not int or snapshot.fixed_rule_total < 0 or snapshot.commission_rate:
            return False
        deal_fee = fixed_specs(receipt.deal_units, count, snapshot.fixed_rule_total)[slot]['commission_fixed']
        paid_fee = fixed_specs(receipt.paid_units, count, snapshot.fixed_rule_total)[slot]['commission_fixed']
    else:
        return False
    return (snapshot.incoming_basis == deal and assignment.settlement_base == paid
            and snapshot.commission_fixed == deal_fee and assignment.commission_fixed == paid_fee
            and snapshot.income_at_entry == compute_provider_income(
                deal, snapshot.commission_type, snapshot.commission_rate, deal_fee)
            and assignment.provider_income == compute_provider_income(
                paid, snapshot.commission_type, snapshot.commission_rate, paid_fee))


def _ordinary_current_segments(order, settlement):
    """Check the active post-reassignment ordinary-service seats without locks."""
    from orders.models import (OrderOriginalAttribution, OrderProvider,
                               OrderSegmentFinancialCoverage, OrderSegmentFinancialSnapshot)

    attribution = OrderOriginalAttribution.objects.filter(order=order, status='ACTIVE').first()
    if not attribution or not OrderSegmentFinancialCoverage.objects.filter(
            attribution=attribution, status='ACTIVE', source='WALLET_NO_POINTS',
            deal_amount=order.amount).exists():
        return None
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution=attribution, segment__released_at__isnull=True).select_related('segment'))
    assignments = {row.provider_account_id: row for row in OrderProvider.objects.filter(
        order=order, released_at__isnull=True)}
    if len(snapshots) != order.required_provider_count or len(assignments) != len(snapshots):
        return None
    for snapshot in snapshots:
        assignment = assignments.get(snapshot.segment.provider_account_id)
        if (not assignment or snapshot.retained_basis is not None
                or not _matches_initial_snapshot(order, settlement, snapshot, assignment)):
            return None
    return {snapshot.segment_id for snapshot in snapshots}


def verified_initial_person(order, assignment):
    """Read the initial, independently paid service share without row locks."""
    from django.core.exceptions import ObjectDoesNotExist
    from orders.models import (OrderOriginalShareTransfer, OrderPointsSettlement,
                               OrderPointsSettlementPerson, OrderPrestartHandoff,
                               OrderSegmentFinancialSnapshot)
    from orders.addon_main_deduction import deductions_at
    from orders.points_settlement_test import _verify_person
    from orders.settlement import SettlementError

    settlement = OrderPointsSettlement.objects.filter(order=order).first()
    if not settlement:
        return False, None
    if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        return True, None
    try:
        from .deposit_clearance import _verified_unaffected_refund
        if any(not _verified_unaffected_refund(refund, assignment.provider_account_id)
               for refund in order.refund_records.all()):
            return True, None
        handoff = OrderPrestartHandoff.objects.filter(order=order).order_by('-pk').first()
        people = list(OrderPointsSettlementPerson.objects.filter(
            settlement=settlement, segment__provider_account_id=assignment.provider_account_id,
            segment__attribution__order=order).select_related(
                'segment__attribution', 'report', 'income_transaction__wallet')[:2])
        if len(people) != 1:
            return True, None
        person = people[0]
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment_id=person.segment_id)
        if handoff:
            if settlement.redemption_id:
                from orders.points_prestart_release import capture
                segments = capture(order, handoff)['segments']
            else:
                segments = _ordinary_current_segments(order, settlement)
            if not segments or person.segment_id not in segments:
                return True, None
        if not _matches_initial_snapshot(order, settlement, snapshot, assignment):
            return True, None
        deduction = deductions_at(order).get(assignment.provider_account_id, 0)
        _verify_person(order, person, snapshot, assignment, deduction=deduction)
        return True, person
    except (ValidationError, SettlementError, ObjectDoesNotExist,
            TypeError, ValueError, AttributeError, KeyError):
        return True, None


def verified_refunded_person(order, assignment):
    """Return (is_refunded_points_order, settled person or None).

    Each supported negotiated refund has an immutable person and payment
    trail. Service transfers and later funding histories remain subject to
    their existing review gate.
    """
    from orders.models import (OrderPointsRefundSettlementPerson, OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot,
                               PointsNegotiationPosting)
    from orders.negotiation_proposals import _decisions, _verified
    from orders.points_negotiation_execution import verify_participants
    from orders.points_negotiation_preview import verified_asset_terms
    from orders.points_refunded_settlement import _check_person
    from orders.refunds import request_fingerprint
    from orders.settlement import SettlementError
    from django.core.exceptions import ObjectDoesNotExist

    postings = list(PointsNegotiationPosting.objects.filter(
        points_refund__redemption__order=order).select_related(
        'proposal__case__order', 'proposal__points_terms',
        'points_refund__refund', 'points_refund__coin_transaction__wallet',
        'points_refund__points_ledger').order_by('pk')[:3])
    if not postings:
        return False, None
    if (len(postings) > 2
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        return True, None
    try:
        refunds = list(order.refund_records.all())
        if refunds and all(verified_unaffected_points_refund(refund, assignment.provider_account_id)
                           for refund in refunds):
            return False, None
        if len(refunds) != len(postings):
            return True, None
        affected = [item for item in postings if assignment.provider_account_id in item.proposal.affected_accounts]
        if len(affected) != 1:
            return True, None
        posting = affected[0]
        if any(item.pk != posting.pk and not verified_unaffected_points_refund(
                item.points_refund.refund, assignment.provider_account_id) for item in postings):
            return True, None
        proposal, proof = posting.proposal, posting.points_refund
        if not isinstance(posting.participants, list):
            return True, None
        case, refund = proposal.case, proof.refund
        episode = copy(case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        if proposal.points_terms.prior_refund_id:
            _verify_followup_proposal_without_locks(episode, proposal)
            assets = _followup_assets_without_locks(proposal, proof)
        else:
            _verified(episode, proposal)
            assets = verified_asset_terms(proposal)
        _decisions(episode, proposal)
        verify_participants(posting)
        if (proposal.status != 'ACCEPTED' or posting.terms_hash != proposal.terms_hash
                or posting.accepted_at != proposal.accepted_at or posting.operator_id != refund.operator_account_id
                or refund.order_id != order.pk or refund.reason != proposal.explanation
                or not refund.client_request_id or refund.amount != assets['coin_units']
                or proof.equivalent_units != proposal.refund_units
                or (proof.redemption_id, proof.coin_units, proof.points_hundredths, proof.prepaid_units,
                    proof.gift_units, proof.compensation_units, proof.is_final) != (
                        assets['redemption_id'], assets['coin_units'], assets['points_hundredths'],
                        assets['prepaid_units'], assets['gift_units'], assets['compensation_units'], assets['is_final'])
                or refund.request_fingerprint != request_fingerprint(refund.amount, refund.reason,
                    {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash,
                     'equivalent_units': proposal.refund_units})):
            return True, None
        coin, points = proof.coin_transaction, proof.points_ledger
        if bool(coin) != bool(proof.coin_units) or bool(points) != bool(proof.points_hundredths):
            return True, None
        if coin and (coin.order_id != order.pk or coin.wallet.account_id != order.customer_account_id
                or coin.wallet.user_id != order.customer_id or coin.tx_type != 'REFUND'
                or coin.status != 'SUCCESS' or coin.amount != proof.coin_units
                or coin.balance_after != coin.balance_before + coin.amount
                or coin.operator_account_id != posting.operator_id or coin.operator_id != refund.operator_id
                or coin.reversal_of_id
                or coin.reversals.exists()):
            return True, None
        if points and (points.order_id != order.pk or points.account_id != order.customer_account_id
                or points.kind != 'ORDER_POINTS_RETURN' or points.amount != proof.points_hundredths
                or points.event_key != f'order:{order.pk}:points-return:{refund.pk}'
                or points.operator_id != posting.operator_id):
            return True, None
        shares = list(posting.shares.select_related('assignment', 'wallet_transaction__wallet',
            'deposit_deficit__source_store_transaction__wallet__user', 'deposit_deficit__profile').order_by('account_id'))
        if len(shares) != len(proposal.provider_terms):
            return True, None
        for share, term in zip(shares, proposal.provider_terms):
            if (share.account_id != term['account_id'] or share.assignment.order_id != order.pk
                    or share.assignment.provider_account_id != share.account_id
                    or (share.original_income, share.burden, share.retained_income) != (
                        term['original_income_units'], term['burden_units'], term['retained_units'])
                    or share.pending_deduction != min(share.burden, share.original_income)
                    or share.wallet_deduction != min(share.burden - share.pending_deduction,
                                                     share.wallet_available_before)
                    or share.deposit_deduction > share.deposit_paid_before
                    or share.burden != share.pending_deduction + share.wallet_deduction + share.deposit_deduction):
                return True, None
            debit = share.wallet_transaction
            if bool(debit) != bool(share.wallet_deduction):
                return True, None
            if debit and (debit.wallet.account_id != share.account_id
                    or debit.wallet.user_id != share.assignment.provider_id or debit.order_id != order.pk
                    or debit.tx_type != 'REFUND' or debit.status != 'SUCCESS'
                    or debit.amount != -share.wallet_deduction or debit.balance_before != share.wallet_available_before
                    or debit.balance_after != debit.balance_before + debit.amount
                    or debit.operator_account_id != posting.operator_id or debit.operator_id != refund.operator_id
                    or debit.reversal_of_id or debit.reversals.exists()):
                return True, None
            deficit = share.deposit_deficit
            if bool(deficit) != bool(share.deposit_deduction):
                return True, None
            if deficit:
                from django.conf import settings
                source = deficit.source_store_transaction
                if (deficit.refund_id != refund.pk or deficit.account_id != share.account_id
                        or deficit.profile.user_id != share.assignment.provider_id
                        or deficit.amount != share.deposit_deduction or deficit.source_kind != 'DIRECT'
                        or not source or source.order_id != order.pk or source.tx_type != 'DEPOSIT_INCOME'
                        or source.status != 'SUCCESS' or source.amount != -deficit.amount
                        or not source.wallet.user or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                        or source.balance_after != source.balance_before + source.amount
                        or source.operator_account_id != posting.operator_id or source.operator_id != refund.operator_id
                        or source.reversal_of_id or source.reversals.exists()):
                    return True, None
        if (posting.rounding_to_store_units != sum(share.burden for share in shares) - proposal.refund_units
                or refund.recovered_amount != sum(share.wallet_deduction + share.deposit_deduction for share in shares)):
            return True, None
        people = list(OrderPointsRefundSettlementPerson.objects.filter(
            settlement__posting=posting, segment__provider_account_id=assignment.provider_account_id,
            segment__attribution__order=order).select_related(
                'segment__attribution', 'report', 'income_transaction__wallet')[:2])
        if len(people) != 1:
            return True, None
        person = people[0]
        snapshot = OrderSegmentFinancialSnapshot.objects.get(segment_id=person.segment_id)
        if person.settled_at < posting.created_at or person.settled_at < refund.created_at:
            return True, None
        _check_person(order, posting, person, snapshot, assignment)
        return True, person
    except (ValidationError, SettlementError, ObjectDoesNotExist,
            TypeError, ValueError, AttributeError, KeyError):
        return True, None
