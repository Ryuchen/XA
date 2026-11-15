"""Receipt-local proof for a posted fourth or later points refund."""
from dataclasses import asdict
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from stores.db import transaction


def _verify(version, *, receipt_id=None, order_locked=False):
    from .models import (OrderPointsFundingAllocation, OrderPointsFundingRelease,
        OrderPointsTransferFundingRelease, PointsPostRefundTransferRelease, PointsFollowupTransferRelease,
        OrderAddonFundingRelease, PointsThirdTransferRelease, PointsRefundRoundEvidence,
        PointsThirdAddonFundingRevision, PointsThirdTransferRevision)
    from .points_refund_round_evidence import _digest as round_digest
    from .points_funding_allocation import plan_release, PointsFundingAllocationError
    from .points_negotiation_preview import funding_after_refund, asset_digest
    from .points_negotiation_execution import participant_digest
    from .negotiation_proposals import _digest as proposal_digest
    from .refund_math import payment_refund, wallet_source_refund, RefundCalculationError
    from .points_third_refund_debit_facts import verify as verify_debits
    from .points_round_refund_release import posting_active
    from stores.context import current_store
    from rest_framework.exceptions import ValidationError as RestValidationError
    store = current_store()
    if (not transaction.get_connection().in_atomic_block or not store
            or store.database_alias != transaction.get_connection().alias
            or version._state.db != transaction.get_connection().alias):
        raise ValidationError('多轮退款核验不属于当前门店事务')
    order, posting, snapshot = version.order, version.posting, version.snapshot
    if (not isinstance(snapshot, dict) or version.evidence_hash != round_digest(snapshot)
            or snapshot.get('order_id') != order.pk or posting.proposal.case.order_id != order.pk
            or not version.previous_id or version.previous.order_id != order.pk
            or version.previous.created_at >= posting.created_at
            or (not posting.termination_processed_at and not posting_active(order, posting))):
        raise ValidationError('多轮退款版本或结案状态异常')
    rounds = snapshot.get('rounds')
    if (not isinstance(rounds, list) or len(rounds) < 4
            or rounds[:-1] != version.previous.snapshot.get('rounds')
            or not isinstance(rounds[-1], dict) or rounds[-1].get('posting_id') != posting.pk):
        raise ValidationError('多轮退款真实前链异常')
    source = rounds[-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise ValidationError('多轮退款资金来源缺失')
    current = source[1]
    keys = {'allocations','releases','funding','prior_round_id','prior_round_hash',
            'previous_addon_id','previous_addon_hash','previous_transfer_id','previous_transfer_hash','prior_funding'}
    if 'pending_handovers' in current:
        keys |= {'pending_handovers'}
    if 'provider_recoveries' in current:
        keys |= {'provider_recoveries'}
    if 'paid_snapshot' in current:
        keys |= {'paid_snapshot'}
    if set(current) != keys:
        raise ValidationError('多轮退款资金快照异常')
    addon = PointsThirdAddonFundingRevision.objects.filter(order=order, created_at__lt=posting.created_at).order_by('-created_at','-pk').first()
    transfer = PointsThirdTransferRevision.objects.filter(order=order, created_at__lt=posting.created_at).order_by('-created_at','-pk').first()
    context = {key: current[key] for key in keys - {'funding','releases'}}
    if (current['prior_round_id'] != version.previous_id
            or current['prior_round_hash'] != version.previous.evidence_hash
            or current['previous_addon_id'] != (addon.pk if addon else None)
            or current['previous_addon_hash'] != (addon.evidence_hash if addon else None)
            or current['previous_transfer_id'] != (transfer.pk if transfer else None)
            or current['previous_transfer_hash'] != (transfer.evidence_hash if transfer else None)
            or version.latest_transfer_version_id != (transfer.pk if transfer else None)
            or posting.proposal.points_terms.round_context != context):
        raise ValidationError('多轮退款方案与实际前版不一致')
    from .models import OrderSegmentPersonSettlement
    paid_rows = list(OrderSegmentPersonSettlement.objects.filter(manifest__order=order,
        settled_at__lt=posting.created_at).order_by('pk'))
    if OrderSegmentPersonSettlement.objects.filter(manifest__order=order,
            settled_at=posting.created_at).exists():
        raise ValidationError('人员结算与退款时序无法区分')
    paid = current.get('paid_snapshot')
    if paid_rows:
        if (not isinstance(paid, dict) or not isinstance(paid.get('people'), list)
                or any(row.manifest_id != paid.get('manifest_id') for row in paid_rows)):
            raise ValidationError('多轮退款缺少本单已付人员原清单')
        from .points_voluntary_paid_evidence import verify_historical_paid
        from rest_framework.exceptions import ValidationError as RestValidationError
        try:
            verify_historical_paid(order, paid, posting.created_at, paid.get('manifest_id'))
        except RestValidationError as exc:
            raise ValidationError('多轮退款原已付流水待核对') from exc
        from engagement.confirmation import aftersale_deadline
        deadline = aftersale_deadline(order)
        opened = posting.proposal.case_opened_at
        proposal_account_ids = set(posting.proposal.affected_accounts or [])
        if (not order.completed_at or not deadline or not opened or opened >= deadline
                or opened > posting.accepted_at or not proposal_account_ids
                or any(row.account_id in proposal_account_ids for row in paid_rows)):
            raise ValidationError('已付人员退款售后时间或受影响对象异常')
    elif paid is not None:
        raise ValidationError('多轮退款的已付清单没有真实已付人员')
    previous_source = version.previous.snapshot.get('rounds', [])[-1].get('release_source')
    previous_paid = (previous_source[1].get('paid_snapshot') if isinstance(previous_source, list)
                     and len(previous_source) == 2 and isinstance(previous_source[1], dict) else None)
    if isinstance(previous_paid, dict) and isinstance(paid, dict):
        from .models import OrderSegmentSettlementRevision
        if previous_paid.get('manifest_id') != paid.get('manifest_id'):
            raise ValidationError('相邻退款的原已付清单不一致')
        revisions = list(OrderSegmentSettlementRevision.objects.filter(manifest_id=paid['manifest_id'],
            created_at__gt=version.previous.posting.created_at, created_at__lt=posting.created_at)
            .select_related('posting__proposal__points_terms').order_by('created_at', 'pk'))
        if not revisions:
            if previous_paid != paid:
                raise ValidationError('清单未修订期间不能改变已付快照')
        else:
            from .segment_settlement import _hash
            from .points_transfer_paid_addons import unchanged
            plan = previous_paid['plan']
            protected = previous_paid
            for revision in revisions:
                from .models import PointsRefundRoundEvidence
                revision_version = PointsRefundRoundEvidence.objects.filter(order=order,
                    posting=revision.posting, previous__isnull=False).first()
                revision_context = (revision.posting.proposal.points_terms.round_context
                                    if revision_version else None)
                revision_paid = revision_context.get('paid_snapshot') if isinstance(revision_context, dict) else None
                if (not revision_version or not revision.posting.termination_processed_at
                        or revision.posting.created_at > version.previous.posting.created_at
                        or revision.posting.created_at >= revision.created_at
                        or not isinstance(revision_paid, dict) or revision_paid.get('manifest_id') != paid['manifest_id']
                        or revision_paid.get('plan') != plan or revision.before_plan_hash != _hash(plan)
                        or revision.after_plan_hash != _hash(revision.after_plan)):
                    raise ValidationError('退款间已付清单修订来源异常')
                _verify(revision_version, receipt_id=receipt_id, order_locked=order_locked)
                verify_historical_paid(order, revision_paid, revision.posting.created_at, paid['manifest_id'])
                before_people = {item['account_id']: item for item in plan.get('people', [])}
                after_people = {item['account_id']: item for item in revision.after_plan.get('people', [])}
                if any(before_people.get(item['account_id']) != after_people.get(item['account_id'])
                       for item in protected['people']):
                    raise ValidationError('退款间清单修订改变已付人员收益')
                unchanged(protected, revision.after_plan.get('points_funding', {}).get('addons', []))
                plan, protected = revision.after_plan, revision_paid
            if paid.get('plan') != plan or paid.get('plan_hash') != _hash(plan):
                raise ValidationError('退款时已付清单与实际修订终点不一致')
            if any(item not in paid['people'] for item in protected['people']):
                raise ValidationError('退款时已付人员原流水缺失')
    if 'pending_handovers' in current:
        from .models import OrderAddonHandover, PointsThirdHandoverTargetInvalidation
        from .points_third_target_invalidation import verify as verify_target_invalidation
        pending = current['pending_handovers']
        if (not isinstance(pending, list) or not pending or any(not isinstance(row, list) or len(row) != 8 for row in pending)
                or [row[0] for row in pending] != sorted({row[0] for row in pending})):
            raise ValidationError('多轮退款待交方案快照异常')
        for item in pending:
            handover = OrderAddonHandover.objects.select_related('payment__intent').filter(pk=item[0],
                payment__intent__order=order, kind='STANDARD').first()
            if not handover:
                raise ValidationError('多轮退款待交方案缺失')
            decisions = [[d.pk, d.account_id, d.role, d.accepted, d.decided_at.isoformat() if d.decided_at else None]
                         for d in handover.decisions.order_by('pk')]
            actual = [handover.pk, handover.payment_id, handover.source_segment_id, handover.target_segment_id,
                      handover.previous_id, handover.source_basis_before, handover.created_at.isoformat(), decisions]
            if actual != item or handover.created_at >= posting.created_at or handover.target_segment_id not in (posting.proposal.affected_segment_ids or []):
                raise ValidationError('多轮退款待交目标与真实方案不一致')
            invalidation = PointsThirdHandoverTargetInvalidation.objects.filter(handover=handover, posting=posting).first()
            if invalidation:
                from rest_framework.exceptions import ValidationError as RestValidationError
                try:
                    verify_target_invalidation(invalidation)
                except RestValidationError as exc:
                    raise ValidationError('多轮退款待交目标失效事实异常') from exc
            elif not posting_active(order, posting) or handover.status != 'PENDING':
                raise ValidationError('多轮退款待交目标缺少真实失效记录')
    allocations = list(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order=order) | Q(service_order=order), created_at__lt=posting.created_at).order_by('pk'))
    witness = receipt_id or (allocations[0].receipt_id if allocations and order_locked else None)
    if witness is not None:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import _require_current_unlocked_reads
        _locked_receipt(witness)
        _require_current_unlocked_reads()
    previous = version.previous
    if len(rounds) == 4:
        if witness is not None:
            from .points_third_refund_receipt_usage import facts as third_facts
            allocation = OrderPointsFundingAllocation.objects.filter(
                Q(redemption__order=order) | Q(service_order=order), receipt_id=witness).first()
            if not allocation:
                raise ValidationError('多轮退款到账见证不属于本单')
            old_release = previous.posting.funding_releases.select_related('allocation').order_by('pk').first()
            third_facts(old_release or SimpleNamespace(posting=previous.posting, allocation=allocation, pk=0, amount=0),
                        read_receipt_id=witness)
        elif order_locked:
            from .points_third_refund_no_receipt import verify_locked as third_no_receipt
            old = previous.snapshot['rounds'][-1]['release_source'][1]
            third_no_receipt(order, previous.posting, previous.transfer_revision, old)
        else:
            raise ValidationError('无到账须在本单资金事务核验')
    else:
        _verify(previous, receipt_id=witness, order_locked=order_locked)
    from .points_third_addon_receipt_usage import _verify as verify_addon, _verify_third_transfer
    if addon:
        verify_addon(addon.run, witness_receipt_id=witness, order_locked=order_locked)
    if transfer:
        _verify_third_transfer(transfer, witness_receipt_id=witness, order_locked=order_locked)
    events = [previous] + [item for item in (addon, transfer) if item and item.created_at > previous.created_at]
    latest = max(events, key=lambda item: item.created_at)
    prior = previous.snapshot['rounds'][-1]['release_source'][1]['funding'] if latest is previous else latest.snapshot['after']
    rows = []
    for allocation in allocations:
        released = sum(model.objects.filter(allocation=allocation, **{field + '__lt': posting.created_at})
            .aggregate(total=Sum('amount'))['total'] or 0 for model, field in (
                (OrderPointsFundingRelease, 'created_at'), (OrderPointsTransferFundingRelease, 'created_at'),
                (PointsPostRefundTransferRelease, 'revision__created_at'), (PointsFollowupTransferRelease, 'revision__created_at'),
                (OrderAddonFundingRelease, 'run__created_at'), (PointsThirdTransferRelease, 'revision__created_at')))
        if released > allocation.amount:
            raise ValidationError('多轮退款历史释放超过原到账')
        rows.append([allocation.pk, allocation.amount, released])
    available = sum(amount - released for _, amount, released in rows)
    expected_prior = dict(prior, allocated_units=available, release_units=0,
                          remaining_units=max(prior['required_units'] - available, 0))
    if current['allocations'] != rows or current['prior_funding'] != expected_prior:
        raise ValidationError('多轮退款前到账及资金版本不一致')
    proof, proposal = posting.points_refund, posting.proposal
    prior_postings = list(type(posting).objects.filter(points_refund__redemption__order=order,
        created_at__lt=posting.created_at).select_related('points_refund').order_by('created_at','pk'))
    if ([item.pk for item in prior_postings] != [item['posting_id'] for item in rounds[:-1]]
            or any(item.points_refund.redemption_id != proof.redemption_id for item in prior_postings)):
        raise ValidationError('多轮退款原付款或先后顺序异常')
    original = list(order.funding_allocations.select_related('lot').order_by('pk'))
    from engagement.funding_wallet import validate_order_funding_sources
    try:
        validate_order_funding_sources(order, original)
        totals = {kind: sum(x.amount for x in original if x.kind == kind) for kind in ('PREPAID','GIFT','COMPENSATION')}
        prior_proofs = [x.points_refund for x in prior_postings]
        expected = payment_refund(paid_coin_units=proof.redemption.paid_units,
            paid_point_hundredths=proof.redemption.points_hundredths, point_value_units=proof.redemption.discount_units,
            refund_equivalent_units=proposal.refund_units,
            previously_refunded_equivalent_units=sum(x.equivalent_units for x in prior_proofs),
            previously_refunded_coin_units=sum(x.coin_units for x in prior_proofs),
            previously_refunded_point_hundredths=sum(x.points_hundredths for x in prior_proofs))
        sources = wallet_source_refund(coin_units=expected.coin_units, paid_prepaid_units=totals['PREPAID'],
            paid_gift_units=totals['GIFT'], paid_compensation_units=totals['COMPENSATION'],
            refunded_prepaid_units=sum(x.prepaid_units for x in prior_proofs),
            refunded_gift_units=sum(x.gift_units for x in prior_proofs),
            refunded_compensation_units=sum(x.compensation_units for x in prior_proofs))
        if ((proof.coin_units, proof.points_hundredths, proof.is_final, proof.prepaid_units, proof.gift_units, proof.compensation_units)
                != (expected.coin_units, expected.point_hundredths, expected.is_final,
                    sources.prepaid_units, sources.gift_units, sources.compensation_units)
                or proof.equivalent_units != proposal.refund_units
                or proof.refund.order_id != order.pk or proof.refund.amount != proof.coin_units):
            raise ValidationError('多轮原路退款来源异常')
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
            raise ValidationError('多轮退款实际钱包或积分流水异常')
        shares = list(posting.shares.select_related('assignment').order_by('pk'))
        accounts = proposal.affected_accounts
        selected = proposal.affected_segment_ids or []
        if (not shares or not isinstance(accounts, list) or not accounts or len(set(accounts)) != len(accounts)
                or not selected or len(set(selected)) != len(selected)
                or len({x.account_id for x in shares}) != len(shares)
                or {x.account_id for x in shares} != set(accounts)):
            raise ValidationError('多轮退款受影响人员或履约段异常')
        from .models import (OrderAddonPayment, OrderAddonRefundMainSegmentDeduction,
                             OrderParticipationSegment, OrderSegmentPersonSettlement)
        from .points_transfer_segment_math import segment_lines
        from .points_round_closed import from_postings
        anchor = transfer if transfer else version.transfer_revision
        anchor_facts = anchor.snapshot.get('facts') if isinstance(anchor.snapshot, dict) else None
        if not isinstance(anchor_facts, dict):
            raise ValidationError('多轮退款的履约资金快照缺失')
        lines = segment_lines(anchor_facts, through_transfer_id=anchor.transfer_id)
        remaining = {line[0]: line[4] for line in lines}
        fact_segments = {item['id']: item for item in anchor_facts['segments']}
        segments = {item.pk: item for item in OrderParticipationSegment.objects.filter(
            attribution__order=order, pk__in=remaining)}
        if (not set(selected).issubset(segments) or any(pk not in remaining or pk not in fact_segments for pk in selected)
                or {segments[pk].provider_account_id for pk in selected} != set(accounts)):
            raise ValidationError('多轮退款选中履约段与本人不一致')
        for old_round in rounds[:-1]:
            for old_share in old_round.get('shares', []):
                if not isinstance(old_share, list) or len(old_share) < 9:
                    raise ValidationError('历史本人逐段承担缺失')
                for segment_id, amount in old_share[8]:
                    if segment_id not in remaining or amount < 0 or amount > remaining[segment_id]:
                        raise ValidationError('历史退款超过本人履约收益')
                    remaining[segment_id] -= amount
        for segment_id, amount in OrderAddonRefundMainSegmentDeduction.objects.filter(
                participant__proposal__payment__intent__order=order,
                participant__proposal__refund__created_at__lt=posting.created_at).values_list('segment_id', 'amount'):
            if segment_id not in remaining or amount > remaining[segment_id]:
                raise ValidationError('加购退款超过本人历史主单收益')
            remaining[segment_id] -= amount
        closed = from_postings(prior_postings)
        if set(selected).intersection(closed):
            raise ValidationError('历史已终止履约段不能再次承担')
        from .models import OrderOriginalShareTransfer
        for segment_id in selected:
            segment = segments[segment_id]
            if (not proposal.case_opened_at or not segment.started_at
                    or segment.accepted_at > segment.started_at
                    or segment.started_at > proposal.case_opened_at):
                raise ValidationError('选中履约段的开始时间待核对')
            if segment.released_at and segment.released_at <= posting.created_at:
                event = OrderOriginalShareTransfer.objects.filter(previous_segment=segment,
                    previous_segment__attribution__order=order).first()
                if (not order.completed_at or order.completed_at > proposal.case_opened_at or not event
                        or event.created_at != segment.released_at or event.created_at > proposal.case_opened_at
                        or fact_segments[segment_id]['original_share'] != event.retained_share):
                    raise ValidationError('已转出履约段的售后或原价份额待核对')
        if OrderSegmentPersonSettlement.objects.filter(manifest__order=order, account_id__in=accounts,
                settled_at__lte=posting.created_at).exists():
            raise ValidationError('退款前已有逐人结算，须先核对')
        eligible = {}
        for account in accounts:
            own_selected = [segments[pk] for pk in selected if segments[pk].provider_account_id == account]
            cycles = {segment.provider_business_cycle for segment in own_selected}
            if len(cycles) != 1 or None in cycles:
                raise ValidationError('本人履约业务周期待核对')
            cycle = next(iter(cycles))
            others = [segment for segment in segments.values()
                      if segment.provider_account_id == account and segment.pk not in selected
                      and segment.started_at and segment.started_at < posting.created_at]
            if any(segment.provider_business_cycle != cycle for segment in others):
                raise ValidationError('本人其他未结收益的履约周期待核对')
            eligible[account] = sorted(x.pk for x in own_selected) + sorted(segment.pk for segment in others)
        from .addon_payment import verify_receipt
        from .points_third_addon_responsibilities import current as responsibilities_at
        addon_income = {account: 0 for account in accounts}
        addon_sources = {account: [] for account in accounts}
        if OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at=posting.created_at).exists():
            raise ValidationError('加购付款与主单退款时序无法区分')
        payments = OrderAddonPayment.objects.filter(intent__order=order,
            buyer_transaction__created_at__lt=posting.created_at).select_related('intent').order_by('pk')
        for addon_payment in payments:
            verify_receipt(addon_payment)
            _, shares_before = responsibilities_at(addon_payment, lock=False,
                before=posting.created_at, with_share_id=True)
            for part in shares_before:
                if part['provider_id'] not in addon_income or not part['income']:
                    continue
                if (addon_payment.settlements.filter(segment_id=part['segment_id'], settled_at__lte=posting.created_at).exists()
                        or addon_payment.segment_settlements.filter(segment_id=part['segment_id'], settled_at__lte=posting.created_at).exists()):
                    continue
                if part['segment_id'] not in eligible[part['provider_id']]:
                    continue
                addon_income[part['provider_id']] += part['income']
                addon_sources[part['provider_id']].append({'payment_id': addon_payment.pk,
                    'segment_id': part['segment_id'], 'addon_share_id': part.get('share_id'),
                    'income_before': part['income']})
        from .refund_math import provider_refund_burden
        weights = {account: sum(fact_segments[pk]['original_share'] for pk in selected
                    if segments[pk].provider_account_id == account) for account in accounts}
        burden = provider_refund_burden(refund_equivalent_units=proposal.refund_units,
                                        original_price_weights=weights)
        frozen_shares = []
        expected_main_plan, expected_addon_plan, expected_terms = [], [], []
        for share in sorted(shares, key=lambda x: x.account_id):
            own = eligible[share.account_id]
            if (share.original_income != sum(remaining[pk] for pk in own) + addon_income[share.account_id]
                    or share.assignment.order_id != order.pk
                    or share.assignment.provider_account_id != share.account_id
                    or share.burden != burden.amounts[share.account_id]
                    or share.retained_income != share.original_income - share.pending_deduction):
                raise ValidationError('多轮退款本人真实收益或承担不一致')
            total_income = sum(remaining[pk] for pk in own) + addon_income[share.account_id]
            expected_terms.append({'account_id': share.account_id, 'original_income_units': total_income,
                'burden_units': share.burden, 'retained_units': max(total_income - share.burden, 0),
                'additional_units': max(share.burden - total_income, 0)})
            left = min(share.burden, total_income)
            for segment_id in own:
                amount = min(remaining[segment_id], left)
                if amount:
                    expected_main_plan.append({'account_id': share.account_id, 'segment_id': segment_id, 'amount': amount})
                    left -= amount
            for part in sorted(addon_sources[share.account_id], key=lambda x:
                    (x['payment_id'], x['segment_id'], x['addon_share_id'] or 0)):
                amount = min(part['income_before'], left)
                if amount:
                    expected_addon_plan.append({**part, 'amount': amount})
                    left -= amount
            if left:
                raise ValidationError('本人待结收益分配不守恒')
            verify_debits(order, posting, share)
            parts = list(share.segment_deductions.select_related('segment__attribution').order_by('segment_id'))
            addon_parts = list(share.addon_deductions.order_by('position'))
            if (sum(x.amount for x in parts) + sum(x.amount for x in addon_parts) != share.pending_deduction
                    or any(x.segment.attribution.order_id != order.pk or x.segment.provider_account_id != share.account_id
                           or x.segment_id not in own for x in parts)):
                raise ValidationError('多轮退款本人逐段承担异常')
            frozen_share = [share.pk, share.account_id, share.pending_deduction, share.wallet_deduction,
                share.deposit_deduction, share.wallet_transaction_id, share.deposit_deficit_id, 'ROWS',
                [[part.segment_id, part.amount] for part in parts]]
            if proposal.points_terms.addon_pending_plan is not None:
                frozen_share.append([[part.pk, part.payment_id, part.segment_id, part.addon_share_id,
                                      part.income_before, part.amount] for part in addon_parts])
            frozen_shares.append(frozen_share)
        # Evidence rows use primary-key order even when account order differs.
        frozen_by_id = {item[0]: item for item in frozen_shares}
        frozen_shares = [frozen_by_id[item.pk] for item in shares]
        if (proposal.provider_terms != expected_terms
                or proposal.points_terms.segment_pending_plan != expected_main_plan
                or (proposal.points_terms.addon_pending_plan is not None
                    and proposal.points_terms.addon_pending_plan != expected_addon_plan)
                or (proposal.points_terms.addon_pending_plan is None and expected_addon_plan)):
            raise ValidationError('多轮退款逐人逐段承担顺序与确认方案不一致')
        if 'provider_recoveries' in current:
            recoveries = current['provider_recoveries']
            expected_recoveries = [{'account_id': x.account_id, 'pending_units': x.pending_deduction,
                'wallet_units': x.wallet_deduction, 'deposit_units': x.deposit_deduction, 'shortfall_units': 0,
                'wallet_available_before': x.wallet_available_before, 'deposit_paid_before': x.deposit_paid_before}
                for x in sorted(shares, key=lambda x: x.account_id)]
            if recoveries != expected_recoveries:
                raise ValidationError('多轮退款逐人承担与确认快照不一致')
        elif len(shares) != 1:
            raise ValidationError('多轮退款缺少逐人确认快照')
        decisions = list(proposal.decisions.values_list('account_id','role','accepted','decided_at'))
        expected_decisions = {(order.customer_account_id,'BOSS',True)} | {
            (share.account_id,'PROVIDER',True) for share in shares}
        frozen_round = rounds[-1]
        if (posting.rounding_to_store_units != burden.rounding_to_store_units
                or frozen_round.get('proof_id') != proof.pk or frozen_round.get('refund_id') != proof.refund_id
                or frozen_round.get('coin_transaction_id') != proof.coin_transaction_id
                or frozen_round.get('points_ledger_id') != proof.points_ledger_id
                or frozen_round.get('assets') != [proof.equivalent_units, proof.coin_units, proof.points_hundredths,
                                                  proof.prepaid_units, proof.gift_units, proof.compensation_units]
                or frozen_round.get('shares') != frozen_shares
                or frozen_round.get('releases') != [list(x) for x in posting.funding_releases.order_by('allocation_id').values_list('pk','allocation_id','amount')]
                or len(decisions) != len(expected_decisions) or {(a,b,c) for a,b,c,_ in decisions} != expected_decisions
                or any(not at for _,_,_,at in decisions) or proposal.status != 'ACCEPTED'
                or proposal.accepted_at != max(at for _,_,_,at in decisions) or posting.accepted_at != proposal.accepted_at
                or posting.participants_hash != participant_digest(proposal, posting.participants)
                or proof.refund.recovered_amount != sum(x.wallet_deduction + x.deposit_deduction for x in shares)):
            raise ValidationError('多轮退款双方确认或本人承担异常')
        from copy import copy
        episode = copy(proposal.case)
        episode.created_at, episode.client_request_id = proposal.case_opened_at, proposal.case_request_id
        terms = proposal.points_terms
        assets = {'redemption_id': terms.redemption_id, 'coin_units': terms.coin_units,
            'points_hundredths': terms.points_hundredths, 'prepaid_units': terms.prepaid_units,
            'gift_units': terms.gift_units, 'compensation_units': terms.compensation_units, 'is_final': terms.is_final,
            'prior_refund_id': terms.prior_refund_id, 'segment_pending_plan': terms.segment_pending_plan,
            'round_context': terms.round_context}
        if terms.addon_pending_plan is not None:
            assets['addon_pending_plan'] = terms.addon_pending_plan
        from .refunds import request_fingerprint
        if (proof.refund.operator_account_id != posting.operator_id or proof.refund.reason != proposal.explanation
                or proof.refund.request_fingerprint != request_fingerprint(proof.coin_units, proposal.explanation,
                    {'proposal_id': proposal.pk, 'terms_hash': proposal.terms_hash, 'equivalent_units': proposal.refund_units})
                or proposal.terms_hash != proposal_digest(episode, proposal.version, proposal.affected_accounts,
                proposal.refund_units, proposal.provider_terms, proposal.explanation, proposal.affected_segment_ids)
                or posting.terms_hash != proposal.terms_hash or terms.evidence_hash != asset_digest(proposal, assets)
                or terms.redemption_id != proof.redemption_id or terms.prior_refund_id != prior_proofs[-1].pk
                or (terms.coin_units, terms.points_hundredths, terms.prepaid_units, terms.gift_units, terms.compensation_units, terms.is_final)
                    != (proof.coin_units, proof.points_hundredths, proof.prepaid_units, proof.gift_units, proof.compensation_units, proof.is_final)):
            raise ValidationError('多轮退款已确认方案或原付款不一致')
        funding = funding_after_refund(principal_units=prior['net_principal_units'], gift_units=prior['net_gift_units'],
            compensation_units=prior['net_compensation_units'], returned_prepaid_units=proof.prepaid_units,
            returned_gift_units=proof.gift_units, returned_compensation_units=proof.compensation_units,
            provider_income_units=prior['provider_income_units'], pending_deduction_units=sum(x.pending_deduction for x in shares),
            provider_external_units=prior['planned_provider_external_units'] + sum(x.wallet_deduction + x.deposit_deduction for x in shares),
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            allocated_units=available, rounding_reserved_units=prior['rounding_reserved_units'] + posting.rounding_to_store_units)
        planned = sorted([[part.allocation_id, part.amount] for part in plan_release(allocations=rows, amount=funding.release_units)])
        actual = sorted([list(x) for x in posting.funding_releases.values_list('allocation_id','amount')])
        if current['funding'] != asdict(funding) or current['releases'] != planned or actual != planned:
            raise ValidationError('多轮退款实际释放或资金不守恒')
    except (RestValidationError, RefundCalculationError, PointsFundingAllocationError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError('多轮退款真实资金待核对') from exc
    return version, planned


def facts(row, *, read_receipt_id=None):
    from users.receipt_usage_sources import _digest
    from .models import PointsRefundRoundEvidence
    version = PointsRefundRoundEvidence.objects.filter(posting=row.posting).first()
    if not version:
        raise ValidationError('多轮退款资金版本缺失')
    version, planned = _verify(version, receipt_id=read_receipt_id or row.allocation.receipt_id)
    if row.amount <= 0 or [row.allocation_id, row.amount] not in planned:
        raise ValidationError('多轮退款释放不属于真实计划')
    return ('RELEASE', f'order:{row.allocation_id}', f'points-round-refund:{row.pk}', row.amount,
            _digest(['points-round-refund', row.pk, row.allocation_id, row.allocation.receipt_id,
                     row.amount, version.pk, version.evidence_hash]))


def verify_locked(version):
    from .points_transfer_refund_access import permits
    if not any(permits(version.order, phase=phase) for phase in ('refund','settlement')):
        raise ValidationError('多轮退款须在本单资金事务核验')
    result, _ = _verify(version, order_locked=True)
    for row in version.posting.funding_releases.select_related('allocation__receipt').order_by('allocation__receipt_id','pk'):
        facts(row)
    return result
