"""Funding evidence for an add-on change after a points-service refund."""
import hashlib
import json
from dataclasses import asdict

from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from .models import OrderSegmentFinancialSnapshot
from .points_funding_plan import PointsFundingPlanError


def capture(order, posting, net_allocations, at, *, read_receipt_id=None):
    from .models import OrderOriginalShareTransfer
    transfers = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
    if transfers.filter(created_at=at).exists():
        raise ValidationError('加购资金事件与转单时序无法区分')
    if transfers.filter(created_at__lt=at).exists():
        return _capture_transferred(order, posting, net_allocations, at)
    from .points_funding_release import _compute
    from .addon_funding_history import combined_at
    from .refunded_addon_funding import combine_refunded_history
    if transfers.exists():
        from .points_pretransfer_refund_source import verify_participants_locked
        rows = verify_participants_locked(posting, receipt_id=read_receipt_id)
        by_id = {item.pk: item for item in OrderSegmentFinancialSnapshot.objects.filter(
            pk__in=[row['snapshot_id'] for row in rows]).select_related('segment')}
        if len(by_id) != len(rows):
            raise ValidationError('转单前加购的主单收益快照缺失')
        snapshots = [by_id[row['snapshot_id']] for row in rows]
    else:
        from .points_service_participation import service_snapshots
        snapshots = service_snapshots(order)
    income = sum(row.income_at_entry for row in snapshots)
    main, _ = _compute(posting, net_allocations, income, include_addons=False,
                       read_receipt_id=read_receipt_id)
    pending = {row.account_id: row.pending_deduction for row in posting.shares.all()}
    retained = {row.segment.provider_account_id: row.income_at_entry - pending.get(row.segment.provider_account_id, 0)
                for row in snapshots}
    shares = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis, 0,
               retained[row.segment.provider_account_id]) for row in snapshots]
    try:
        history = combined_at(order, main, at, segment_shares=shares)
        plan = combine_refunded_history(main, history, main_retained_by_account=retained)
    except PointsFundingPlanError as exc:
        raise ValidationError(f'主单退款后的加购资金待核对：{exc}') from exc
    return {'posting_id': posting.pk, 'captured_at': at.isoformat(), 'addon_history': history['addon_history'],
            'main_deductions': history['main_deductions'], 'funding': asdict(plan)}


def _capture_transferred(order, posting, net_allocations, at, *, receipt_local=False, witness_allocation=None):
    """Rebuild the pre-existing transfer version at the add-on refund boundary."""
    from .models import PointsPostRefundTransferRevision, OrderOriginalShareTransfer
    from .points_post_refund_transfer import verify_locked as verify_version
    from .points_transfer_refund_evidence import _reconstruct_from_verified_facts
    from .points_negotiation_preview import funding_after_refund
    from .points_transfer_followup_scope import combine_addon
    version = PointsPostRefundTransferRevision.objects.filter(order=order, created_at__lt=at).order_by('-pk').first()
    at_refunds = list(order.refund_records.filter(created_at__lte=at).order_by('created_at', 'pk'))
    if (len(at_refunds) != 1 or at_refunds[0].pk != posting.points_refund.refund_id
            or (version and version.snapshot.get('posting_id') != posting.pk)):
        raise ValidationError('加购退款缺少可核验的退款后转单版本')
    versions = list(PointsPostRefundTransferRevision.objects.filter(order=order, created_at__lt=at).order_by('pk'))
    if any(item.prior_id != (versions[index - 1].pk if index else None)
           or (index and item.created_at <= versions[index - 1].created_at)
           or not isinstance(item.snapshot, dict) or item.snapshot.get('posting_id') != posting.pk
           for index, item in enumerate(versions)):
        raise ValidationError('退款后转单资金版本链异常')
    if version:
        facts, through = version.snapshot['facts'], version.transfer_id
    else:
        facts = posting.participants.get('facts') if isinstance(posting.participants, dict) else None
        transfers = facts.get('transfers') if isinstance(facts, dict) else None
        if (not transfers or OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order, created_at__gte=posting.created_at,
                created_at__lte=at).exists()):
            raise ValidationError('主单退款后的转单历史待核对')
        through = transfers[-1]['id']
    if receipt_local:
        from types import SimpleNamespace
        from django.core.exceptions import ValidationError as DjangoValidationError
        from users.receipt_usage_sources import _require_current_unlocked_reads
        from .models import OrderPointsFundingAllocation
        from .points_transfer_receipt_usage import historical_plan
        _require_current_unlocked_reads()
        evidence = getattr(posting, 'funding_release_evidence', None)
        allocation_ids = ([item[0] for item in version.snapshot.get('allocations', [])] if version else
                          [item[0] for item in evidence.allocations] if evidence else [])
        original = (OrderPointsFundingAllocation.objects.filter(pk__in=allocation_ids).first()
                    if allocation_ids else witness_allocation)
        if not original:
            raise ValidationError('退款后转单原到账凭证缺失')
        try:
            witness = witness_allocation.receipt_id if witness_allocation else original.receipt_id
            if version:
                from .points_post_refund_transfer_receipt_usage import facts as transfer_facts
                for item in versions:
                    item_ids = [row[0] for row in item.snapshot.get('allocations', [])]
                    item_original = (OrderPointsFundingAllocation.objects.filter(pk__in=item_ids).order_by('pk').first()
                                     if item_ids else witness_allocation or original)
                    if not item_original:
                        raise ValidationError('退款后转单历史到账见证缺失')
                    transfer_facts(SimpleNamespace(revision=item, allocation=item_original, pk=0, amount=0,
                                                   read_receipt_id=witness))
            else:
                from .points_transfer_refund_receipt_usage import facts as transfer_refund_facts
                transfer_refund_facts(SimpleNamespace(posting=posting, allocation=original, pk=0, amount=0,
                                                      read_receipt_id=witness, history_before=at))
        except DjangoValidationError as exc:
            raise ValidationError('退款后转单到账来源待核对') from exc
        sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
        paid = {kind: sum(item.amount for item in sources if item.kind == kind)
                for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        original_plan, lines = historical_plan(facts, posting.points_refund.redemption, paid, through)
        history = SimpleNamespace(funding=original_plan, segment_shares=lines)
    else:
        if version:
            for item in versions:
                verify_version(item)
        else:
            if order.refund_records.count() == 1:
                from .points_transfer_refund_continuation import verify_locked as verify_posting
                from .points_transfer_refund_release import verify_release_locked
                verify_posting(order)
                verify_release_locked(posting.funding_release_evidence)
            else:
                from .points_negotiation_termination import _verify_transfer_termination_facts_locked
                from .points_transfer_refund_release import _verify_release_facts_locked
                _verify_transfer_termination_facts_locked(posting, allow_verified_later_final=True)
                _verify_release_facts_locked(posting.funding_release_evidence)
        history = _reconstruct_from_verified_facts(order.pk, facts, refund_id=posting.points_refund.refund_id,
                                                   through_transfer_id=through)
    original, proof = history.funding, posting.points_refund
    shares = list(posting.shares.all())
    main = funding_after_refund(principal_units=original.principal_units, gift_units=original.gift_units,
        compensation_units=original.compensation_units, returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=original.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=facts['inviter_commission_rate'] if facts['inviter_id'] or facts['inviter_account_id'] else 0,
        allocated_units=sum(item[1] for item in net_allocations),
        rounding_reserved_units=posting.rounding_to_store_units)
    deductions = [(item.account_id, item.pending_deduction,
                   set(posting.proposal.affected_segment_ids or [])) for item in shares]
    plan, addon_history = combine_addon(order, main, at, history.segment_shares, facts['segments'], deductions)
    from .addon_main_segment_deduction import verified_deductions
    segment_deductions = verified_deductions(order, before=at)
    result = {'posting_id': posting.pk, 'captured_at': at.isoformat(), 'addon_history': addon_history,
            'main_deductions': [list(item) for item in sorted(
                (account, amount) for account, amount in _account_deductions(facts, segment_deductions).items())],
            'funding': asdict(plan), 'transfer_revision_id': version.pk if version else None,
            'main_segment_deductions': [[key, value] for key, value in sorted(segment_deductions.items())]}
    if not version:
        result['transfer_origin_posting_id'] = posting.pk
    from .models import PointsZeroFollowupTermination
    zero = PointsZeroFollowupTermination.objects.filter(order=order, terminated_at__isnull=False).first()
    if (zero and zero.terminated_at == at and isinstance(zero.funding_snapshot, dict)
            and zero.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1'):
        raise ValidationError('加购资金事件与零退款终止时间无法区分')
    if (zero and zero.terminated_at < at and isinstance(zero.funding_snapshot, dict)
            and zero.funding_snapshot.get('kind') == 'TRANSFER_ZERO_V1'):
        from .points_transfer_zero_followup import verify_event_current_read
        result['zero_termination_id'] = verify_event_current_read(zero, before=at)
    return result


def _account_deductions(facts, deductions):
    from collections import defaultdict
    accounts = {item['id']: item['account_id'] for item in facts['segments']}
    totals = defaultdict(int)
    for segment_id, amount in deductions.items():
        if segment_id not in accounts:
            raise ValidationError('加购退款主单段不属于转单版本')
        totals[accounts[segment_id]] += amount
    return totals


def digest(run, snapshot):
    payload = [run.refund_id, run.handover_id, run.payment_id, run.required_amount, run.allocated_before,
               run.released_amount, run.allocation_snapshot, snapshot]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(run, net_allocations, *, receipt_local=False, witness_allocation=None):
    snapshot = run.refunded_main_snapshot
    if not isinstance(snapshot, dict) or run.refunded_main_hash != digest(run, snapshot):
        raise ValidationError('主单退款后的加购资金版本异常')
    from .models import PointsNegotiationPosting
    posting = PointsNegotiationPosting.objects.filter(pk=snapshot.get('posting_id')).select_related(
        'proposal__case__order', 'points_refund__redemption').first()
    at = parse_datetime(snapshot.get('captured_at', ''))
    if (not posting or not at or at < posting.created_at or at > run.created_at
            or (run.refund_id and at < run.refund.created_at)
            or (run.payment_id and at < run.payment.buyer_transaction.created_at)
            or (run.handover_id and (not run.handover.accepted_at or at <= run.handover.accepted_at))):
        raise ValidationError('主单退款后的加购资金版本时序异常')
    if receipt_local and (snapshot.get('transfer_revision_id') or snapshot.get('transfer_origin_posting_id')):
        actual = _capture_transferred(posting.proposal.case.order, posting, net_allocations, at,
                                      receipt_local=True, witness_allocation=witness_allocation)
    else:
        actual = capture(posting.proposal.case.order, posting, net_allocations, at,
                         read_receipt_id=witness_allocation.receipt_id if receipt_local and witness_allocation else None)
    if actual != snapshot or snapshot['funding']['required_units'] != run.required_amount:
        raise ValidationError('主单退款后的加购资金版本不一致')
    return snapshot
