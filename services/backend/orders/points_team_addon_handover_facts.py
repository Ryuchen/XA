"""Read-only facts for a first same-team add-on handover.

This verifier does not authorize handover, refund, allocation or settlement.
It is intended for an order-locked caller until the shared receipt writer and
settlement paths can use the same event evidence.
"""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def has_ordered_funding_chain(runs):
    """Check each payment's predecessor without treating other payments as predecessors."""
    previous = {}
    for run in runs:
        handover = run.handover
        if (not handover or not handover.accepted_at
                or handover.previous_id != previous.get(handover.payment_id)):
            return False
        previous[handover.payment_id] = handover.pk
    return True


def verify_rejected_attempts(payment, *, before=None):
    """Verify rejected proposals without treating their shares as a transfer."""
    from .points_transfer_followup_handover_scope import _verify_rejected
    attempts = list(payment.handovers.filter(status='REJECTED').order_by('version', 'pk'))
    all_attempts = list(payment.handovers.order_by('version', 'pk'))
    if [row.version for row in all_attempts] != list(range(1, len(all_attempts) + 1)):
        raise ValidationError('同单加购接替方案版本不连续')
    for attempt in attempts:
        if before is not None and attempt.created_at >= before:
            continue
        later = next((row for row in all_attempts if row.version > attempt.version), None)
        boundary = later.created_at if later else before
        if before is not None and (boundary is None or before < boundary):
            boundary = before
        _verify_rejected(payment, attempt, before=boundary)


def _verify_invalidated(attempt, *, before, receipt_id=None):
    """Read the actual invalidating refund under the caller's existing locks."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .models import PointsHandoverRefundInvalidation, OrderAddonFundingReleaseRun, OrderPointsFundingAllocation
    from .points_handover_refund_invalidation import _verify_facts
    record = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).select_related(
        'handover__payment__intent', 'refund').first()
    run = OrderAddonFundingReleaseRun.objects.filter(refund=record.refund).first() if record else None
    if not record or not run or not before or record.created_at >= before or run.created_at >= before:
        raise ValidationError('同单接替重提缺少真实失效退款')
    if not isinstance(run.allocation_snapshot, list):
        raise ValidationError('失效退款的原到账分配异常')
    if receipt_id is None and run.allocation_snapshot:
        first = run.allocation_snapshot[0]
        if not isinstance(first, list) or not first or type(first[0]) is not int:
            raise ValidationError('失效退款的原到账分配异常')
        receipt_id = OrderPointsFundingAllocation.objects.filter(
            pk=first[0]).values_list('receipt_id', flat=True).first()
        if not receipt_id:
            raise ValidationError('失效退款的原到账分配缺失')
    try:
        _verify_facts(record, receipt_id=receipt_id)
    except DjangoValidationError as exc:
        raise ValidationError('同单接替失效退款凭证异常') from exc


def _verify_later_locked(order, payment, handover, *, before=None, receipt_id=None):
    """Rebuild one same-team split from its immediately preceding real split."""
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_funding_release import verify_run
    from .models import OrderAddonFundingReleaseRun, OrderProvider, OrderProviderCompletionFact
    from console.models import AdminAuditLog
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED'
            or not handover.created_at or not handover.accepted_at
            or handover.created_at >= handover.accepted_at
            or (before is not None and handover.accepted_at >= before)
            or order.refund_records.filter(created_at__lte=handover.accepted_at).exists()
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                                             created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                                                        refund__created_at__lte=handover.accepted_at).exists()):
        raise ValidationError('后续同单接替的订单或资金时序待核对')
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    if not previous or previous.pk != handover.previous_id:
        raise ValidationError('后续同单接替的前次责任待核对')
    attempts = list(payment.handovers.filter(version__gt=previous.version,
        version__lt=handover.version).order_by('version', 'pk'))
    if (handover.version <= previous.version
            or [row.version for row in attempts] != list(range(previous.version + 1, handover.version))
            or any(row.status not in ('REJECTED', 'SUPERSEDED') or row.previous_id != previous.pk
                   for row in attempts)):
        raise ValidationError('后续同单接替的方案链待核对')
    from .points_transfer_followup_handover_scope import _verify_rejected
    for index, attempt in enumerate(attempts):
        boundary = attempts[index + 1].created_at if index + 1 < len(attempts) else handover.created_at
        if attempt.status == 'REJECTED':
            _verify_rejected(payment, attempt, before=boundary)
        else:
            _verify_invalidated(attempt, before=boundary, receipt_id=receipt_id)
    prior_run = OrderAddonFundingReleaseRun.objects.filter(handover=previous,
        points_handover_snapshot__kind='SAME_TEAM_V1').first()
    if not prior_run or prior_run.created_at >= handover.created_at:
        raise ValidationError('后续同单接替缺少前次资金事件')
    verify_run(prior_run, _skip_usage=True, witness_receipt_id=receipt_id)
    active = [row for row in shares if row['is_current']]
    source, target = handover.source_segment, handover.target_segment
    if (len(active) != 1 or active[0]['segment_id'] != source.pk
            or active[0]['basis'] != handover.source_basis_before
            or source.attribution.order_id != order.pk or target.attribution.order_id != order.pk
            or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at
            or source.started_at > handover.created_at or target.started_at > handover.created_at
            or (source.released_at and source.released_at <= handover.accepted_at)
            or (target.released_at and target.released_at <= handover.accepted_at)
            or not handover.transferred_basis
            or handover.retained_basis + handover.transferred_basis != active[0]['basis']
            or not handover.remaining_work or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('后续同单接替的实际履约或份额异常')
    accounts = [source.provider_account_id, target.provider_account_id]
    assignments = list(OrderProvider.objects.filter(order=order, provider_account_id__in=accounts))
    if (len(assignments) != 2 or {row.provider_account_id for row in assignments} != set(accounts)
            or any(row.settled_at and row.settled_at <= handover.accepted_at for row in assignments)
            or OrderProviderCompletionFact.objects.filter(order=order, provider_account_id__in=accounts,
                completed_at__lte=handover.accepted_at).exists()):
        raise ValidationError('后续同单接替人员或结算状态异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (accounts[0], 'PROVIDER', True),
                (accounts[1], 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, yes) for a, role, yes, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('后续同单接替三方确认异常')
    try:
        target_commission = _commission(target, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('后续同单接替抽成凭证异常') from exc
    source_row = active[0]
    expected_rows = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                     for row in shares if not row['is_current']]
    if handover.retained_basis:
        full = _income(source_row['basis'], source_row['commission_snapshot'])
        retained = (_income(handover.retained_basis, source_row['commission_snapshot'])
                    if source_row['income'] == full else
                    source_row['income'] * handover.retained_basis // source_row['basis'])
        expected_rows.append((source.pk, handover.retained_basis, retained, source_row['commission_snapshot'], False))
    expected_rows.append((target.pk, handover.transferred_basis,
                          _income(handover.transferred_basis, target_commission), target_commission, True))
    rows = list(handover.shares.select_related('segment').order_by('position'))
    if ([row.position for row in rows] != list(range(len(rows))) or
            [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current)
             for row in rows] != expected_rows):
        raise ValidationError('后续同单接替逐份额快照异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': accounts[0],
        'target_provider': accounts[1], 'retained_basis': handover.retained_basis,
        'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('后续同单接替客服审计异常')
    return {'payment_id': payment.pk, 'handover_id': handover.pk, 'accepted_at': handover.accepted_at.isoformat(),
            'previous_id': previous.pk, 'prior_run_id': prior_run.pk,
            'shares': [[row.segment_id, row.segment.provider_account_id, row.basis, row.income,
                        row.commission_snapshot, row.is_current] for row in rows]}


def verify_locked(order, payment, handover, *, before=None, receipt_id=None):
    from .addon_handover import _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from .addon_payment import verify_receipt
    from .models import OrderOriginalShareTransfer, OrderProvider, OrderProviderCompletionFact
    from console.models import AdminAuditLog

    if (not transaction.get_connection().in_atomic_block or payment.intent.order_id != order.pk
            or order.required_provider_count != 2
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at__lte=handover.accepted_at).exists()):
        raise ValidationError('同单加购接替资金范围待核对')
    if handover.previous_id:
        return _verify_later_locked(order, payment, handover, before=before, receipt_id=receipt_id)
    verify_receipt(payment)
    accepted = handover.accepted_at
    source, target = handover.source_segment, handover.target_segment
    prior_refunds = list(payment.refund_records.filter(created_at__lt=handover.created_at)
                         .order_by('created_at', 'pk')) if handover.created_at else []
    remaining_basis = payment.intent.amount - sum(item.amount for item in prior_refunds)
    earlier = list(payment.handovers.filter(version__lt=handover.version).order_by('version', 'pk'))
    if ([row.version for row in earlier] != list(range(1, handover.version))
            or any(row.status not in ('REJECTED', 'SUPERSEDED') or row.previous_id for row in earlier)):
        raise ValidationError('同单加购接替前方案待核对')
    verify_rejected_attempts(payment, before=handover.created_at)
    for index, attempt in enumerate(earlier):
        if attempt.status == 'SUPERSEDED':
            boundary = earlier[index + 1].created_at if index + 1 < len(earlier) else handover.created_at
            _verify_invalidated(attempt, before=boundary, receipt_id=receipt_id)
    if (handover.payment_id != payment.pk or handover.status != 'ACCEPTED' or not accepted
            or (before is not None and accepted >= before) or handover.previous_id
            or source.pk != payment.intent.segment_id or source.attribution.order_id != order.pk
            or target.attribution.order_id != order.pk or source.provider_account_id == target.provider_account_id
            or not source.started_at or not target.started_at or source.started_at > accepted or target.started_at > accepted
            or (source.released_at and source.released_at <= accepted)
            or (target.released_at and target.released_at <= accepted)
            or not order.in_service_at or accepted < order.in_service_at
            or (order.completed_at and accepted >= order.completed_at)
            or remaining_basis <= 0 or handover.source_basis_before != remaining_basis
            or handover.retained_basis + handover.transferred_basis != handover.source_basis_before
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)
            or payment.refund_records.filter(created_at__gte=handover.created_at, created_at__lte=accepted).exists()):
        raise ValidationError('同单加购接替时序或责任份额待核对')
    accounts = [source.provider_account_id, target.provider_account_id]
    if OrderProviderCompletionFact.objects.filter(order=order, provider_account_id__in=accounts,
                                                   completed_at__lte=accepted).exists():
        raise ValidationError('加购接替时陪玩已结束本人履约')
    assignments = list(OrderProvider.objects.filter(order=order, provider_account_id__in=accounts))
    if (len(assignments) != 2 or {row.provider_account_id for row in assignments} != set(accounts)
            or any(row.settled_at and row.settled_at <= accepted for row in assignments)):
        raise ValidationError('接替时陪玩身份或既有结算待核对')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True),
                (accounts[0], 'PROVIDER', True), (accounts[1], 'PROVIDER', True)}
    if (len(decisions) != 3 or {(account, role, answer) for account, role, answer, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != accepted):
        raise ValidationError('同单加购接替三方确认待核对')
    try:
        target_commission = _commission(target, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('接替陪玩抽成快照待核对') from exc
    # The proposal freezes the remaining original owner's income at creation;
    # a prior refund of a different add-on may already have consumed part.
    # No refund may change this payment while its three decisions are pending.
    if (not handover.created_at or handover.created_at > accepted
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                                                        refund__created_at__lte=accepted).exists()):
        raise ValidationError('接替确认期间加购收益发生变化')
    prior_deductions = list(payment.refund_earning_deductions.filter(
        refund__created_at__lt=handover.created_at).select_related('refund', 'participant', 'segment__attribution')
        .order_by('refund__created_at', 'pk'))
    if any(row.refund.order_id != order.pk
           or row.segment_id != source.pk or row.segment.attribution.order_id != order.pk
           or row.participant.account_id != source.provider_account_id or row.amount <= 0
           for row in prior_deductions):
        raise ValidationError('接替前加购收益占用的付款或履约归属异常')
    remaining_income = payment.provider_income - sum(row.amount for row in prior_deductions)
    if not 0 <= remaining_income <= handover.source_basis_before:
        raise ValidationError('接替前加购剩余收益异常')
    expected_rows = []
    if handover.retained_basis:
        original_income = _income(handover.source_basis_before, payment.intent.commission_snapshot)
        retained_income = (_income(handover.retained_basis, payment.intent.commission_snapshot)
                           if remaining_income == original_income else
                           remaining_income * handover.retained_basis // handover.source_basis_before)
        expected_rows.append((source.pk, handover.retained_basis, retained_income,
                              payment.intent.commission_snapshot, False))
    expected_rows.append((target.pk, handover.transferred_basis,
                          _income(handover.transferred_basis, target_commission), target_commission, True))
    rows = list(handover.shares.order_by('position'))
    if ([(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows]
            != expected_rows or [row.position for row in rows] != list(range(len(rows)))):
        raise ValidationError('同单加购接替的逐段资金快照异常')
    path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/'
    expected_body = {'order_id': order.pk, 'payment_id': payment.pk, 'source_provider': accounts[0],
        'target_provider': accounts[1], 'retained_basis': handover.retained_basis,
        'completed_work': handover.completed_work, 'remaining_work': handover.remaining_work}
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
                                               path=path, method='POST', status_code=200))
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != expected_body:
        raise ValidationError('同单加购接替的客服审计凭证异常')
    result = {'payment_id': payment.pk, 'handover_id': handover.pk, 'accepted_at': accepted.isoformat(),
              'shares': [[row.segment_id, row.segment.provider_account_id, row.basis, row.income,
                          row.commission_snapshot, row.is_current] for row in rows]}
    if prior_refunds:
        result['prior_payment_refunds'] = [[row.pk, row.amount, row.created_at.isoformat()] for row in prior_refunds]
    if prior_deductions:
        result['prior_earning_deductions'] = [[row.pk, row.refund_id, row.segment_id,
                                               row.participant.account_id, row.amount] for row in prior_deductions]
    return result


def funding_facts(run, *, receipt_id):
    """Reconstruct the first same-team handover's whole-order funding.

    This is a current-read verifier, not an allocation writer. No order or
    allocation row locks are acquired while a receipt is held.
    """
    from users.receipt_usage_sources import _require_current_unlocked_reads
    from .addon_funding_history import combined_at
    from .models import OrderAddonHandover, OrderAddonRefund, OrderPrestartHandoff
    from .points_addon_receipt_usage import payment_facts
    from .points_funding_plan import PointsFundingPlan, PointsFundingPlanError

    _require_current_unlocked_reads()
    handover = run.handover
    if not handover or not run.created_at:
        raise ValidationError('同单加购接替的资金事件缺失')
    payment, order = handover.payment, handover.payment.intent.order
    if (order.refund_records.filter(created_at__lte=run.created_at).exists()
            or OrderAddonRefund.objects.filter(order=order, created_at__gte=handover.accepted_at,
                                                  created_at__lte=run.created_at).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or handover.accepted_at >= run.created_at):
        raise ValidationError('同单加购接替的其他资金事件尚未接入')
    previous = list(OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
        accepted_at__lt=handover.accepted_at).order_by('accepted_at', 'pk'))
    same_payment = [item for item in previous if item.payment_id == payment.pk]
    if ((same_payment[-1].pk if same_payment else None) != handover.previous_id
            or OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                accepted_at=handover.accepted_at).exclude(pk=handover.pk).exists()
            or OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
                accepted_at__gt=handover.accepted_at, accepted_at__lte=run.created_at).exists()):
        raise ValidationError('同单加购接替事件时序待核对')
    from .models import OrderAddonFundingReleaseRun
    from .addon_funding_release import verify_run
    prior_facts, refund_facts = [], []
    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=handover.accepted_at)
                   .select_related('payment').order_by('created_at', 'pk'))
    events = [(item.accepted_at, 'handover', item) for item in previous]
    events += [(item.created_at, 'refund', item) for item in refunds]
    if len({event[0] for event in events}) != len(events):
        raise ValidationError('此前同单接替和退款事件时序异常')
    last_at, accepted_payments = None, set()
    for event_at, kind, item in sorted(events, key=lambda event: event[0]):
        if last_at and event_at <= last_at:
            raise ValidationError('此前同单资金事件与版本时间重叠')
        if kind == 'handover':
            prior = OrderAddonFundingReleaseRun.objects.filter(handover=item,
                points_handover_snapshot__kind='SAME_TEAM_V1').first()
            if not prior or prior.created_at <= event_at or prior.created_at >= handover.accepted_at:
                raise ValidationError('此前同单加购接替资金版本待核对')
            accepted_payments.add(item.payment_id)
            verify_run(prior, _skip_usage=True, witness_receipt_id=receipt_id)
            prior_facts.append([prior.pk, prior.points_handover_hash])
        else:
            prior = OrderAddonFundingReleaseRun.objects.filter(refund=item).first()
            from .points_team_other_addon_refund import applies as team_other_applies
            if ((item.payment_id not in accepted_payments and not team_other_applies(item))
                    or not prior or not prior.points_addon_snapshot
                    or prior.created_at < event_at or prior.created_at >= handover.accepted_at):
                raise ValidationError('此前同单加购退款资金版本待核对')
            verify_run(prior, _skip_usage=True, witness_receipt_id=receipt_id)
            refund_facts.append([prior.pk, prior.points_addon_hash])
        last_at = prior.created_at
    handover_facts = verify_locked(order, payment, handover, before=run.created_at, receipt_id=receipt_id)
    paid = payment_facts(payment, read_receipt_id=receipt_id)
    shares = paid.get('main_shares')
    if not isinstance(shares, list) or len(shares) != 2:
        raise ValidationError('同单加购的主单双陪资金快照缺失')
    try:
        main = PointsFundingPlan(**paid['main_plan'])
        combined = combined_at(order, main, run.created_at,
            segment_shares=[(row[1], row[2], row[3], row[6], row[7]) for row in shares])
    except (KeyError, TypeError, IndexError, PointsFundingPlanError) as exc:
        raise ValidationError('同单加购接替整单资金待核对') from exc
    if combined.get('additional_store_funding_units') != run.required_amount:
        raise ValidationError('同单加购接替与整单补资不一致')
    result = {'version': 1, 'payment': paid, 'handover': handover_facts, 'funding': combined}
    if prior_facts:
        result['prior_handovers'] = prior_facts
    if refund_facts:
        result['prior_refunds'] = refund_facts
    return result
