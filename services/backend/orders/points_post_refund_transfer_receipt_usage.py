"""Receipt-local reconstruction for a transfer after one points refund."""
from dataclasses import asdict
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                     PointsNegotiationPosting, PointsPostRefundTransferRelease, PointsPostRefundTransferRevision)


def facts(row):
    from users.receipt_usage_sources import _digest
    from .points_post_refund_transfer import _digest as version_digest
    from .points_transfer_refund_evidence import current_read_matches_history
    from .points_transfer_refund_receipt_usage import facts as refund_facts
    from .points_transfer_receipt_usage import facts as transfer_facts, historical_plan
    from .points_negotiation_preview import funding_after_refund
    from .points_funding_allocation import plan_release
    from engagement.funding_wallet import validate_order_funding_sources
    from rest_framework.exceptions import ValidationError as RestValidationError
    revision, allocation = row.revision, row.allocation
    historical_witness = row.pk == 0 and row.amount == 0
    order, snapshot = revision.order, revision.snapshot
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('facts'), dict)
            or not isinstance(snapshot.get('allocations'), list) or not isinstance(snapshot.get('releases'), list)
            or revision.evidence_hash != version_digest(order.pk, revision.transfer_id, revision.prior_id, snapshot)
            or revision.transfer.previous_segment.attribution.order_id != order.pk
            or allocation.funding_order_id != order.pk):
        raise ValidationError('退款后积分转单到账版本异常')
    prior_id = (PointsPostRefundTransferRevision.objects.filter(order_id=order.pk, pk__lt=revision.pk)
                .order_by('-pk').values_list('pk', flat=True).first())
    if revision.prior_id != prior_id:
        raise ValidationError('退款后积分转单到账版本前驱异常')
    posting = PointsNegotiationPosting.objects.filter(pk=snapshot.get('posting_id'),
        points_refund__refund_id=snapshot.get('refund_id'), points_refund__redemption__order=order).first()
    evidence = getattr(posting, 'funding_release_evidence', None) if posting else None
    if (not posting or not evidence or not isinstance(evidence.allocations, list) or not isinstance(posting.participants, (dict, list))
            or posting.created_at >= revision.transfer.created_at):
        raise ValidationError('退款后积分转单前驱凭证缺失')
    original = (OrderPointsFundingAllocation.objects.filter(pk=evidence.allocations[0][0]).first()
                if evidence.allocations else allocation)
    if not original:
        raise ValidationError('退款后积分转单原到账分配缺失')
    read_receipt_id = getattr(row, 'read_receipt_id', allocation.receipt_id)
    if isinstance(posting.participants, list):
        from users.receipt_usage_sources import first_negotiation_release_facts
        from .points_first_transfer_addon_history import verify as verify_addon_history
        first_version = (PointsPostRefundTransferRevision.objects.filter(order=order, pk__lte=revision.pk)
                         .select_related('transfer').order_by('pk').first())
        if not first_version:
            raise ValidationError('首次转单资金版本缺失')
        try:
            verify_addon_history(order, posting, receipt_id=read_receipt_id,
                                 before=first_version.transfer.created_at)
        except RestValidationError as exc:
            raise ValidationError('首次转单前加购退款历史异常') from exc
        previous_refund_fact = first_negotiation_release_facts(SimpleNamespace(
            posting=posting, allocation=original, pk=0, amount=0, read_receipt_id=read_receipt_id))
    else:
        previous_refund_fact = refund_facts(SimpleNamespace(posting=posting, allocation=original, pk=0, amount=0,
                                                            read_receipt_id=read_receipt_id,
                                                            history_before=revision.transfer.created_at))
    frozen = snapshot['facts']
    if not current_read_matches_history(order, read_receipt_id, frozen, revision.created_at):
        raise ValidationError('退款后积分转单履约事实不一致')
    if revision.transfer_id not in {item.get('id') for item in frozen.get('transfers', [])}:
        raise ValidationError('退款后积分转单事件未绑定')
    from .models import OrderPrestartHandoff
    from .points_transfer_refund_access import _prestart_slot_counts
    prestart = OrderPrestartHandoff.objects.filter(order=order, created_at__lt=revision.transfer.created_at)\
        .order_by('-pk').first()
    counts = _prestart_slot_counts(order, prestart) if prestart else {}
    segments = {item['id']: item for item in frozen['segments']}
    for event in frozen['transfers']:
        source = segments.get(event['previous_segment_id'])
        if not source:
            raise ValidationError('退款后转单席位来源缺失')
        counts[source['slot']] = counts.get(source['slot'], 0) + 1
    if any(value > 2 for value in counts.values()):
        raise ValidationError('同一席位改派和转单次数超过上限')
    closed = set(posting.proposal.affected_segment_ids or [])
    if not closed:
        if isinstance(posting.participants, dict):
            closed = {item['id'] for item in posting.participants['facts']['segments']
                      if item['started_at'] and item['account_id'] in posting.proposal.affected_accounts}
        else:
            closed = {item['segment_id'] for item in posting.participants
                      if item['started_at'] and item['account_id'] in posting.proposal.affected_accounts}
    if sorted(closed) != snapshot.get('closed_segments') or revision.transfer.previous_segment_id in closed:
        raise ValidationError('退款后转单触及已确认履约段')
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('退款后转单原付款来源异常') from exc
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    original_plan, lines = historical_plan(frozen, posting.points_refund.redemption, paid, revision.transfer_id)
    income = {line[0]: line[4] for line in lines}
    selected = posting.proposal.affected_segment_ids
    shares = list(posting.shares.order_by('account_id'))
    for share in shares:
        participants = (posting.participants['facts']['segments'] if isinstance(posting.participants, dict)
                        else posting.participants)
        ids = [(item['id'] if isinstance(posting.participants, dict) else item['segment_id'])
               for item in participants if item['account_id'] == share.account_id and item['started_at']
               and (not selected or (item['id'] if isinstance(posting.participants, dict)
                                     else item['segment_id']) in selected)]
        if len(ids) != 1 or ids[0] not in income or share.pending_deduction > income[ids[0]]:
            raise ValidationError('退款后转单收益无法归属原段')
        income[ids[0]] -= share.pending_deduction
    if [[key, value] for key, value in sorted(income.items())] != snapshot.get('segment_income'):
        raise ValidationError('退款后转单逐段净收益异常')
    from django.db.models import Q
    owner_rows = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order),
        created_at__lte=revision.transfer.created_at).order_by('pk'))
    rebuilt = []
    from .models import OrderAddonFundingRelease, OrderAddonPayment
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=revision.transfer.created_at))
    boundary = revision.transfer.created_at
    if (OrderAddonPayment.objects.filter(intent__order=order, buyer_transaction__created_at=boundary).exists()
            or any(item.refund_records.filter(created_at=boundary).exists() for item in addons)):
        raise ValidationError('退款后转单加购历史暂不支持')
    addon_payload = None
    pending_handover = []
    if addons:
        from .points_addon_receipt_usage import payment_facts, facts as addon_refund_facts, _prior_addon_events
        from .addon_handover import verify_handover
        from .points_post_refund_transfer import _addon_responsibility_at
        for addon in addons:
            payment_facts(addon, read_receipt_id=read_receipt_id)
            for item in addon.refund_records.filter(created_at__lt=boundary).order_by('created_at', 'pk'):
                addon_refund_facts(item, read_receipt_id=read_receipt_id, _event_history=False)
            for handover in addon.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary):
                verify_handover(addon, handover, as_of=boundary, receipt_id=read_receipt_id)
            responsible, remaining = _addon_responsibility_at(addon, boundary)
            if responsible == revision.transfer.previous_segment_id:
                pending_handover.append([addon.pk, responsible, remaining])
        if len(addons) > 1 or any(item.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary).exists() and item.refund_records.filter(
                created_at__lt=boundary).exists() for item in addons):
            addon_payload = _prior_addon_events(order, boundary, read_receipt_id=read_receipt_id)
        else:
            addon_payload = payment_facts(addons[0], read_receipt_id=read_receipt_id)
    if sorted(pending_handover) != snapshot.get('pending_handover', []):
        raise ValidationError('退款后转单待接替加购负债不一致')
    for item in owner_rows:
        main_rows = list(OrderPointsFundingRelease.objects.filter(allocation=item,
            posting__created_at__lt=revision.transfer.created_at).select_related('posting').order_by('pk'))
        transfer_rows = list(OrderPointsTransferFundingRelease.objects.filter(allocation=item,
            revision__created_at__lt=revision.transfer.created_at).select_related('revision__order').order_by('pk'))
        later_rows = list(PointsPostRefundTransferRelease.objects.filter(allocation=item,
            revision_id__lt=revision.pk).select_related('revision__order', 'revision__transfer').order_by('pk'))
        addon_rows = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=revision.transfer.created_at).select_related('run', 'allocation__redemption').order_by('pk'))
        for part in main_rows:
            source = SimpleNamespace(posting=part.posting, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=read_receipt_id,
                history_before=revision.transfer.created_at)
            if isinstance(part.posting.participants, list):
                from users.receipt_usage_sources import first_negotiation_release_facts
                first_negotiation_release_facts(source)
            else:
                refund_facts(source)
        for part in transfer_rows:
            transfer_facts(SimpleNamespace(revision=part.revision, allocation=part.allocation,
                pk=part.pk, amount=part.amount, read_receipt_id=read_receipt_id))
        for part in later_rows:
            facts(part)
        from users.receipt_usage_sources import _addon_release_facts
        for part in addon_rows:
            if part.run.prestart_handoff_id:
                if part.run.prestart_handoff.order_id != order.pk:
                    raise ValidationError('改派释放不属于该订单')
            elif not addons or not (part.run.payment_id in {addon.pk for addon in addons}
                    or (part.run.handover_id and part.run.handover.payment_id in {addon.pk for addon in addons}
                        and part.run.handover.accepted_at < revision.transfer.created_at)
                    or (part.run.refund_id and part.run.refund.payment_id in {addon.pk for addon in addons}
                        and part.run.refund.created_at < boundary)):
                raise ValidationError('退款后转单加购释放来源异常')
            _addon_release_facts(part, witness_receipt_id=read_receipt_id)
        used = sum(part.amount for part in main_rows + transfer_rows + later_rows + addon_rows)
        if used > item.amount:
            raise ValidationError('退款后转单累计释放超过到账分配')
        rebuilt.append([item.pk, item.receipt_id, item.amount, used])
    if (rebuilt != snapshot['allocations'] or (not any(item[0] == allocation.pk for item in rebuilt)
            and not (historical_witness and not rebuilt and allocation.created_at > revision.transfer.created_at))):
        raise ValidationError('退款后转单到账分配与历史不一致')
    proof = posting.points_refund
    funding = funding_after_refund(principal_units=original_plan.principal_units, gift_units=original_plan.gift_units,
        compensation_units=original_plan.compensation_units, returned_prepaid_units=proof.prepaid_units,
        returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
        provider_income_units=original_plan.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=frozen['inviter_commission_rate'] if frozen['inviter_id'] or frozen['inviter_account_id'] else 0,
        allocated_units=sum(item[2] - item[3] for item in rebuilt),
        rounding_reserved_units=posting.rounding_to_store_units)
    if addons:
        from collections import defaultdict
        from .addon_funding_history import combined_at
        from .refunded_addon_funding import combine_refunded_history
        retained = defaultdict(int)
        for line in lines:
            retained[line[1]] += income[line[0]]
        combined = combined_at(order, funding, revision.transfer.created_at, segment_shares=[
            (line[0], line[1], line[2], line[3], income[line[0]]) for line in lines])
        if combined['addon_history'] != snapshot.get('addon_history'):
            raise ValidationError('退款后转单加购历史快照异常')
        funding = combine_refunded_history(funding, combined, main_retained_by_account=retained)
    elif snapshot.get('addon_history') is not None:
        raise ValidationError('退款后转单加购快照多余')
    if asdict(funding) != snapshot.get('funding') or (not addons and sum(income.values()) != funding.provider_income_units):
        raise ValidationError('退款后转单资金不守恒')
    parts = plan_release(allocations=[(item[0], item[2], item[3]) for item in rebuilt], amount=funding.release_units)
    actual = list(revision.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    if (snapshot['releases'] != [[part.allocation_id, part.amount] for part in parts]
            or actual != [(part.allocation_id, part.amount) for part in parts]):
        raise ValidationError('退款后转单释放记录异常')
    payload = ['points-post-refund-transfer', row.pk, row.amount, allocation.pk, allocation.receipt_id,
               revision.pk, revision.evidence_hash, previous_refund_fact]
    if len(addons) > 1 or any(item.handovers.filter(status='ACCEPTED', accepted_at__lt=boundary).exists() and item.refund_records.filter(
            created_at__lt=boundary).exists() for item in addons):
        payload.append(addon_payload)
    return ('RELEASE', f'order:{allocation.pk}', f'points-post-refund-transfer:{row.pk}', row.amount,
            _digest(payload))
