"""A real ordinary add-on refund invalidates its pending responsibility split."""
from django.core.exceptions import ValidationError


def permits(order, payment, handover):
    from .ordinary_team_pre_main_handover import permits as ordinary_permits
    from .ordinary_refunded_team_handover import permits as refunded_permits
    from .ordinary_transfer_capability import permits as transferred_permits
    transferred = transferred_permits(order, allow_addon_refund=True, allow_addon_handover=True)
    return bool((ordinary_permits(order) or refunded_permits(order) or transferred) and handover
        and handover.payment.intent.order_id == order.pk and payment.intent.order_id == order.pk
        and handover.status == 'PENDING' and not handover.accepted_at
        and (transferred or order.refund_records.count() <= 1) and not order.settled_at
        and not order.providers.filter(settled_at__isnull=False).exists())


def capture(handover, refund):
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    from console.models import AdminAuditLog
    payment, order = handover.payment, handover.payment.intent.order
    if (handover.status != 'SUPERSEDED' or handover.accepted_at
            or refund.order_id != order.pk
            or not handover.created_at or refund.created_at <= handover.created_at):
        raise ValidationError('待确认接替与退款时序异常')
    mains = list(order.refund_records.filter(created_at__lte=refund.created_at).order_by('created_at', 'pk'))
    from .models import OrderOriginalShareTransfer
    transferred = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
    if (not transferred and len(mains) > 1) or any(item.created_at >= handover.created_at for item in mains):
        raise ValidationError('待确认接替与主单退款时序异常')
    from .models import NegotiationExecution
    if mains or (transferred and NegotiationExecution.objects.filter(zero_order=order,
            termination_processed_at__lt=handover.created_at).exists()):
        if transferred:
            from console.deposit_clearance import _verified_refund_execution
            from .models import OrdinaryTransferFollowupRound, OrderTransferNegotiationEvidence
            from .negotiation_termination import verify_termination_evidence
            from django.db.models import Q
            events = list(NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
                termination_processed_at__isnull=False, termination_processed_at__lt=handover.created_at).order_by('pk'))
            if (not events or {item.refund_id for item in events if item.refund_id} != {item.pk for item in mains}
                    or NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
                        termination_processed_at__gte=handover.created_at,
                        termination_processed_at__lte=refund.created_at).exists()):
                raise ValidationError('待确认接替主单协商时序异常')
            first = events[0]
            rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order,
                execution_id__in=[item.pk for item in events[1:]]).order_by('sequence'))
            from .ordinary_transfer_followup import _hash
            if (len(rounds) != len(events) - 1
                    or [item.sequence for item in rounds] != list(range(1, len(events)))
                    or [item.execution_id for item in rounds] != [item.pk for item in events[1:]]
                    or any(item.previous_id != previous.pk for item, previous in zip(rounds, events))
                    or any(item.facts_hash != _hash(item.proposal_id, item.facts) for item in rounds)):
                raise ValidationError('待确认接替主单协商轮次异常')
            for execution in events:
                if execution.refund_id:
                    if not _verified_refund_execution(execution.refund):
                        raise ValidationError('待确认接替缺少真实转单主单退款')
                    verify_termination_evidence(execution)
                elif execution.pk != first.pk:
                    from .ordinary_transfer_followup_zero import verify_termination
                    verify_termination(execution, lock=False)
            first_evidence = OrderTransferNegotiationEvidence.objects.filter(order=order, execution=first).first()
            if first_evidence:
                if not first.refund_id:
                    from .ordinary_transfer_zero_receipt import verify as verify_first_zero
                    verify_first_zero(first)
                else:
                    from dataclasses import asdict
                    from .negotiation_transfer_evidence import _digest as transfer_digest
                    from .ordinary_transfer_event_scope import segment_lines_at
                    if (first_evidence.snapshot_hash != transfer_digest(first_evidence.snapshot)
                            or first_evidence.snapshot.get('lines') != [asdict(item) for item in
                                segment_lines_at(order, first.created_at)]
                            or first_evidence.snapshot.get('provider_terms') != first.proposal.provider_terms):
                        raise ValidationError('待确认接替原协商分段凭证异常')
            else:
                from .models import OrderOriginalShareTransfer
                transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order)
                transfer = transfer.order_by('created_at', 'pk').first()
                if not transfer:
                    raise ValidationError('待确认接替缺少原始转单')
                if first.refund_id:
                    from .ordinary_positive_pretransfer import verify as verify_first
                else:
                    from .ordinary_zero_pretransfer import verify as verify_first
                verified = verify_first(order, before=transfer.created_at)
                if not verified or verified.pk != first.pk:
                    raise ValidationError('待确认接替原协商与转单不一致')
        else:
            execution = NegotiationExecution.objects.filter(refund=mains[0], termination_processed_at__isnull=False).first()
            if not execution or execution.termination_processed_at > handover.created_at:
                raise ValidationError('待确认接替缺少已完成主单退款')
            from .ordinary_refund_receipt_evidence import verified_shares
            shares = verified_shares(execution)
            if len(shares) != 1 or shares[0].account_id != handover.source_segment.provider_account_id:
                raise ValidationError('待确认接替原负责人不是已终止陪玩')
    previous, parts = current_responsibilities(payment, before=handover.created_at)
    current = [row for row in parts if row['is_current']]
    if ((previous.pk if previous else None) != handover.previous_id
            or len(current) != 1 or current[0]['segment_id'] != handover.source_segment_id):
        raise ValidationError('待确认接替原始负责人异常')
    if handover.version > 1:
        from .models import OrderOriginalShareTransfer
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
            from .ordinary_transfer_handover_history import verify_history
            verify_history(payment, before_version=handover.version, boundary=handover.created_at)
        elif previous and not verify_retry(payment, before_version=handover.version, before=handover.created_at):
            raise ValidationError('待确认接替此前版本异常')
    source = current[0]
    deductions = list(refund.earning_deductions.filter(payment=payment).select_related('participant').order_by('pk'))
    if refund.payment_id != payment.pk:
        # Any share included in the proposal can change, including a retained
        # stint belonging to someone other than the current owner. The refund
        # must identify actual participants and cannot consume more than the
        # historical per-person, per-segment income available at proposal time.
        available = {}
        for part in parts:
            key = (part['provider_id'], part['segment_id'])
            available[key] = available.get(key, 0) + part['income']
        consumed = {}
        for row in deductions:
            key = (row.participant.account_id, row.segment_id)
            if (row.amount <= 0 or row.participant.proposal_id != refund.proposal_id
                    or key not in available):
                raise ValidationError('另一笔退款的逐人接替收益来源异常')
            consumed[key] = consumed.get(key, 0) + row.amount
        if not consumed or any(amount > available[key] for key, amount in consumed.items()):
            raise ValidationError('另一笔退款没有真实改变待确认接替收益')
    rows = list(handover.shares.order_by('position'))
    original = _income(source['basis'], source['commission_snapshot'])
    retained = (_income(handover.retained_basis, source['commission_snapshot'])
                if source['income'] == original else source['income'] * handover.retained_basis // source['basis'])
    expected = [(row['segment_id'], row['basis'], row['income'], row['commission_snapshot'], False)
                for row in parts if not row['is_current']]
    if handover.retained_basis:
        expected.append((handover.source_segment_id, handover.retained_basis, retained, source['commission_snapshot'], False))
    target = next((row for row in rows if row.is_current), None)
    if not target:
        raise ValidationError('待确认接替目标份额缺失')
    try:
        target_commission = _commission(handover.target_segment, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('待确认接替目标抽成异常') from exc
    if target.commission_snapshot != target_commission:
        raise ValidationError('待确认接替目标抽成快照异常')
    expected.append((handover.target_segment_id, handover.transferred_basis,
                     _income(handover.transferred_basis, target.commission_snapshot), target.commission_snapshot, True))
    decisions = list(handover.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
    parties = {(payment.intent.buyer_id, 'BOSS'), (handover.source_segment.provider_account_id, 'PROVIDER'),
               (handover.target_segment.provider_account_id, 'PROVIDER')}
    if (handover.source_basis_before != source['basis'] or not handover.transferred_basis
            or handover.retained_basis + handover.transferred_basis != source['basis']
            or [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current) for row in rows] != expected
            or [row.position for row in rows] != list(range(len(rows)))
            or len(decisions) != len(parties) or {(a, role) for a, role, _, _ in decisions} != parties
            or any(answer is False or ((answer is None) != (at is None)) or (at and at > refund.created_at)
                   for _, _, answer, at in decisions) or all(answer is True for _, _, answer, _ in decisions)):
        raise ValidationError('退款前待确认接替份额或决定异常')
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/handovers/', method='POST', status_code=200))
    body = {'order_id': order.pk, 'payment_id': payment.pk,
            'source_provider': handover.source_segment.provider_account_id,
            'target_provider': handover.target_segment.provider_account_id,
            'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
            'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('待确认接替客服审计异常')
    return {'kind': 'ORDINARY_PENDING_REFUND_V1', 'order_id': order.pk, 'payment_id': payment.pk,
            'handover_id': handover.pk, 'refund_id': refund.pk, 'basis': source['basis'], 'income': source['income'],
            **({'deductions': [[row.pk, row.participant_id, row.segment_id, row.amount] for row in deductions]}
               if refund.payment_id != payment.pk else {}),
            'shares': [[row.pk, row.segment_id, row.position, row.basis, row.income,
                        row.commission_snapshot, row.is_current] for row in rows],
            'decisions': [[a, role, answer, at.isoformat() if at else None] for a, role, answer, at in decisions]}


def verify_retry(payment, *, before_version=None, before=None):
    from rest_framework.exceptions import ValidationError as RestValidationError
    from .models import PointsHandoverRefundInvalidation
    from .points_handover_refund_invalidation import digest
    query = payment.handovers.order_by('version')
    if before_version is not None:
        query = query.filter(version__lt=before_version)
    rows = list(query)
    if not rows:
        return before_version in (None, 1)
    if before_version is not None and len(rows) != before_version - 1:
        return False
    if [row.version for row in rows] != list(range(1, len(rows) + 1)):
        return False
    for index, row in enumerate(rows):
        boundary = rows[index + 1].created_at if index + 1 < len(rows) else before
        if row.status == 'ACCEPTED':
            if not row.accepted_at or (boundary and row.accepted_at >= boundary):
                return False
            try:
                if payment.intent.order.refund_records.filter(created_at__lte=row.accepted_at).exists():
                    from .ordinary_refunded_team_handover import verify as verify_accepted
                else:
                    from .ordinary_team_pre_main_handover import verify as verify_accepted
                verify_accepted(row)
            except (ValidationError, RestValidationError, ValueError, TypeError, ZeroDivisionError):
                return False
            continue
        record = PointsHandoverRefundInvalidation.objects.filter(handover=row).select_related(
            'handover__payment__intent__order', 'handover__source_segment', 'handover__target_segment', 'refund').first()
        if (not record or not isinstance(record.snapshot, dict) or record.snapshot.get('kind') != 'ORDINARY_PENDING_REFUND_V1'
                or record.evidence_hash != digest(record.snapshot) or record.created_at < record.refund.created_at
                or (boundary and record.created_at >= boundary)):
            return False
        try:
            if capture(record.handover, record.refund) != record.snapshot:
                return False
        except (ValidationError, RestValidationError, ValueError, TypeError, ZeroDivisionError):
            return False
    return True
