"""Immutable source capture for later points-refund rounds.

This is not a refund authorizer: callers must hold the order and money locks,
post and verify the actual refund, release and receipt-use events in their
transaction before creating a version.  It intentionally does not open a new
refund path or infer missing historical funds.
"""
import hashlib
import json

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def _digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _facts(order, posting, *, current_release=None):
    from .models import (OrderPointsFundingRelease, OrderPointsFundingReleaseEvidence,
                         OrderPointsTransferFollowupFundingRevision, PointsNegotiationPosting,
                         PointsRefundRoundEvidence)
    from .points_service_refund import verify_refund_history_locked

    postings = list(PointsNegotiationPosting.objects.filter(
        points_refund__redemption__order=order, created_at__lte=posting.created_at)
        .select_related('points_refund__refund', 'proposal__case').order_by('created_at', 'pk'))
    if (len(postings) < 3 or postings[-1].pk != posting.pk
            or len({item.created_at for item in postings}) != len(postings)
            or any(item.proposal.case.order_id != order.pk for item in postings)):
        raise ValidationError('多轮退款实际顺序待核对')
    rounds = []
    for item in postings:
        proof = verify_refund_history_locked(item.points_refund.refund_id)
        if proof.pk != item.points_refund_id:
            raise ValidationError('多轮退款原付款来源不一致')
        shares = []
        for share in item.shares.select_related('assignment').order_by('pk'):
            deductions = list(share.segment_deductions.select_related('segment__attribution').order_by('segment_id'))
            parts = [[row.segment_id, row.amount] for row in deductions]
            terms = getattr(item.proposal, 'points_terms', None)
            addon_plan = terms.addon_pending_plan if terms else None
            addon_rows = list(share.addon_deductions.select_related('payment__intent', 'segment__attribution')
                              .order_by('position')) if addon_plan is not None else []
            if addon_plan is not None:
                from .points_third_refund_debit_facts import verify as verify_debits
                from django.core.exceptions import ValidationError as DjangoValidationError
                try:
                    verify_debits(order, item, share)
                except DjangoValidationError as exc:
                    raise ValidationError('多轮退款的加购份额承担待核对') from exc
            part_source = 'ROWS'
            if not parts and share.pending_deduction and not addon_rows:
                participants = item.participants
                from .points_negotiation_execution import participant_digest
                if item.participants_hash != participant_digest(item.proposal, participants):
                    raise ValidationError('历史退款履约快照摘要异常')
                origin = (participants.get('facts', {}).get('segments', []) if isinstance(participants, dict)
                          else participants if isinstance(participants, list) else [])
                selected = set(item.proposal.affected_segment_ids or [])
                ids = [(entry.get('id') if isinstance(participants, dict) else entry.get('segment_id'))
                       for entry in origin if isinstance(entry, dict) and entry.get('started_at')
                       and entry.get('account_id') == share.account_id and (not selected or
                           (entry.get('id') if isinstance(participants, dict) else entry.get('segment_id')) in selected)]
                if len(ids) != 1:
                    raise ValidationError('历史退款承担缺少唯一履约段')
                from .models import OrderParticipationSegment
                if not OrderParticipationSegment.objects.filter(pk=ids[0], attribution__order=order,
                        provider_account_id=share.account_id).exists():
                    raise ValidationError('历史退款履约段不属于本人')
                parts, part_source = [[ids[0], share.pending_deduction]], 'UNIQUE_PARTICIPANT'
            if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
                    or any(row.segment.attribution.order_id != order.pk
                           or row.segment.provider_account_id != share.account_id for row in deductions)
                    or sum(part[1] for part in parts) + sum(row.amount for row in addon_rows)
                       != share.pending_deduction):
                raise ValidationError('多轮退款的本人逐段承担待核对')
            entry = [share.pk, share.account_id, share.pending_deduction, share.wallet_deduction,
                           share.deposit_deduction, share.wallet_transaction_id, share.deposit_deficit_id,
                           part_source, parts]
            if addon_plan is not None:
                entry.append([[row.pk, row.payment_id, row.segment_id, row.addon_share_id,
                               row.income_before, row.amount] for row in addon_rows])
            shares.append(entry)
        release = OrderPointsFundingReleaseEvidence.objects.filter(posting=item).first()
        followup = OrderPointsTransferFollowupFundingRevision.objects.filter(posting=item).first()
        current = current_release if item.pk == posting.pk else None
        if item.pk != posting.pk:
            old = PointsRefundRoundEvidence.objects.filter(posting=item, order=order).first()
            if old:
                old_rounds = old.snapshot.get('rounds') if isinstance(old.snapshot, dict) else None
                if (old.evidence_hash != _digest(old.snapshot) or not isinstance(old_rounds, list)
                        or not old_rounds or old_rounds[-1].get('posting_id') != item.pk):
                    raise ValidationError('历史多轮退款版本异常')
                source = old_rounds[-1].get('release_source')
                if not isinstance(source, list) or len(source) != 2 or source[0] != 'current':
                    raise ValidationError('历史多轮退款释放来源异常')
                current = source[1]
        if sum((bool(release), bool(followup), current is not None)) != 1:
            raise ValidationError('多轮退款缺少唯一的实际补资释放版本')
        rows = list(OrderPointsFundingRelease.objects.filter(posting=item).select_related('allocation')
                    .order_by('allocation_id'))
        if (any(row.allocation.redemption_id != proof.redemption_id
                       and row.allocation.service_order_id != order.pk for row in rows)):
            raise ValidationError('多轮退款释放与本单到账不一致')
        if release:
            if sum(row.amount for row in rows) != release.expected_release:
                raise ValidationError('首轮退款释放金额不一致')
            release_source = ['first', release.pk, release.evidence_hash]
        elif followup:
            actual = sorted([[row.allocation_id, row.allocation.receipt_id, row.amount] for row in rows])
            if sorted(followup.releases) != actual:
                raise ValidationError('追加退款释放流水不一致')
            release_source = ['followup', followup.pk, followup.evidence_hash]
        else:
            # The current round has no earlier version: compare the actual
            # releases written by its posting transaction with that same
            # transaction's independently calculated allocation plan.
            basic_keys = {'allocations', 'releases', 'funding'}
            later_keys = basic_keys | {'prior_round_id', 'prior_round_hash', 'previous_addon_id',
                'previous_addon_hash', 'previous_transfer_id', 'previous_transfer_hash', 'prior_funding'}
            allowed = [basic_keys]
            allowed.extend(later_keys | optional for optional in (
                set(), {'pending_handovers'}, {'provider_recoveries'},
                {'pending_handovers', 'provider_recoveries'}, {'paid_snapshot'},
                {'pending_handovers', 'paid_snapshot'}, {'provider_recoveries', 'paid_snapshot'},
                {'pending_handovers', 'provider_recoveries', 'paid_snapshot'}))
            if not isinstance(current, dict) or set(current) not in allowed:
                raise ValidationError('当前退款释放资金计划缺失')
            allocations, planned, funding = (current['allocations'], current['releases'], current['funding'])
            if (not isinstance(allocations, list) or not isinstance(planned, list)
                    or not isinstance(funding, dict) or type(funding.get('release_units')) is not int):
                raise ValidationError('当前退款释放资金计划异常')
            from .points_funding_allocation import plan_release, PointsFundingAllocationError
            try:
                parts = plan_release(allocations=allocations, amount=funding['release_units'])
            except PointsFundingAllocationError as exc:
                raise ValidationError('当前退款释放资金计划异常') from exc
            from .models import OrderPointsFundingAllocation
            from django.db.models import Q
            owner = Q(redemption__order=order) | Q(service_order=order)
            actual_allocations = dict(OrderPointsFundingAllocation.objects.filter(owner,
                pk__in=[item[0] for item in allocations]).values_list('pk', 'amount'))
            if actual_allocations != {item[0]: item[1] for item in allocations}:
                raise ValidationError('当前退款计划中的原分配不属于本单')
            actual = sorted([[row.allocation_id, row.amount] for row in rows])
            expected = sorted([[row.allocation_id, row.amount] for row in parts])
            if planned != expected or actual != expected:
                raise ValidationError('当前退款实际释放与资金计划不一致')
            release_source = ['current', current]
        rounds.append({'posting_id': item.pk, 'proof_id': proof.pk, 'refund_id': proof.refund_id,
            'coin_transaction_id': proof.coin_transaction_id, 'points_ledger_id': proof.points_ledger_id,
            'assets': [proof.equivalent_units, proof.coin_units, proof.points_hundredths,
                       proof.prepaid_units, proof.gift_units, proof.compensation_units],
            'shares': shares, 'release_source': release_source,
            'releases': [[row.pk, row.allocation_id, row.amount] for row in rows]})
    return {'order_id': order.pk, 'rounds': rounds}


def record_locked(order, posting, *, transfer_revision, previous=None, current_release=None,
                  latest_transfer_version=None):
    """Bind already-created facts inside the caller's refund transaction."""
    from .models import PointsRefundRoundEvidence
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('多轮退款版本须在订单资金事务内写入')
    if (not transfer_revision or transfer_revision.order_id != order.pk
            or transfer_revision.created_at >= posting.created_at
            or transfer_revision.transfer.next_segment.attribution.order_id != order.pk):
        raise ValidationError('多轮退款的实际前序转单版本异常')
    if previous and (previous.order_id != order.pk or previous.posting_id == posting.pk):
        raise ValidationError('多轮退款前版不属于本单')
    snapshot = _facts(order, posting, current_release=current_release)
    snapshot['transfer_revision_id'] = transfer_revision.pk
    if len(snapshot['rounds']) > 3:
        from .models import PointsThirdAddonFundingRevision, PointsThirdTransferRevision
        if (not previous or not isinstance(current_release, dict)
                or current_release.get('prior_round_id') != previous.pk
                or current_release.get('prior_round_hash') != previous.evidence_hash):
            raise ValidationError('本轮退款缺少真实前一轮资金版本')
        prior_addon = PointsThirdAddonFundingRevision.objects.filter(order=order,
            created_at__lt=posting.created_at).order_by('-created_at', '-pk').first()
        prior_transfer = PointsThirdTransferRevision.objects.filter(order=order,
            created_at__lt=posting.created_at).order_by('-created_at', '-pk').first()
        if ((prior_addon.pk if prior_addon else None) != current_release.get('previous_addon_id')
                or (prior_addon.evidence_hash if prior_addon else None) != current_release.get('previous_addon_hash')
                or (prior_transfer.pk if prior_transfer else None) != current_release.get('previous_transfer_id')
                or (prior_transfer.evidence_hash if prior_transfer else None) != current_release.get('previous_transfer_hash')
                or (latest_transfer_version.pk if latest_transfer_version else None) != (prior_transfer.pk if prior_transfer else None)):
            raise ValidationError('本轮退款之前的资金事件前版异常')
        snapshot['latest_transfer_version_id'] = prior_transfer.pk if prior_transfer else None
        snapshot['latest_transfer_version_hash'] = prior_transfer.evidence_hash if prior_transfer else None
    if previous and (previous.evidence_hash != _digest(previous.snapshot)
                     or snapshot['rounds'][:-1] != previous.snapshot.get('rounds')):
        raise ValidationError('多轮退款前版与真实事件不一致')
    if len(snapshot['rounds']) > 3:
        # Each version from the third posting onwards must preserve the exact
        # earlier prefix and link to the immediately preceding persisted
        # version.  Do not accept an independently reconstructed later row as
        # authority for historical releases, even when its hash is valid.
        prefix = snapshot['rounds'][:-1]
        ids = [round_['posting_id'] for round_ in prefix[2:]]
        versions = list(PointsRefundRoundEvidence.objects.select_for_update().filter(
            order=order, posting_id__in=ids).order_by('posting__created_at', 'posting_id'))
        if not previous or len(versions) != len(ids) or [row.posting_id for row in versions] != ids:
            raise ValidationError('多轮退款前序版本不完整')
        prior_id = None
        for index, version in enumerate(versions, start=3):
            if (version.previous_id != prior_id or version.evidence_hash != _digest(version.snapshot)
                    or not isinstance(version.snapshot, dict)
                    or version.snapshot.get('order_id') != order.pk
                    or version.snapshot.get('transfer_revision_id') != version.transfer_revision_id
                    or version.snapshot.get('rounds') != prefix[:index]):
                raise ValidationError('多轮退款历史版本链或释放事实不一致')
            prior_id = version.pk
        if previous.pk != prior_id:
            raise ValidationError('多轮退款前序版本不是上一轮')
    digest = _digest(snapshot)
    existing = PointsRefundRoundEvidence.objects.filter(posting=posting).first()
    if existing:
        if (existing.order_id != order.pk or existing.transfer_revision_id != transfer_revision.pk
                or existing.latest_transfer_version_id != getattr(latest_transfer_version, 'pk', None)
                or existing.previous_id != getattr(previous, 'pk', None)
                or existing.snapshot != snapshot or existing.evidence_hash != digest):
            raise ValidationError('多轮退款版本与原请求不一致')
        return existing
    return PointsRefundRoundEvidence.objects.create(order=order, posting=posting, transfer_revision=transfer_revision,
        latest_transfer_version=latest_transfer_version, previous=previous, snapshot=snapshot, evidence_hash=digest)
