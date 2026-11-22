"""Receipt-local source checks for the isolated usage ledger.

Reservation cancellation, the first points main refund, the narrowly scoped
unpaid followup refund and first points add-on refund use isolated switches.
Other release writers remain blocked.
"""
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from stores.db import transaction
from .models import PassReceiptUsageState, PassRefundRepaymentAllocation
from .receipt_usage_ledger import _locked_receipt, _validate_locked, append_locked


def _digest(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def pass_facts(row):
    return ('PASS', f'pass:{row.pk}', f'pass:{row.request_key}', row.amount,
            _digest(['pass', row.pk, row.receipt_id, row.refund_id, row.amount, row.request_key, row.allocated_by]))


def order_facts(row):
    return ('ORDER', f'order:{row.pk}', f'order:{row.request_key}', row.amount,
            _digest(['order', row.pk, row.receipt_id, row.redemption_id, row.service_order_id,
                     row.amount, row.request_key, row.allocated_by]))


def referral_facts(row):
    """Verify one immutable tenant reservation; refund binding may occur later."""
    from stores.context import current_store
    from orders.models import ReferralRefundFundingAction
    store = current_store()
    action = ReferralRefundFundingAction.objects.filter(allocation=row).first()
    expected = {'receipt_id': row.receipt_id, 'allocation_id': row.pk,
                'order_id': row.order_id, 'amount': row.amount, 'refund_amount': row.target_refund_amount}
    if (not store or not row.order_id or not row.receipt_id or type(row.amount) is not int or row.amount <= 0
            or type(row.target_refund_amount) is not int or row.target_refund_amount <= 0
            or not row.request_key or row.request_key != row.request_key.strip()
            or not row.allocated_by or row.allocated_by != row.allocated_by.strip()
            or not action or action.request_key != row.request_key or action.actor != row.allocated_by
            or action.result != expected or (row.refund_id and (row.refund.order_id != row.order_id
                or row.refund.amount != row.target_refund_amount))):
        raise ValidationError('推荐佣金退款的到账分配凭证异常')
    return ('REFERRAL', f'referral:{row.pk}', f'referral:{row.request_key}', row.amount,
            _digest([store.slug, row.pk, row.order_id, row.receipt_id, row.amount, row.target_refund_amount,
                     row.request_key, row.allocated_by]))


def record_referral_locked(row):
    """Append the actual tenant reservation and action in their transaction."""
    from orders.models import ReferralRefundFunding
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('推荐佣金退款分配不属于当前门店')
    receipt = _locked_receipt(row.receipt_id)
    _require_current_unlocked_reads()
    actual = ReferralRefundFunding.objects.select_related('refund').get(pk=row.pk, receipt=receipt)
    kind, key, request, amount, digest = referral_facts(actual)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def referral_release_facts(row):
    from stores.context import current_store
    from orders.models import ReferralRefundFundingRelease
    store, allocation = current_store(), row.allocation
    expected = {'allocation_id': allocation.pk, 'receipt_id': allocation.receipt_id,
                'order_id': allocation.order_id, 'amount': allocation.amount}
    if (not store or allocation.refund_id or not row.request_key or row.request_key != row.request_key.strip()
            or not row.actor or row.actor != row.actor.strip() or not row.reason or row.reason != row.reason.strip()
            or row.result != expected or ReferralRefundFundingRelease.objects.filter(allocation=allocation).count() != 1):
        raise ValidationError('推荐佣金退款到账释放凭证异常')
    referral_facts(allocation)
    return ('REFERRAL_RELEASE', f'referral:{allocation.pk}', f'referral-release:{row.request_key}', allocation.amount,
            _digest([store.slug, row.pk, allocation.pk, allocation.receipt_id, allocation.order_id,
                     allocation.amount, row.request_key, row.actor, row.reason]))


def record_referral_release_locked(row):
    """Release an unused reservation once in the same tenant transaction."""
    from orders.models import ReferralRefundFundingRelease
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('推荐佣金退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    actual = ReferralRefundFundingRelease.objects.select_related('allocation__refund').get(
        pk=row.pk, allocation__receipt=receipt)
    kind, key, request, amount, digest = referral_release_facts(actual)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def reservation_release_facts(row):
    """Check a cancellation's frozen sources using reads local to this receipt."""
    from orders.reservation_cancel_funding_test import _digest as evidence_digest
    from stores.context import current_store
    allocation, evidence = row.allocation, row.evidence
    proof, redemption = evidence.points_refund, evidence.points_refund.redemption
    refund = proof.refund
    facts = evidence.facts
    expected = [allocation.pk, allocation.receipt_id, allocation.amount]
    if (not current_store() or evidence.store_slug != current_store().slug
            or not isinstance(facts, dict) or not isinstance(facts.get('allocations'), list)
            or expected not in facts['allocations']
            or facts.get('order_id') != redemption.order_id or facts.get('buyer_id') != redemption.ledger.account_id
            or redemption.payment.wallet.account_id != redemption.ledger.account_id
            or allocation.redemption_id != redemption.pk or allocation.service_order_id is not None
            or row.amount != allocation.amount or not proof.is_final
            or proof.equivalent_units != redemption.deal_units or proof.coin_units != redemption.paid_units
            or proof.points_hundredths != redemption.points_hundredths
            or refund.order_id != redemption.order_id or refund.amount != proof.coin_units
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('reservation_points_prestart_cancel') is not True
            or evidence.evidence_hash != evidence_digest(proof.pk, evidence.store_slug, facts)):
        raise ValidationError('预约退款释放来源凭证异常')
    payment, spent = redemption.payment, redemption.ledger
    if (payment.order_id != redemption.order_id or payment.status != 'SUCCESS' or payment.tx_type != 'PAY'
            or payment.amount != -redemption.paid_units or payment.reversal_of_id or payment.reversals.exists()
            or payment.balance_after != payment.balance_before + payment.amount
            or spent.order_id != redemption.order_id or spent.kind != 'ORDER_POINTS_REDEEM'
            or spent.event_key != f'order:{redemption.order_id}:points-redemption'
            or spent.amount != -redemption.points_hundredths):
        raise ValidationError('预约原付款或积分流水异常')
    coin, points = proof.coin_transaction, proof.points_ledger
    if (proof.coin_units and (not coin or coin.order_id != redemption.order_id or coin.wallet_id != payment.wallet_id
            or coin.status != 'SUCCESS' or coin.tx_type != 'REFUND' or coin.amount != proof.coin_units
            or coin.operator_id != refund.operator_id or coin.operator_account_id != refund.operator_account_id
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists())):
        raise ValidationError('预约退款店币流水异常')
    if (proof.points_hundredths and (not points or points.order_id != redemption.order_id
            or points.account_id != redemption.ledger.account_id or points.kind != 'ORDER_POINTS_RETURN'
            or points.amount != proof.points_hundredths or points.operator_id != refund.operator_account_id
            or points.event_key != f'order:{redemption.order_id}:points-return:{refund.pk}')):
        raise ValidationError('预约退款积分流水异常')
    payload = ['reservation-cancel', row.pk, row.amount, allocation.pk, allocation.receipt_id,
               allocation.redemption_id, evidence.pk, evidence.store_slug, evidence.evidence_hash,
               proof.pk, proof.refund_id, proof.equivalent_units, proof.coin_units, proof.points_hundredths,
               proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final,
               payment.pk, payment.amount, spent.pk, spent.amount,
               coin.pk if coin else None, coin.amount if coin else None,
               points.pk if points else None, points.amount if points else None]
    return ('RELEASE', f'order:{allocation.pk}', f'reservation-release:{row.pk}', row.amount, _digest(payload))


def record_reservation_release_locked(row):
    """Append the release while its writer owns the order and receipt locks."""
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('预约补资释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    kind, key, request, amount, digest = reservation_release_facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def prestart_cancel_release_facts(row):
    from stores.context import current_store
    from orders.models import OrderAddonFundingRelease
    from orders.points_prestart_cancel_funding import _digest as evidence_digest
    from orders.points_prestart_receipt_usage import facts as prior_facts
    allocation, evidence = row.allocation, row.evidence
    proof, redemption = evidence.points_refund, evidence.points_refund.redemption
    refund, facts = proof.refund, evidence.facts
    if (not current_store() or evidence.store_slug != current_store().slug or not isinstance(facts, dict)
            or not isinstance(facts.get('allocations'), list) or not isinstance(facts.get('prior_releases'), list)
            or not isinstance(facts.get('prior_facts'), list) or facts.get('order_id') != redemption.order_id
            or facts.get('buyer_id') != redemption.ledger.account_id or allocation.redemption_id != redemption.pk
            or allocation.service_order_id is not None or not proof.is_final
            or proof.equivalent_units != redemption.deal_units or proof.coin_units != redemption.paid_units
            or proof.points_hundredths != redemption.points_hundredths or refund.order_id != redemption.order_id
            or refund.amount != proof.coin_units or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_prestart_cancel_funding') is not True
            or evidence.evidence_hash != evidence_digest(proof.pk, evidence.store_slug, facts)):
        raise ValidationError('服务前取消补资释放来源异常')
    entries = facts['allocations']
    if (any(not isinstance(item, list) or len(item) != 4 or any(type(x) is not int or x < 0 for x in item)
            for item in entries) or len({item[0] for item in entries}) != len(entries)):
        raise ValidationError('服务前取消到账分配快照异常')
    matches = [item for item in entries if item[0] == allocation.pk]
    if (len(matches) != 1 or matches[0][1:3] != [allocation.receipt_id, allocation.amount]
            or row.amount != allocation.amount - matches[0][3] or row.amount <= 0):
        raise ValidationError('服务前取消释放金额异常')
    ids = [item[0] for item in entries]
    prior = list(OrderAddonFundingRelease.objects.filter(allocation_id__in=ids,
        run__created_at__lt=evidence.created_at).select_related('allocation__redemption', 'run__prestart_handoff')
        .order_by('pk'))
    if ([[item.pk, item.allocation_id, item.amount] for item in prior] != facts['prior_releases']
            or [list(prior_facts(item)) for item in prior] != facts['prior_facts']
            or any(sum(item.amount for item in prior if item.allocation_id == entry[0]) != entry[3] for entry in entries)):
        raise ValidationError('服务前取消先前释放记录异常')
    payment, spent = redemption.payment, redemption.ledger
    if (payment.order_id != redemption.order_id or payment.status != 'SUCCESS' or payment.tx_type != 'PAY'
            or payment.amount != -redemption.paid_units or payment.wallet.account_id != spent.account_id
            or payment.balance_after != payment.balance_before + payment.amount or payment.reversal_of_id
            or payment.reversals.exists() or spent.order_id != redemption.order_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{redemption.order_id}:points-redemption'):
        raise ValidationError('服务前取消原付款或积分流水异常')
    coin, points = proof.coin_transaction, proof.points_ledger
    if proof.coin_units and (not coin or coin.order_id != redemption.order_id or coin.wallet_id != payment.wallet_id
            or coin.status != 'SUCCESS' or coin.tx_type != 'REFUND' or coin.amount != proof.coin_units
            or coin.operator_id != refund.operator_id or coin.operator_account_id != refund.operator_account_id
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists()):
        raise ValidationError('服务前取消店币退款流水异常')
    if proof.points_hundredths and (not points or points.order_id != redemption.order_id
            or points.account_id != spent.account_id or points.kind != 'ORDER_POINTS_RETURN'
            or points.amount != proof.points_hundredths or points.operator_id != refund.operator_account_id
            or points.event_key != f'order:{redemption.order_id}:points-return:{refund.pk}'):
        raise ValidationError('服务前取消积分退款流水异常')
    return ('RELEASE', f'order:{allocation.pk}', f'points-prestart-cancel:{row.pk}', row.amount,
            _digest(['points-prestart-cancel', row.pk, allocation.pk, allocation.receipt_id, row.amount,
                     evidence.pk, evidence.evidence_hash, proof.pk, refund.pk, payment.pk, spent.pk,
                     coin.pk if coin else None, points.pk if points else None]))


def record_prestart_cancel_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('服务前取消释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    kind, key, request, amount, digest = prestart_cancel_release_facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def first_negotiation_release_facts(row):
    """Recheck frozen first-refund facts without locking an order or another receipt."""
    from orders.models import (OrderPointsFundingReleaseEvidence, OrderAddonFundingReleaseRun,
                               PointsFirstRefundPaidPersonEvidence)
    from orders.points_funding_release import first_release_hash_matches
    from orders.points_negotiation_execution import participant_digest
    from orders.points_funding_allocation import plan_release, PointsFundingAllocationError
    posting, allocation = row.posting, row.allocation
    read_receipt_id = getattr(row, 'read_receipt_id', allocation.receipt_id)
    proposal, proof = posting.proposal, posting.points_refund
    redemption, refund = proof.redemption, proof.refund
    evidence = OrderPointsFundingReleaseEvidence.objects.filter(posting=posting).first()
    historical_empty = bool(evidence and not evidence.allocations and row.pk == 0 and row.amount == 0
                            and allocation.created_at > posting.created_at)
    team_runs = list(OrderAddonFundingReleaseRun.objects.filter(
        handover__payment__intent__order_id=redemption.order_id,
        points_handover_snapshot__kind='SAME_TEAM_V1', created_at__lt=posting.created_at)
        .select_related('handover').order_by('handover__accepted_at', 'pk'))
    team = bool(team_runs)
    paid_source = PointsFirstRefundPaidPersonEvidence.objects.filter(posting=posting).exists()
    terms = getattr(proposal, 'points_terms', None)
    if (not evidence or not terms or terms.prior_refund_id or not isinstance(posting.participants, list)
            or not isinstance(evidence.allocations, list) or not isinstance(evidence.prior_addon_release_snapshot, list)
            or posting.participants_hash != participant_digest(proposal, posting.participants)
            or posting.terms_hash != proposal.terms_hash or proposal.status != 'ACCEPTED'
            or posting.accepted_at != proposal.accepted_at or terms.redemption_id != redemption.pk
            or allocation.funding_order_id != redemption.order_id
            or ([allocation.pk, allocation.amount, allocation.receipt_id] not in evidence.allocations
                and not historical_empty)
            or proof.equivalent_units != proposal.refund_units or refund.order_id != redemption.order_id
            or refund.amount != proof.coin_units or refund.operator_account_id != posting.operator_id
            or (terms.coin_units, terms.points_hundredths, terms.prepaid_units, terms.gift_units,
                terms.compensation_units, terms.is_final) != (proof.coin_units, proof.points_hundredths,
                proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final)
            or not first_release_hash_matches(evidence, posting)):
        raise ValidationError('首次积分退款释放来源凭证异常')
    from orders.models import OrderPointsSettlement
    original = OrderPointsSettlement.objects.filter(order_id=redemption.order_id,
        pk=evidence.original_settlement_id).first() if evidence.original_settlement_id else None
    paid_before = bool(original and original.persons.filter(settled_at__lt=evidence.created_at).exists())
    if paid_before:
        from orders.points_first_paid_source import verify_current
        verify_current(posting, evidence, receipt_id=read_receipt_id)
    elif PointsFirstRefundPaidPersonEvidence.objects.filter(posting=posting).exists():
        raise ValidationError('首退已付人员与到账来源不一致')
    from orders.models import OrderOriginalShareTransfer
    if OrderOriginalShareTransfer.objects.filter(
            previous_segment__attribution__order_id=redemption.order_id).exists():
        from orders.points_pretransfer_refund_source import verify_participants_locked
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            verify_participants_locked(posting,
                receipt_id=read_receipt_id)
        except RestValidationError as exc:
            raise ValidationError('首次转单前的退款履约凭证异常') from exc
    addon_payload = None
    prior_by_allocation = {}
    if evidence.addon_funding_snapshot is not None or evidence.prior_addon_release_snapshot:
        # Receipt callers hold this receipt and use READ COMMITTED current reads.
        # Rebuild the other events without taking another order or receipt lock.
        from orders.models import OrderAddonFundingRelease, OrderAddonPayment
        from orders.points_addon_receipt_usage import facts as addon_facts, payment_facts
        from orders.points_funding_release import _compute
        from orders.addon_funding_release import verify_run
        order = redemption.order
        payments = list(OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=posting.created_at).order_by('pk'))
        if OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at=posting.created_at).exists():
            raise ValidationError('加购付款与主单退款时序异常')
        if evidence.addon_funding_snapshot is not None and not payments:
            raise ValidationError('主单退款前加购付款或退款范围异常')
        if evidence.addon_funding_snapshot is None and payments:
            raise ValidationError('主单退款前加购资金快照缺失')
        prior_refunds = sorted((refund for item in payments for refund in
                         item.refund_records.filter(created_at__lt=posting.created_at)),
                         key=lambda item: (item.created_at, item.pk))
        from orders.points_team_other_addon_refund import applies as team_other_applies
        if team and (len({item.created_at for item in prior_refunds}) != len(prior_refunds) or any(
                not any(run.handover.payment_id == item.payment_id and run.created_at < item.created_at
                        for run in team_runs) and not team_other_applies(item) for item in prior_refunds)):
            raise ValidationError('同单接替后的加购退款事件顺序待核对')
        if any(item.refund_records.filter(created_at=posting.created_at).exists() for item in payments):
            raise ValidationError('加购退款与主单退款时序异常')
        prior_refund = prior_refunds[0] if prior_refunds else None
        payment_snapshot = payment_facts(payments[0], read_receipt_id=read_receipt_id) if payments else None
        refund_snapshot = addon_facts(prior_refund, read_receipt_id=read_receipt_id) if prior_refund else None
        if len(prior_refunds) > 1:
            refund_snapshot = [addon_facts(item, read_receipt_id=read_receipt_id) for item in prior_refunds]
        ids = [item[0] for item in evidence.allocations]
        prior = list(OrderAddonFundingRelease.objects.filter(allocation_id__in=ids,
            run__created_at__lt=posting.created_at).select_related('run').order_by('pk'))
        actual_prior = [[item.pk, item.allocation_id, item.run_id, item.amount] for item in prior]
        if actual_prior != evidence.prior_addon_release_snapshot:
            raise ValidationError('主单退款前加购释放记录异常')
        prestart_payload = []
        if team:
            from orders.points_team_addon_handover_facts import has_ordered_funding_chain
            if not has_ordered_funding_chain(team_runs):
                raise ValidationError('同单加购接替前版资金事件异常')
            refund_runs = []
            events = [(run.handover.accepted_at, 'handover', run) for run in team_runs]
            events += [(item.created_at, 'refund', item) for item in prior_refunds]
            if any(not item[0] for item in events) or len({item[0] for item in events}) != len(events):
                raise ValidationError('同单接替与退款时序无法区分')
            previous_run_at, accepted_payments = None, set()
            for event_at, kind, item in sorted(events, key=lambda entry: entry[0]):
                if previous_run_at and event_at <= previous_run_at:
                    raise ValidationError('同单资金版本时间顺序异常')
                if kind == 'handover':
                    event_run = item
                    accepted_payments.add(item.handover.payment_id)
                else:
                    event_runs = list(OrderAddonFundingReleaseRun.objects.filter(refund=item,
                        created_at__lt=posting.created_at))
                    if ((item.payment_id not in accepted_payments and not team_other_applies(item))
                            or len(event_runs) != 1 or not event_runs[0].points_addon_snapshot):
                        raise ValidationError('同单接替后的加购退款资金版本缺失')
                    event_run = event_runs[0]
                    refund_runs.append(event_run)
                if event_run.created_at < event_at:
                    raise ValidationError('同单资金版本早于实际事件')
                verify_run(event_run, _skip_usage=True, witness_receipt_id=read_receipt_id)
                previous_run_at = event_run.created_at
        for item in prior:
            if item.run.prestart_handoff_id:
                from orders.points_prestart_receipt_usage import facts as prestart_facts
                prestart_payload.append(list(prestart_facts(item)))
            else:
                if (not payments or (item.run.payment_id not in {payment.pk for payment in payments}
                        and item.run.refund_id not in {refund.pk for refund in prior_refunds}
                        and (not team or item.run_id not in {run.pk for run in team_runs}))):
                    raise ValidationError('加购释放来源超出已核验范围')
                verify_run(item.run, _skip_usage=True, witness_receipt_id=read_receipt_id)
            prior_by_allocation[item.allocation_id] = prior_by_allocation.get(item.allocation_id, 0) + item.amount
        net = [[item[0], item[1] - prior_by_allocation.get(item[0], 0), item[2]]
               for item in evidence.allocations]
        if any(item[1] < 0 for item in net):
            raise ValidationError('加购释放超过原到账分配')
        recomputed, addon_snapshot = _compute(posting, net, evidence.original_provider_income,
            read_receipt_id=read_receipt_id)
        if (addon_snapshot != evidence.addon_funding_snapshot
                or recomputed.release_units != evidence.expected_release):
            raise ValidationError('主单与加购退款的整单补资重建不一致')
        if len(payments) > 1:
            from orders.points_addon_receipt_usage import _prior_addon_events
            addon_payload = [_prior_addon_events(order, posting.created_at, read_receipt_id=read_receipt_id),
                             actual_prior]
        else:
            addon_payload = [payment_snapshot, refund_snapshot, actual_prior]
        if prestart_payload:
            addon_payload.append(prestart_payload)
        if team:
            for team_run in team_runs:
                addon_payload.append(['SAME_TEAM_V1', team_run.pk, team_run.points_handover_hash])
            for refund_run in refund_runs:
                addon_payload.append(['TEAM_ADDON_REFUND_V1', refund_run.pk,
                                      refund_run.points_addon_hash])
    try:
        planned = plan_release(allocations=[(item[0], item[1], prior_by_allocation.get(item[0], 0))
                                            for item in evidence.allocations],
                               amount=evidence.expected_release)
    except (TypeError, IndexError, PointsFundingAllocationError) as exc:
        raise ValidationError('积分退款释放分配快照异常') from exc
    releases = list(posting.funding_releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    if releases != [(part.allocation_id, part.amount) for part in planned]:
        raise ValidationError('积分退款释放分配不一致')
    payment, spent = redemption.payment, redemption.ledger
    if (payment.order_id != redemption.order_id or payment.status != 'SUCCESS' or payment.tx_type != 'PAY'
            or payment.amount != -redemption.paid_units or payment.reversal_of_id or payment.reversals.exists()
            or payment.balance_after != payment.balance_before + payment.amount
            or payment.wallet.account_id != spent.account_id or spent.order_id != redemption.order_id
            or spent.kind != 'ORDER_POINTS_REDEEM' or spent.amount != -redemption.points_hundredths
            or spent.event_key != f'order:{redemption.order_id}:points-redemption'):
        raise ValidationError('积分退款原付款或积分流水异常')
    coin, points = proof.coin_transaction, proof.points_ledger
    if proof.coin_units and (not coin or coin.order_id != redemption.order_id or coin.wallet_id != payment.wallet_id
            or coin.status != 'SUCCESS' or coin.tx_type != 'REFUND' or coin.amount != proof.coin_units
            or coin.operator_id != refund.operator_id or coin.operator_account_id != refund.operator_account_id
            or coin.balance_after != coin.balance_before + coin.amount or coin.reversal_of_id or coin.reversals.exists()):
        raise ValidationError('积分退款店币流水异常')
    if proof.points_hundredths and (not points or points.order_id != redemption.order_id
            or points.account_id != spent.account_id or points.kind != 'ORDER_POINTS_RETURN'
            or points.amount != proof.points_hundredths or points.operator_id != refund.operator_account_id
            or points.event_key != f'order:{redemption.order_id}:points-return:{refund.pk}'):
        raise ValidationError('积分退款积分流水异常')
    decisions = list(proposal.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    shares = list(posting.shares.order_by('account_id').values_list('account_id', 'assignment_id', 'original_income',
        'burden', 'pending_deduction', 'wallet_available_before', 'deposit_paid_before', 'wallet_deduction',
        'deposit_deduction', 'retained_income', 'wallet_transaction_id', 'deposit_deficit_id'))
    from copy import copy
    from orders.negotiation_proposals import _digest as proposal_digest, _snapshot_terms
    episode = copy(proposal.case)
    episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
    expected_decisions = {(spent.account_id, 'BOSS', True)} | {
        (share[0], 'PROVIDER', True) for share in shares}
    if (episode.order_id != redemption.order_id or episode.account_id != spent.account_id
            or not shares or set((account, role, accepted) for account, role, accepted, _ in decisions) != expected_decisions
            or any(not decided for _, _, _, decided in decisions)
            or proposal.accepted_at != max(decided for _, _, _, decided in decisions)
            or sorted(proposal.affected_accounts) != sorted(share[0] for share in shares)
            or _snapshot_terms(proposal.provider_terms, proposal.affected_accounts) != proposal.provider_terms
            or proposal.terms_hash != proposal_digest(episode, proposal.version, proposal.affected_accounts,
                proposal.refund_units, proposal.provider_terms, proposal.explanation, proposal.affected_segment_ids)):
        raise ValidationError('主单退款双方确认凭证异常')
    share_money = []
    for share in posting.shares.select_related('wallet_transaction', 'deposit_deficit__source_store_transaction').order_by('account_id'):
        tx = share.wallet_transaction
        deficit = share.deposit_deficit
        deposit_tx = deficit.source_store_transaction if deficit else None
        if share.wallet_deduction and (not tx or tx.order_id != redemption.order_id
                or tx.wallet.account_id != share.account_id or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                or tx.amount != -share.wallet_deduction or tx.balance_before != share.wallet_available_before
                or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
            raise ValidationError('积分退款陪玩扣款流水异常')
        if share.deposit_deduction and (not deficit or deficit.refund_id != refund.pk
                or deficit.account_id != share.account_id or deficit.amount != share.deposit_deduction
                or not deposit_tx or deposit_tx.order_id != redemption.order_id
                or deposit_tx.tx_type != 'DEPOSIT_INCOME' or deposit_tx.status != 'SUCCESS'
                or deposit_tx.amount != -share.deposit_deduction or deposit_tx.reversal_of_id
                or deposit_tx.reversals.exists() or deposit_tx.balance_after != deposit_tx.balance_before + deposit_tx.amount):
            raise ValidationError('积分退款押金扣款流水异常')
        share_money.append([share.pk, tx.pk if tx else None, tx.order_id if tx else None,
            tx.wallet_id if tx else None, tx.tx_type if tx else None, tx.amount if tx else None,
            tx.status if tx else None, tx.operator_id if tx else None, tx.operator_account_id if tx else None,
            tx.reversal_of_id if tx else None, tx.balance_before if tx else None, tx.balance_after if tx else None,
            deficit.pk if deficit else None, deficit.refund_id if deficit else None,
            deficit.account_id if deficit else None, deficit.profile_id if deficit else None,
            deficit.amount if deficit else None, deficit.source_kind if deficit else None,
            deficit.direct_key if deficit else None, deposit_tx.pk if deposit_tx else None,
            deposit_tx.order_id if deposit_tx else None, deposit_tx.wallet_id if deposit_tx else None,
            deposit_tx.tx_type if deposit_tx else None, deposit_tx.amount if deposit_tx else None,
            deposit_tx.status if deposit_tx else None, deposit_tx.operator_id if deposit_tx else None,
            deposit_tx.operator_account_id if deposit_tx else None, deposit_tx.reversal_of_id if deposit_tx else None,
            deposit_tx.balance_before if deposit_tx else None, deposit_tx.balance_after if deposit_tx else None])
    payload = ['first-negotiation', row.pk, row.amount, allocation.pk, allocation.receipt_id,
        evidence.pk, evidence.evidence_hash, posting.pk, posting.terms_hash, posting.participants_hash,
        proposal.pk, proposal.status, proposal.provider_terms, proposal.affected_accounts, terms.evidence_hash,
        [[a, b, c, d.isoformat() if d else None] for a, b, c, d in decisions], shares, share_money,
        proof.pk, proof.refund_id, proof.equivalent_units, proof.coin_units, proof.points_hundredths,
        proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final,
        payment.pk, payment.amount, spent.pk, spent.amount, coin.pk if coin else None, coin.amount if coin else None,
        points.pk if points else None, points.amount if points else None]
    if addon_payload is not None:
        payload.append(addon_payload)
    return ('RELEASE', f'order:{allocation.pk}', f'points-first-release:{row.pk}', row.amount, _digest(payload))


def record_first_negotiation_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('积分退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    kind, key, request, amount, digest = first_negotiation_release_facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def first_addon_release_facts(row, *, witness_receipt_id=None):
    from orders.addon_funding_release import verify_run
    from orders.models import OrderAddonFundingReleaseRun
    run, allocation = row.run, row.allocation
    from orders.points_team_other_addon_refund import applies as team_other_applies
    team = bool(run.refund_id and (OrderAddonFundingReleaseRun.objects.filter(
        handover__payment_id=run.refund.payment_id, points_handover_snapshot__kind='SAME_TEAM_V1',
        handover__accepted_at__lt=run.refund.created_at).exists() or team_other_applies(run.refund)))
    if (not run.refund_id or not run.points_addon_snapshot or not run.points_addon_hash
            or (team and allocation.funding_order_id != run.refund.order_id)
            or (not team and (allocation.redemption_id is None or allocation.service_order_id is not None
                              or allocation.redemption.order_id != run.refund.order_id))):
        raise ValidationError('积分加购退款释放来源异常')
    try:
        verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        if run.refund_id:
            from orders.ordinary_addon_paid_receipt import verify_run as verify_paid_refund
            from orders.points_voluntary_paid_evidence import verify_standard_refund_current_read
            verify_paid_refund(run)
            verify_standard_refund_current_read(run)
    except Exception as exc:
        from rest_framework.exceptions import ValidationError as RestValidationError
        if isinstance(exc, (RestValidationError, ValidationError)):
            raise ValidationError('积分加购退款释放依据异常') from exc
        raise
    return ('RELEASE', f'order:{allocation.pk}', f'points-addon-release:{row.pk}', row.amount,
            _digest(['points-addon-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     run.pk, run.refund_id, run.points_addon_hash, run.required_amount,
                     run.allocated_before, run.released_amount, run.allocation_snapshot]))


def addon_payment_release_facts(row, *, witness_receipt_id=None):
    from orders.addon_funding_release import verify_run
    run, allocation = row.run, row.allocation
    if (not run.payment_id or not run.points_payment_snapshot or not run.points_payment_hash
            or allocation.redemption_id is None or allocation.service_order_id is not None
            or allocation.redemption.order_id != run.payment.intent.order_id):
        raise ValidationError('积分加购付款释放来源异常')
    try:
        verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
    except Exception as exc:
        from rest_framework.exceptions import ValidationError as RestValidationError
        if isinstance(exc, (RestValidationError, ValidationError)):
            raise ValidationError('积分加购付款释放依据异常') from exc
        raise
    return ('RELEASE', f'order:{allocation.pk}', f'points-addon-payment-release:{row.pk}', row.amount,
            _digest(['points-addon-payment-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     run.pk, run.payment_id, run.points_payment_hash, run.required_amount,
                     run.allocated_before, run.released_amount, run.allocation_snapshot]))


def _addon_release_facts(row, *, witness_receipt_id=None):
    from orders.models import PointsThirdAddonFundingRevision
    if PointsThirdAddonFundingRevision.objects.filter(run_id=row.run_id).exists():
        from orders.points_third_addon_receipt_usage import facts
        return facts(row, witness_receipt_id=witness_receipt_id)
    if (row.run.negotiation_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('ordinary_transfer_followup_version') == 1):
        from orders.ordinary_transfer_followup_receipt import verify
        run, allocation = row.run, row.allocation
        if (allocation.service_order_id != run.negotiation.refund.order_id
                or allocation.redemption_id is not None or row.amount <= 0):
            raise ValidationError('追加转单退款到账释放来源异常')
        verify(run, witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-transfer-followup-refund:{row.pk}', row.amount,
                _digest(['ordinary-transfer-followup-refund', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                         run.pk, run.negotiation_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.negotiation_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('ordinary_transfer_refund_version') == 1):
        from orders.ordinary_transfer_first_receipt import verify
        run, allocation = row.run, row.allocation
        if (allocation.service_order_id != run.negotiation.refund.order_id
                or allocation.redemption_id is not None or row.amount <= 0):
            raise ValidationError('首次转单退款到账释放来源异常')
        verify(run, witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-first-transfer-refund:{row.pk}', row.amount,
                _digest(['ordinary-first-transfer-refund', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                         run.pk, run.negotiation_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if row.run.ordinary_transfer_revision_id:
        from orders.addon_funding_release import verify_run
        run, allocation = row.run, row.allocation
        if (allocation.redemption_id is not None or allocation.service_order_id != run.ordinary_transfer_revision.order_id
                or row.amount <= 0):
            raise ValidationError('普通转单到账释放来源异常')
        verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-transfer-release:{row.pk}', row.amount,
                _digest(['ordinary-transfer-release', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                         run.pk, run.ordinary_transfer_revision_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.handover_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('kind') == 'ordinary_refund_addon_handover_v1'):
        from orders.ordinary_refund_addon_payment_release import verify
        run, allocation = row.run, row.allocation
        if allocation.service_order_id != run.handover.payment.intent.order_id or allocation.redemption_id is not None or row.amount <= 0:
            raise ValidationError('退款后加购接替到账释放来源异常')
        verify(run, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-refund-addon-handover-release:{row.pk}', row.amount,
                _digest(['ordinary-refund-addon-handover-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.handover_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.payment_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('kind') == 'ordinary_refund_addon_payment_v1'):
        from orders.ordinary_refund_addon_payment_release import verify
        run, allocation = row.run, row.allocation
        if allocation.service_order_id != run.payment.intent.order_id or allocation.redemption_id is not None or row.amount <= 0:
            raise ValidationError('退款后加购付款到账释放来源异常')
        verify(run, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-refund-addon-payment-release:{row.pk}', row.amount,
                _digest(['ordinary-refund-addon-payment-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.payment_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.negotiation_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and 'followup_link_id' in row.run.ordinary_refund_snapshot):
        from orders.ordinary_followup_receipt import facts
        return facts(row, witness_receipt_id=witness_receipt_id)
    if (row.run.refund_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('kind') == 'ordinary_followup_addon_refund_v1'):
        from orders.ordinary_followup_addon_receipt import verify
        run, allocation = row.run, row.allocation
        if allocation.service_order_id != run.refund.order_id or allocation.redemption_id is not None or row.amount <= 0:
            raise ValidationError('追加协商后加购退款到账释放来源异常')
        verify(run, witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-followup-addon-release:{row.pk}', row.amount,
                _digest(['ordinary-followup-addon-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.refund_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.refund_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and row.run.ordinary_refund_snapshot.get('kind') == 'ordinary_addon_after_main_refund_v1'):
        from orders.ordinary_after_main_addon_receipt import verified_release
        run, allocation = row.run, row.allocation
        if (allocation.service_order_id != run.refund.order_id or allocation.redemption_id is not None
                or row.amount <= 0):
            raise ValidationError('普通主单后加购退款到账释放来源异常')
        verified_release(run, witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-after-main-addon-release:{row.pk}', row.amount,
                _digest(['ordinary-after-main-addon-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.refund_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if (row.run.negotiation_id and isinstance(row.run.ordinary_refund_snapshot, dict)
            and 'combined' in row.run.ordinary_refund_snapshot
            and 'addon_history' in row.run.ordinary_refund_snapshot):
        from orders.ordinary_refund_receipt_evidence import verified_release
        run, allocation = row.run, row.allocation
        if (allocation.service_order_id != run.negotiation.refund.order_id
                or allocation.redemption_id is not None or row.amount <= 0):
            raise ValidationError('普通主单退款到账释放来源异常')
        verified_release(run, witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-main-refund-release:{row.pk}', row.amount,
                _digest(['ordinary-main-refund-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.negotiation_id, run.ordinary_refund_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if row.run.ordinary_prestart_snapshot is not None or row.run.ordinary_prestart_hash:
        from orders.addon_funding_release import verify_run
        run, allocation = row.run, row.allocation
        order_id = (run.payment.intent.order_id if run.payment_id else
                    run.refund.order_id if run.refund_id else
                    run.handover.payment.intent.order_id if run.handover_id else None)
        if (not order_id or allocation.service_order_id != order_id or allocation.redemption_id is not None
                or not run.ordinary_prestart_snapshot or not run.ordinary_prestart_hash):
            raise ValidationError('普通改派加购补资释放来源异常')
        verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        return ('RELEASE', f'order:{allocation.pk}', f'ordinary-prestart-addon-release:{row.pk}', row.amount,
                _digest(['ordinary-prestart-addon-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.payment_id, run.refund_id,
                         *([run.handover_id] if run.handover_id else []),
                         run.ordinary_prestart_hash, run.required_amount, run.allocated_before,
                         run.released_amount, run.allocation_snapshot]))
    if row.run.points_followup_snapshot is not None:
        if (isinstance(row.run.points_followup_snapshot, dict)
                and row.run.points_followup_snapshot.get('kind') == 'TRANSFER_FOLLOWUP_V1'):
            from orders.points_transfer_followup_addon_version import receipt_facts
        else:
            from orders.points_followup_addon_version import receipt_facts
        return receipt_facts(row, witness_receipt_id=witness_receipt_id)
    if row.run.prestart_handoff_id:
        from orders.points_prestart_receipt_usage import facts
        return facts(row)
    if row.run.handover_id and row.run.refunded_main_snapshot is not None:
        return after_main_addon_release_facts(row, witness_receipt_id=witness_receipt_id)
    if row.run.points_handover_snapshot is not None:
        from orders.addon_funding_release import verify_run
        run, allocation = row.run, row.allocation
        team = isinstance(run.points_handover_snapshot, dict) and run.points_handover_snapshot.get('kind') == 'SAME_TEAM_V1'
        owner_id = allocation.funding_order_id if team else (allocation.redemption.order_id if allocation.redemption_id else None)
        if (not run.handover_id or not run.points_handover_hash
                or owner_id != run.handover.payment.intent.order_id):
            raise ValidationError('积分加购接替释放来源异常')
        try:
            verify_run(run, _skip_usage=True, witness_receipt_id=witness_receipt_id or allocation.receipt_id)
        except Exception as exc:
            from rest_framework.exceptions import ValidationError as RestValidationError
            if isinstance(exc, (RestValidationError, ValidationError)):
                raise ValidationError('积分加购接替释放依据异常') from exc
            raise
        return ('RELEASE', f'order:{allocation.pk}', f'points-addon-handover-release:{row.pk}', row.amount,
                _digest(['points-addon-handover-release', row.pk, row.amount, allocation.pk,
                         allocation.receipt_id, run.pk, run.handover_id, run.points_handover_hash,
                         run.required_amount, run.allocated_before, run.released_amount, run.allocation_snapshot]))
    if row.run.refunded_main_snapshot is not None:
        return after_main_addon_release_facts(row, witness_receipt_id=witness_receipt_id)
    if row.run.payment_id:
        return addon_payment_release_facts(row, witness_receipt_id=witness_receipt_id)
    return first_addon_release_facts(row, witness_receipt_id=witness_receipt_id)


def after_main_addon_release_facts(row, *, witness_receipt_id=None):
    """Rebuild a post-refund add-on event under the already-held receipt lock."""
    from types import SimpleNamespace
    from orders.models import (OrderAddonFundingRelease, OrderPointsFundingAllocation,
                               OrderPointsFundingRelease, PointsNegotiationPosting)
    from orders.points_addon_receipt_usage import facts as addon_facts, payment_facts, digest as addon_digest
    from orders.points_funding_allocation import plan_release, PointsFundingAllocationError
    from orders.refunded_addon_revision import verify as verify_revision
    run, allocation = row.run, row.allocation
    witness_receipt_id = witness_receipt_id or allocation.receipt_id
    is_payment = bool(run.payment_id)
    is_handover = bool(run.handover_id)
    transfer_history_before = (run.payment.buyer_transaction.created_at if is_payment else
                               run.handover.accepted_at if is_handover else
                               run.refund.created_at if run.refund_id else None)
    event_order_id = (run.payment.intent.order_id if is_payment else run.refund.order_id if run.refund_id
                      else run.handover.payment.intent.order_id if is_handover else None)
    event_snapshot = (run.points_payment_snapshot if is_payment else run.points_handover_snapshot
                      if is_handover else run.points_addon_snapshot)
    event_hash = (run.points_payment_hash if is_payment else run.points_handover_hash
                  if is_handover else run.points_addon_hash)
    # Same-team handover funding can have either order-owned allocation kind.
    # Other post-main events retain their existing redemption-only boundary.
    from orders.models import OrderAddonFundingReleaseRun
    team_refund = bool(run.refund_id and OrderAddonFundingReleaseRun.objects.filter(
        handover__payment_id=run.refund.payment_id,
        points_handover_snapshot__kind='SAME_TEAM_V1',
        handover__accepted_at__lt=run.refund.created_at).exists())
    team_handover = bool(is_handover and isinstance(event_snapshot, dict)
                         and event_snapshot.get('kind') == 'SAME_TEAM_V1')
    team_payment_runs = (list(OrderAddonFundingReleaseRun.objects.filter(
        handover__payment__intent__order_id=event_order_id, points_handover_snapshot__kind='SAME_TEAM_V1',
        created_at__lt=run.payment.buyer_transaction.created_at).order_by('created_at', 'pk')) if is_payment else [])
    team_payment = bool(team_payment_runs)
    owner_invalid = (allocation.funding_order_id != event_order_id if team_refund or team_handover or team_payment else
                     allocation.redemption_id is None or allocation.service_order_id is not None
                     or allocation.redemption.order_id != event_order_id)
    if (sum((bool(run.refund_id), is_payment, is_handover)) != 1
            or owner_invalid
            or not isinstance(run.allocation_snapshot, list)
            or not isinstance(run.refunded_main_snapshot, dict)
            or not isinstance(event_snapshot, dict) or event_hash != addon_digest(event_snapshot)
            or (payment_facts(run.payment, read_receipt_id=witness_receipt_id) if is_payment
                else None if is_handover else addon_facts(run.refund, read_receipt_id=witness_receipt_id))
                != (None if is_handover else event_snapshot)):
        raise ValidationError('主单退款后的加购资金来源异常')
    if run.refund_id:
        # This reads only the immutable paid snapshot and its real ledgers.
        # The receipt replay verifies the refund and release separately below.
        from orders.points_voluntary_paid_evidence import verify_current_read
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            verify_current_read(run)
        except RestValidationError as exc:
            raise ValidationError('加购退款前已付流水凭证异常') from exc
    if run.payment_id:
        from orders.points_voluntary_paid_evidence import verify_payment_current_read
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            verify_payment_current_read(run)
        except RestValidationError as exc:
            raise ValidationError('加购付款前已付流水凭证异常') from exc
    if is_handover:
        if event_snapshot.get('kind') == 'POINTS_VOLUNTARY_HANDOVER_V1':
            from orders.points_voluntary_handover_event import facts as voluntary_facts
            from rest_framework.exceptions import ValidationError as RestValidationError
            try:
                actual_handover = voluntary_facts(run, receipt_id=witness_receipt_id)
            except RestValidationError as exc:
                raise ValidationError('自愿接替到账来源异常') from exc
        elif team_handover:
            from orders.points_team_postmain_handover_facts import facts as team_postmain_facts
            actual_handover = team_postmain_facts(run, receipt_id=witness_receipt_id)
        else:
            from orders.points_addon_handover_receipt_usage import postmain_facts
            actual_handover = postmain_facts(run, witness_receipt_id)
        if actual_handover != event_snapshot:
            raise ValidationError('主单退款后的加购接替来源异常')
    earlier_sources = []
    if not is_payment and not is_handover:
        earlier_refunds = list(run.refund.payment.refund_records.filter(created_at__lt=run.refund.created_at)
                               .order_by('created_at', 'pk'))
        if run.refund.payment.refund_records.filter(created_at=run.refund.created_at).exclude(pk=run.refund_id).exists():
            raise ValidationError('加购退款事件时间无法区分')
        earlier_sources = [addon_facts(item, read_receipt_id=witness_receipt_id) for item in earlier_refunds]
    posting = PointsNegotiationPosting.objects.filter(pk=run.refunded_main_snapshot.get('posting_id')).first()
    evidence = getattr(posting, 'funding_release_evidence', None) if posting else None
    transferred = bool(run.refunded_main_snapshot.get('transfer_revision_id')
                       or run.refunded_main_snapshot.get('transfer_origin_posting_id'))
    from orders.models import OrderOriginalShareTransfer
    historical_pretransfer = bool(posting and isinstance(posting.participants, list)
        and OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order_id=event_order_id,
            created_at__gt=run.created_at).exists())
    if (run.refunded_main_snapshot.get('transfer_origin_posting_id')
            and (run.refunded_main_snapshot['transfer_origin_posting_id'] != run.refunded_main_snapshot.get('posting_id')
                 or run.refunded_main_snapshot.get('transfer_revision_id'))):
        raise ValidationError('主单退款后的转单资金来源异常')
    historical_empty = bool(evidence and not evidence.allocations and allocation.funding_order_id == event_order_id
                            and allocation.created_at > posting.created_at)
    if (not evidence or not isinstance(evidence.allocations, list)
            or (not evidence.allocations and not (transferred or historical_pretransfer or historical_empty))):
        raise ValidationError('主单退款的原到账凭证缺失')
    original = (OrderPointsFundingAllocation.objects.filter(pk=evidence.allocations[0][0]).first()
                if evidence.allocations else allocation)
    if not original:
        raise ValidationError('主单退款的原到账分配缺失')
    if transferred:
        from orders.points_transfer_refund_receipt_usage import facts as transfer_refund_facts
        main_fact = transfer_refund_facts(SimpleNamespace(posting=posting, allocation=original, pk=0, amount=0,
                                                         read_receipt_id=witness_receipt_id,
                                                         history_before=transfer_history_before))
    else:
        main_fact = first_negotiation_release_facts(SimpleNamespace(
            posting=posting, allocation=original, pk=0, amount=0, read_receipt_id=witness_receipt_id))
    team_payment_sources = []
    if team_payment:
        from orders.points_team_postmain_handover_facts import verify_event_current
        for prior_team in team_payment_runs:
            verify_event_current(prior_team, receipt_id=witness_receipt_id)
            team_payment_sources.append([prior_team.pk, prior_team.points_handover_hash])
    normalized, net = [], []
    transfer_sources = []
    for item in run.allocation_snapshot:
        if (not isinstance(item, list) or len(item) != (6 if transferred else 5)
                or (not transferred and item[3] != [])):
            raise ValidationError('主单退款后的加购到账分配异常')
        allocation_id, original, prior, transfer_refs, main_refs = item[:5]
        linked = OrderPointsFundingAllocation.objects.filter(pk=allocation_id).first()
        if (not linked or linked.funding_order_id != event_order_id or linked.amount != original
                or not isinstance(main_refs, list)):
            raise ValidationError('主单退款后的加购到账来源异常')
        earlier = list(OrderAddonFundingRelease.objects.filter(allocation_id=allocation_id,
            run_id__lt=run.pk).select_related('run').order_by('pk'))
        if sum(part.amount for part in earlier) != prior:
            raise ValidationError('主单退款后的既有加购释放不一致')
        for part in earlier:
            _addon_release_facts(part, witness_receipt_id=witness_receipt_id)
        main_rows = list(OrderPointsFundingRelease.objects.filter(allocation_id=allocation_id,
            created_at__lt=run.created_at).order_by('pk'))
        if [[part.pk, part.amount] for part in main_rows] != main_refs:
            raise ValidationError('主单退款后的主单释放不一致')
        for part in main_rows:
            if transferred:
                transfer_refund_facts(SimpleNamespace(posting=part.posting, allocation=part.allocation,
                    pk=part.pk, amount=part.amount, read_receipt_id=witness_receipt_id,
                    history_before=transfer_history_before))
            else:
                first_negotiation_release_facts(part)
        used = prior + sum(part.amount for part in main_rows)
        if transferred:
            from orders.models import OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease
            from orders.points_transfer_receipt_usage import facts as transfer_facts
            from orders.points_post_refund_transfer_receipt_usage import facts as post_transfer_facts
            transfer_rows = list(OrderPointsTransferFundingRelease.objects.filter(allocation_id=allocation_id,
                revision__created_at__lt=run.created_at).select_related('revision__order').order_by('pk'))
            post_rows = list(PointsPostRefundTransferRelease.objects.filter(allocation_id=allocation_id,
                revision__created_at__lt=run.created_at).select_related('revision__order', 'revision__transfer',
                    'allocation__redemption').order_by('pk'))
            if ([[part.pk, part.amount] for part in transfer_rows] != transfer_refs
                    or [[part.pk, part.amount] for part in post_rows] != item[5]):
                raise ValidationError('加购退款前转单释放引用异常')
            transfer_sources.extend(transfer_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=witness_receipt_id)) for part in transfer_rows)
            transfer_sources.extend(post_transfer_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=witness_receipt_id)) for part in post_rows)
            used += sum(part.amount for part in transfer_rows + post_rows)
        if used > original:
            raise ValidationError('主单退款后的累计释放超过分配')
        normalized.append([allocation_id, original, used])
        net.append([allocation_id, original - used, linked.receipt_id])
    try:
        before = sum(item[1] - item[2] for item in normalized)
        amount = max(before - run.required_amount, 0)
        planned = plan_release(allocations=normalized, amount=amount)
        witness = (OrderPointsFundingAllocation.objects.filter(redemption__order_id=event_order_id,
                   receipt_id=witness_receipt_id).first() if transferred or historical_pretransfer else None)
        if (transferred or historical_pretransfer) and not witness:
            raise ValidationError('主单退款后的到账见证不属于本单')
        verify_revision(run, net, receipt_local=transferred or historical_pretransfer, witness_allocation=witness)
    except (TypeError, IndexError, PointsFundingAllocationError) as exc:
        raise ValidationError('主单退款后的加购释放计划异常') from exc
    if (run.allocated_before != before or run.released_amount != amount
            or [(part.allocation_id, part.amount) for part in run.releases.order_by('-allocation_id')]
            != [(part.allocation_id, part.amount) for part in planned]):
        raise ValidationError('主单退款后的加购释放金额异常')
    return ('RELEASE', f'order:{allocation.pk}', f'points-addon-release:{row.pk}', row.amount,
            _digest(['points-addon-after-main-release', row.pk, row.amount, allocation.pk,
                     allocation.receipt_id, run.pk, run.refund_id, run.points_addon_hash,
                     run.refunded_main_hash, run.required_amount, run.allocated_before,
                     run.released_amount, run.allocation_snapshot, main_fact]
                    + ([['payment', run.payment_id, event_hash]] if is_payment else [])
                    + ([['team-payment-history', team_payment_sources]] if team_payment_sources else [])
                    + ([['handover', run.handover_id, event_hash]] if is_handover else [])
                    + ([transfer_sources] if transferred else [])
                    + ([earlier_sources] if earlier_sources else [])))



def record_third_transfer_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('第三轮后转单释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_third_addon_receipt_usage import third_transfer_facts
    kind, key, request, amount, digest = third_transfer_facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)



def record_round_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('多轮退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_round_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_third_addon_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('第三轮后加购释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_third_addon_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_first_addon_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('加购退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    kind, key, request, amount, digest = _addon_release_facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_followup_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('后续退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.models import OrderPointsTransferFollowupFundingRevision, PointsRefundRoundEvidence
    if PointsRefundRoundEvidence.objects.filter(posting_id=row.posting_id).exists():
        from orders.points_third_refund_receipt_usage import facts
    elif OrderPointsTransferFollowupFundingRevision.objects.filter(posting_id=row.posting_id).exists():
        from orders.points_transfer_followup_receipt_usage import facts
    else:
        from orders.points_followup_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_transfer_refund_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('转单退款释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_transfer_refund_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_transfer_revision_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('转单补资释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_transfer_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_post_refund_transfer_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('退款后转单释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_post_refund_transfer_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_followup_transfer_release_locked(row):
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('二次退款后转单释放不属于当前门店')
    receipt = _locked_receipt(row.allocation.receipt_id)
    _require_current_unlocked_reads()
    from orders.points_followup_transfer_receipt_usage import facts
    kind, key, request, amount, digest = facts(row)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def _require_current_unlocked_reads():
    """Allocation writers serialize on the receipt; avoid allocation->receipt inversions."""
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        raise ValidationError('到账用途核对必须在门店事务内执行')
    if connection.vendor == 'sqlite':
        return  # Isolated test database; not evidence of MySQL concurrency.
    if connection.vendor != 'mysql':
        raise ValidationError('当前数据库不支持到账用途当前读')
    # @@transaction_isolation is only the session default: SET TRANSACTION
    # can override the active transaction without changing that variable.
    # The receipt lock has already started the transaction, so inspect its
    # actual instrumentation and fail closed if it is unavailable.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT ISOLATION_LEVEL FROM performance_schema.events_transactions_current "
                           "WHERE THREAD_ID = (SELECT THREAD_ID FROM performance_schema.threads "
                           "WHERE PROCESSLIST_ID = CONNECTION_ID()) AND STATE = 'ACTIVE'")
            row = cursor.fetchone()
    except DatabaseError as exc:
        raise ValidationError('无法核对到账用途事务隔离级别') from exc
    value = row[0] if row else None
    if str(value).upper().replace('_', '-').replace(' ', '-') != 'READ-COMMITTED':
        raise ValidationError('到账用途核对要求 MySQL READ COMMITTED 隔离级别')


def record_pass_locked(row):
    """Record a real pass allocation in the same tenant transaction."""
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('到账分配不属于当前门店')
    receipt = _locked_receipt(row.receipt_id)
    _require_current_unlocked_reads()
    actual = PassRefundRepaymentAllocation.objects.get(pk=row.pk, receipt=receipt)
    kind, key, request, amount, digest = pass_facts(actual)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def record_order_locked(row):
    """Record a real order allocation in the same tenant transaction."""
    from orders.models import OrderPointsFundingAllocation
    if row._state.db != transaction.get_connection().alias:
        raise ValidationError('到账分配不属于当前门店')
    receipt = _locked_receipt(row.receipt_id)
    _require_current_unlocked_reads()
    actual = OrderPointsFundingAllocation.objects.get(pk=row.pk, receipt=receipt)
    kind, key, request, amount, digest = order_facts(actual)
    return append_locked(receipt.pk, request_key=request, kind=kind, allocation_key=key,
                         amount=amount, source_digest=digest)


def verify_allocations_locked(receipt_id):
    """Verify the complete receipt chain and every source under one receipt lock."""
    receipt = _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    state = PassReceiptUsageState.objects.select_for_update().filter(receipt=receipt).first()
    if not state:
        raise ValidationError('到账用途账本尚未初始化')
    _validate_locked(receipt, state)
    from .receipt_usage_paged import verify_sources
    return verify_sources(receipt, state)
