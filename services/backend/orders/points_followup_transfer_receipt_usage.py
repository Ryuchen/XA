"""Receipt-local replay of a transfer after two actual points refunds."""
from dataclasses import asdict
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.exceptions import ValidationError as RestValidationError


def facts(row):
    from users.receipt_usage_sources import _digest, _require_current_unlocked_reads
    from users.receipt_usage_ledger import _locked_receipt
    from .models import (OrderAddonPayment, OrderAddonFundingRelease, OrderPointsFundingAllocation,
        OrderPointsFundingRelease, OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease)
    from .points_followup_transfer_revision import _digest as version_digest
    from .points_transfer_refund_evidence import current_read_matches_history
    from .points_transfer_receipt_usage import facts as transfer_facts, historical_plan
    from .points_transfer_followup_receipt_usage import facts as followup_facts
    from .points_post_refund_transfer_receipt_usage import facts as post_facts
    from .points_negotiation_preview import funding_after_refund
    from .points_funding_allocation import plan_release
    from engagement.funding_wallet import validate_order_funding_sources
    revision, allocation = row.revision, row.allocation
    order, snapshot = revision.order, revision.snapshot
    receipt_id = getattr(row, 'read_receipt_id', allocation.receipt_id)
    _locked_receipt(receipt_id)
    _require_current_unlocked_reads()
    first, second, prior = revision.first_posting, revision.second_posting, revision.followup_revision
    if (not isinstance(snapshot, dict) or snapshot.get('kind') != 'POINTS_FOLLOWUP_TRANSFER_V1'
            or revision.evidence_hash != version_digest(order.pk, revision.transfer_id, first.pk, second.pk, prior.pk, snapshot)
            or allocation.funding_order_id != order.pk or prior.first_posting_id != first.pk
            or prior.posting_id != second.pk or second.created_at >= revision.transfer.created_at
            or not first.termination_processed_at or not second.termination_processed_at
            or first.termination_processed_at >= revision.transfer.created_at
            or second.termination_processed_at >= revision.transfer.created_at
            or not second.termination_in_service or second.completed_by_negotiation
            or revision.transfer.previous_segment.attribution.order_id != order.pk):
        raise ValidationError('二次退款后转单到账版本异常')
    frozen = snapshot.get('facts')
    if not isinstance(frozen, dict) or not current_read_matches_history(order, receipt_id, frozen, revision.created_at):
        raise ValidationError('二次退款后转单历史履约异常')
    original = (OrderPointsFundingAllocation.objects.filter(pk=prior.allocations[0][0]).first()
                if prior.allocations else allocation)
    if not original:
        raise ValidationError('二次退款历史到账见证缺失')
    source = followup_facts(SimpleNamespace(posting=second, allocation=original, pk=0, amount=0,
                                             read_receipt_id=receipt_id, history_before=revision.transfer.created_at))
    from .models import OrderAddonFundingReleaseRun
    latest = OrderAddonFundingReleaseRun.objects.filter(
        points_followup_snapshot__kind='TRANSFER_FOLLOWUP_V1',
        points_followup_snapshot__second_posting_id=second.pk,
        created_at__lt=revision.transfer.created_at).order_by('-pk').first()
    previous_funding = prior.funding
    if latest:
        from .points_transfer_followup_addon_version import _verify_prior_run
        try:
            _verify_prior_run(latest, receipt_id=receipt_id)
        except RestValidationError as exc:
            raise ValidationError('转单前加购事件资金版本异常') from exc
        previous_funding = latest.points_followup_snapshot.get('funding')
    if snapshot.get('previous_funding') != previous_funding:
        raise ValidationError('转单前退款及加购资金版本不一致')
    sources = list(order.funding_allocations.select_related('lot').order_by('pk'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('二次退款后转单付款来源异常') from exc
    paid = {kind: sum(item.amount for item in sources if item.kind == kind)
            for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
    original_funding, lines = historical_plan(frozen, first.points_refund.redemption, paid, revision.transfer_id)
    segments = {item['id']: item for item in frozen['segments']}
    transfers = frozen.get('transfers', [])
    if not transfers or transfers[-1].get('id') != revision.transfer_id:
        raise ValidationError('二次退款后转单履约事件异常')
    from .models import OrderPrestartHandoff
    from .points_transfer_refund_access import _prestart_slot_counts
    prestart = OrderPrestartHandoff.objects.filter(order=order,
        created_at__lt=revision.transfer.created_at).order_by('-pk').first()
    counts = _prestart_slot_counts(order, prestart) if prestart else {}
    for event in transfers:
        segment = segments.get(event.get('previous_segment_id'))
        if not segment:
            raise ValidationError('转单履约席位缺失')
        counts[segment['slot']] = counts.get(segment['slot'], 0) + 1
    if any(value > 2 for value in counts.values()):
        raise ValidationError('同一席位改派和转单超过上限')
    incomes = {line[0]: line[4] for line in lines}
    deductions = []
    shares = []
    for posting in (first, second):
        participants = posting.participants
        origin = (participants.get('facts', {}).get('segments', []) if isinstance(participants, dict)
                  else participants if isinstance(participants, list) else [])
        selected = set(posting.proposal.affected_segment_ids or [])
        closed = selected or {(item.get('id') if isinstance(participants, dict) else item.get('segment_id'))
            for item in origin if item.get('started_at')
            and item.get('account_id') in posting.proposal.affected_accounts}
        if revision.transfer.previous_segment_id in closed:
            raise ValidationError('已确认终止的履约段不能再次转单')
        for share in posting.shares.order_by('pk'):
            shares.append(share)
            parts = list(share.segment_deductions.order_by('segment_id').values_list('segment_id', 'amount'))
            if not parts and share.pending_deduction:
                ids = [(item.get('id') if isinstance(participants, dict) else item.get('segment_id'))
                    for item in origin if item.get('account_id') == share.account_id and item.get('started_at')
                    and (not selected or (item.get('id') if isinstance(participants, dict)
                                          else item.get('segment_id')) in selected)]
                if len(ids) != 1:
                    raise ValidationError('退款承担的历史履约段不唯一')
                parts = [(ids[0], share.pending_deduction)]
            if sum(amount for _, amount in parts) != share.pending_deduction:
                raise ValidationError('退款承担的历史逐段合计异常')
            for segment_id, amount in parts:
                if (segment_id not in incomes or segments[segment_id]['account_id'] != share.account_id
                        or amount > incomes[segment_id]):
                    raise ValidationError('退款承担超过历史履约段收益')
                incomes[segment_id] -= amount
                deductions.append([posting.pk, share.pk, segment_id, amount])
    if (deductions != snapshot.get('deductions')
            or [list(item) for item in sorted(incomes.items())] != snapshot.get('segment_income')):
        raise ValidationError('二次退款后转单逐段承担异常')
    addons = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=revision.transfer.created_at).select_related('intent', 'buyer_transaction'))
    if OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at=revision.transfer.created_at).exists():
        raise ValidationError('加购付款与转单时点无法区分')
    pending_handover = []
    if addons:
        from .models import OrderAddonHandover
        from .points_addon_receipt_usage import payment_facts, facts as addon_refund_facts
        from .addon_handover import current_responsibilities, verify_handover
        for payment in addons:
            if OrderAddonHandover.objects.filter(payment=payment, created_at__lt=revision.transfer.created_at,
                    status='PENDING').exists() or OrderAddonHandover.objects.filter(payment=payment,
                    created_at__lt=revision.transfer.created_at, accepted_at__gte=revision.transfer.created_at).exists():
                raise ValidationError('转单时加购接替尚待确认')
            payment_facts(payment, read_receipt_id=receipt_id)
            for refund in payment.refund_records.filter(created_at__lt=revision.transfer.created_at).order_by('created_at', 'pk'):
                addon_refund_facts(refund, read_receipt_id=receipt_id, _event_history=False)
            for handover in payment.handovers.filter(status='ACCEPTED', accepted_at__lt=revision.transfer.created_at):
                verify_handover(payment, handover, as_of=revision.transfer.created_at, receipt_id=receipt_id)
            _, responsibilities = current_responsibilities(payment, before=revision.transfer.created_at)
            active = [item for item in responsibilities if item['is_current'] and item['basis'] > 0]
            if len(active) > 1:
                raise ValidationError('转出加购当前责任不唯一')
            if active and active[0]['segment_id'] == revision.transfer.previous_segment_id:
                pending_handover.append([payment.pk, active[0]['segment_id'], active[0]['basis']])
        if sorted(pending_handover) != snapshot.get('pending_handover'):
            raise ValidationError('转单待接替责任不一致')
    elif snapshot.get('addon_history') is not None or snapshot.get('pending_handover'):
        raise ValidationError('无付款时存在虚构加购责任')
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order),
        created_at__lte=revision.transfer.created_at).order_by('pk'))
    rows, prior_sources = [], []
    for item in allocations:
        main = list(OrderPointsFundingRelease.objects.filter(allocation=item,
            created_at__lt=revision.transfer.created_at).select_related('posting').order_by('pk'))
        transfers = list(OrderPointsTransferFundingRelease.objects.filter(allocation=item,
            created_at__lt=revision.transfer.created_at).select_related('revision__order').order_by('pk'))
        posts = list(PointsPostRefundTransferRelease.objects.filter(allocation=item,
            revision__created_at__lt=revision.transfer.created_at).select_related('revision__order', 'revision__transfer').order_by('pk'))
        addon_rows = list(OrderAddonFundingRelease.objects.filter(allocation=item,
            run__created_at__lt=revision.transfer.created_at).select_related('run', 'allocation').order_by('pk'))
        from users.receipt_usage_sources import _addon_release_facts
        for part in addon_rows:
            prior_sources.append(_addon_release_facts(part, witness_receipt_id=receipt_id))
        for part in main:
            if part.posting_id == second.pk:
                prior_sources.append(followup_facts(SimpleNamespace(posting=second, allocation=item,
                    pk=part.pk, amount=part.amount, read_receipt_id=receipt_id)))
            elif part.posting_id == first.pk:
                if isinstance(first.participants, list):
                    from users.receipt_usage_sources import first_negotiation_release_facts
                    verifier = first_negotiation_release_facts
                else:
                    from .points_transfer_refund_receipt_usage import facts as verifier
                prior_sources.append(verifier(SimpleNamespace(posting=first, allocation=item,
                    pk=part.pk, amount=part.amount, read_receipt_id=receipt_id,
                    history_before=revision.transfer.created_at)))
            else:
                raise ValidationError('历史主单释放不属于两次退款')
        for part in transfers:
            prior_sources.append(transfer_facts(SimpleNamespace(revision=part.revision, allocation=item,
                pk=part.pk, amount=part.amount, read_receipt_id=receipt_id)))
        for part in posts:
            prior_sources.append(post_facts(SimpleNamespace(revision=part.revision, allocation=item,
                pk=part.pk, amount=part.amount, read_receipt_id=receipt_id)))
        used = sum(part.amount for part in main + transfers + posts + addon_rows)
        if used > item.amount:
            raise ValidationError('历史释放超出原到账')
        entry = [item.pk, item.receipt_id, item.amount, used,
            [[part.pk, part.amount] for part in main], [[part.pk, part.amount] for part in transfers],
            [[part.pk, part.amount] for part in posts]]
        if addons or addon_rows:
            entry.append([[part.pk, part.amount] for part in addon_rows])
        rows.append(entry)
    witness = row.pk == 0 and row.amount == 0
    later_witness = witness and not rows and allocation.created_at > revision.transfer.created_at
    if (rows != snapshot.get('allocations')
            or (not any(item[0] == allocation.pk for item in rows) and not later_witness)):
        raise ValidationError('二次退款后转单到账来源异常')
    proofs = [first.points_refund, second.points_refund]
    funding = funding_after_refund(principal_units=original_funding.principal_units,
        gift_units=original_funding.gift_units, compensation_units=original_funding.compensation_units,
        returned_prepaid_units=sum(item.prepaid_units for item in proofs),
        returned_gift_units=sum(item.gift_units for item in proofs),
        returned_compensation_units=sum(item.compensation_units for item in proofs),
        provider_income_units=original_funding.provider_income_units,
        pending_deduction_units=sum(item.pending_deduction for item in shares),
        provider_external_units=sum(item.wallet_deduction + item.deposit_deduction for item in shares),
        inviter_rate=frozen['inviter_commission_rate'] if frozen['inviter_id'] or frozen['inviter_account_id'] else 0,
        allocated_units=sum(item[2] - item[3] for item in rows),
        rounding_reserved_units=first.rounding_to_store_units + second.rounding_to_store_units)
    addon_history = None
    if addons:
        from .points_transfer_followup_scope import combine_addon
        pending = [(item.account_id, item.pending_deduction,
                    set(item.posting.proposal.affected_segment_ids or []),
                    list(item.segment_deductions.values_list('segment_id', 'amount')) or None) for item in shares]
        try:
            funding, addon_history = combine_addon(order, funding, revision.transfer.created_at,
                                                    lines, frozen['segments'], pending)
        except RestValidationError as exc:
            raise ValidationError('转单加购资金事实异常') from exc
        if addon_history != snapshot.get('addon_history'):
            raise ValidationError('转单加购资金版本不一致')
    addon_income = sum(item['provider_units'] for item in addon_history or [])
    parts = plan_release(allocations=[(item[0], item[2], item[3]) for item in rows], amount=funding.release_units)
    actual = list(revision.releases.order_by('-allocation_id').values_list('allocation_id', 'amount'))
    if (asdict(funding) != snapshot.get('funding') or sum(incomes.values()) + addon_income != funding.provider_income_units
            or snapshot.get('releases') != [[part.allocation_id, part.amount] for part in parts]
            or actual != [(part.allocation_id, part.amount) for part in parts]
            or (witness and (funding.release_units or actual))
            or (not witness and not any(part.allocation_id == allocation.pk
                                        and part.amount == row.amount for part in parts))):
        raise ValidationError('二次退款后转单释放或资金不守恒')
    return ('RELEASE', f'order:{allocation.pk}', f'points-followup-transfer:{row.pk}', row.amount,
            _digest(['points-followup-transfer', row.pk, row.amount, allocation.pk, allocation.receipt_id,
                     revision.pk, revision.evidence_hash, source, prior_sources]))
