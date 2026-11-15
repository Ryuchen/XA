"""Internal third-refund posting and segment closure in one savepoint.

This is not a public endpoint: final settlement and completed-operation replay
must be connected before the capability is enabled.
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def execute_locked(case_id, proposal_id, *, operator, legacy_operator, request_id):
    from .points_transfer_refund_access import permits
    if not transaction.get_connection().in_atomic_block or not permits(phase='refund'):
        raise ValidationError('第三轮退款必须在受控订单事务中执行')
    with transaction.atomic():
        return _execute_locked(case_id, proposal_id, operator=operator,
                               legacy_operator=legacy_operator, request_id=request_id)


def _execute_locked(case_id, proposal_id, *, operator, legacy_operator, request_id, _round=False):
    from .financial_lock import lock_order_money
    from .models import Order, OrderParticipationSegment, PointsNegotiationPosting
    from .negotiation_proposals import _lock_case
    from .points_third_refund_execution import post_locked
    from engagement.models import ConsumptionConfirmation, PointLedger

    order, case = _lock_case(case_id)
    lock_order_money()
    existing = PointsNegotiationPosting.objects.select_for_update().filter(proposal_id=proposal_id,
        proposal__case=case).first()
    if existing:
        if _round:
            from .points_round_refund_termination import verify_completed_locked
            return verify_completed_locked(order, case, existing, operator=operator,
                                           legacy_operator=legacy_operator, request_id=request_id)
        return _verify_completed_locked(order, case, existing, operator=operator,
                                        legacy_operator=legacy_operator, request_id=request_id)
    if (order.status not in (Order.Status.IN_SERVICE, Order.Status.COMPLETED)
            or order.settled_at or order.settlement_mode != Order.SettlementMode.REPORT_APPROVAL
            or order.consumption_confirmed_at or order.funding_benefits_resolved_at
            or ConsumptionConfirmation.objects.select_for_update().filter(order=order).exists()
            or PointLedger.objects.select_for_update().filter(order=order, kind='CONSUMPTION',
                                                               event_key=f'order:{order.pk}:points').exists()):
        raise ValidationError('第三轮退款的订单或会员状态待核对')
    if _round:
        from .points_third_refund_execution import _post_locked
        posting = _post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator,
                               request_id=request_id, _round=True)
    else:
        posting = post_locked(case_id, proposal_id, operator=operator, legacy_operator=legacy_operator,
                              request_id=request_id)
    proposal, proof = posting.proposal, posting.points_refund
    if (not proposal.accepted_at or not proposal.case_opened_at
            or proposal.accepted_at < proposal.case_opened_at or proposal.accepted_at > timezone.now()
            or (order.completed_at and proposal.accepted_at < order.completed_at)):
        raise ValidationError('第三轮退款协商时间或全额退款范围待核对')
    in_service = order.status == Order.Status.IN_SERVICE
    completed_by_negotiation = False
    if in_service:
        from .services import ensure_fulfillment_reports, refresh_escort_status
        ensure_fulfillment_reports(order)
        selected = proposal.affected_segment_ids
        shares = list(posting.shares.select_related('assignment').order_by('account_id'))
        if (not isinstance(selected, list) or not selected or len(set(selected)) != len(selected)
                or not shares or (not _round and (len(selected) != 1 or len(shares) != 1))):
            raise ValidationError('退款须明确选择本人履约段')
        segments = list(OrderParticipationSegment.objects.select_for_update().filter(pk__in=selected,
            attribution__order=order, released_at__isnull=True, started_at__isnull=False))
        active = list(order.providers.select_for_update().filter(released_at__isnull=True).order_by('pk'))
        accounts = {share.account_id for share in shares}
        if (len(segments) != len(selected) or {segment.provider_account_id for segment in segments} != accounts
                or len(accounts) != len(shares)
                or any(share.assignment not in active or share.assignment.settled_at for share in shares)):
            raise ValidationError('第三轮退款履约席位已变化')
        for segment in segments:
            segment.released_at = posting.accepted_at
            segment.save(update_fields=['released_at'])
        for share in shares:
            share.assignment.released_at = posting.accepted_at
            share.assignment.save(update_fields=['released_at'])
        remaining = [item for item in active if item.pk not in {share.assignment_id for share in shares}]
        if remaining and order.provider_account_id in accounts:
            replacement = remaining[0]
            order.provider_id, order.provider_account_id = replacement.provider_id, replacement.provider_account_id
            order.provider_name_snapshot = replacement.provider_name_snapshot
            order.save(update_fields=['provider', 'provider_account', 'provider_name_snapshot', 'updated_at'])
        from .room_completion_review import invalidate_for_transfer_locked
        invalidate_for_transfer_locked(order, operator, reason='NEGOTIATED_TERMINATION')
        if remaining:
            from .state_machine import log_only
            log_only(order, action='REFUND', operator=legacy_operator, operator_account=operator,
                     reason='第三轮协商退款，受影响陪玩终止履约')
        else:
            from .state_machine import transition
            def finish(current):
                current.completed_at = posting.accepted_at
            order = transition(order.pk, Order.Status.COMPLETED, operator=legacy_operator,
                operator_account=operator, action='COMPLETE', reason='双方确认第三轮协商终止',
                side_effect=finish, update_fields=['completed_at'], room_points_negotiation_posting_id=posting.pk)
            completed_by_negotiation = True
        refresh_escort_status(provider_ids=[share.assignment.provider_id for share in shares],
                              account_ids=[share.account_id for share in shares],
                              exclude_order_ids=[order.pk])
    order.payment_status = order.PaymentStatus.REFUNDED if proof.is_final else order.PaymentStatus.PARTIALLY_REFUNDED
    order.refunded_at = timezone.now()
    order.save(update_fields=['payment_status', 'refunded_at', 'updated_at'])
    refund = proof.refund
    refund.benefit_effects = {('points_round_refund_termination_version' if _round
                               else 'points_third_refund_termination_version'): 1}
    if proof.is_final:
        from coupons.benefits import restore_order_coupons
        from coupons.models import UserCoupon
        coupons = list(UserCoupon.objects.select_for_update().filter(order=order, status=UserCoupon.Status.USED)
                       .select_related('coupon').order_by('pk'))
        refund.benefit_effects['restored_coupon_count'] = restore_order_coupons(order)
        refund.benefit_effects['order_coupon_results'] = [
            {'name': row.coupon.name, 'outcome': 'RETURNED' if row.restore_on_refund else 'NOT_RETURNED'}
            for row in coupons]
    refund.save(update_fields=['benefit_effects'])
    from .aftersale import append_history, notify_aftersale_reports, schedule_aftersale_notices
    case.status = 'REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED'
    case.operator, case.resolution, case.resolved_at = operator, proposal.explanation, timezone.now()
    append_history(case)
    from wallet.income_priority_evidence import attach_closure
    attach_closure(case)
    case.save(update_fields=['status', 'operator', 'resolution', 'resolved_at', 'history'])
    posting.termination_processed_at = timezone.now()
    posting.termination_in_service = in_service
    posting.completed_by_negotiation = completed_by_negotiation
    posting.save(update_fields=['termination_processed_at', 'termination_in_service', 'completed_by_negotiation'])
    if _round:
        from .points_third_target_invalidation import record_termination_locked
        record_termination_locked(order, posting)
    from console.models import AdminAuditLog
    AdminAuditLog.objects.create(operator=legacy_operator, operator_account=operator,
        operator_name=(operator.nickname or operator.username)[:150], method='POST',
        path=f'/api/admin/aftersales/{case.pk}/negotiation-execution/', resource='aftersales', object_id=str(case.pk),
        request_body={'proposal_id': proposal.pk, 'refund_id': refund.pk, 'refund_units': refund.amount,
            'refund_equivalent_units': proof.equivalent_units, 'points_hundredths': proof.points_hundredths,
            'client_request_id': request_id}, status_code=200)
    from .notifier import notify_order_update
    transaction.on_commit(lambda: notify_order_update(order))
    transaction.on_commit(lambda: notify_aftersale_reports(order))
    schedule_aftersale_notices(order, case, resolved=True)
    if _round:
        from .points_round_refund_termination import verify_completed_locked
        return verify_completed_locked(order, case, posting, operator=operator,
                                       legacy_operator=legacy_operator, request_id=request_id)
    return _verify_completed_locked(order, case, posting, operator=operator,
                                    legacy_operator=legacy_operator, request_id=request_id)


def _verify_completed_locked(order, case, posting, *, operator, legacy_operator, request_id, allow_later=False):
    """Read the actual completed request without posting money a second time."""
    from .models import PointsRefundRoundEvidence
    from .points_refund_round_evidence import _digest
    from .points_third_refund_release import record_locked as verify_release
    proof, proposal = posting.points_refund, posting.proposal
    refund = proof.refund
    version = PointsRefundRoundEvidence.objects.select_for_update().select_related(
        'transfer_revision__transfer__next_segment').filter(order=order, posting=posting).first()
    if (not version or not isinstance(version.snapshot, dict) or version.evidence_hash != _digest(version.snapshot)
            or posting.operator_id != operator.pk or refund.operator_id != getattr(legacy_operator, 'pk', None)
            or refund.client_request_id != request_id or not posting.termination_processed_at
            or not proposal.case_opened_at
            or (order.refund_records.count() < 3 if allow_later else order.refund_records.count() != 3)
            or (not allow_later and order.payment_status != (order.PaymentStatus.REFUNDED if proof.is_final else order.PaymentStatus.PARTIALLY_REFUNDED))
            or (allow_later and order.refund_records.count() > 3 and proof.is_final)
            or not isinstance(refund.benefit_effects, dict)
            or refund.benefit_effects.get('points_third_refund_termination_version') != 1
            or (proof.is_final and (not isinstance(refund.benefit_effects.get('order_coupon_results'), list)
                or refund.benefit_effects.get('restored_coupon_count') != sum(
                    item.get('outcome') == 'RETURNED' for item in refund.benefit_effects['order_coupon_results']
                    if isinstance(item, dict))))):
        raise ValidationError('第三轮已执行请求或结案凭证不一致')
    history = case.history if isinstance(case.history, list) else []
    matches = [entry for entry in history if isinstance(entry, dict)
        and entry.get('created_at') == proposal.case_opened_at.isoformat()
        and entry.get('client_request_id') == proposal.case_request_id
        and entry.get('status') == ('REFUNDED' if proof.is_final else 'PARTIALLY_REFUNDED')
        and entry.get('operator_id') == operator.pk and entry.get('resolution') == proposal.explanation
        and entry.get('affected_provider_account_ids') == proposal.affected_accounts
        and entry.get('affected_segment_ids') == proposal.affected_segment_ids]
    rounds = version.snapshot.get('rounds')
    if (len(matches) != 1 or not matches[0].get('resolved_at') or not isinstance(rounds, list) or not rounds
            or rounds[-1].get('posting_id') != posting.pk):
        raise ValidationError('第三轮售后结案历史待核对')
    source = rounds[-1].get('release_source')
    if not isinstance(source, list) or len(source) != 2 or source[0] != 'current' or not isinstance(source[1], dict):
        raise ValidationError('第三轮退款释放版本异常')
    target = version.transfer_revision.transfer.next_segment
    if (posting.termination_in_service and target.released_at != posting.accepted_at):
        raise ValidationError('第三轮退款终止时间不一致')
    saved = source[1]
    shares = list(posting.shares.order_by('pk'))
    if len(shares) != 1 or shares[0].account_id != target.provider_account_id:
        raise ValidationError('第三轮退款本人承担记录异常')
    calculation = {'order_id': order.pk, 'case_id': case.pk, 'prior_version_id': version.transfer_revision_id,
        'segment_id': target.pk, 'account_id': target.provider_account_id, 'wallet_units': shares[0].wallet_deduction,
        'deposit_units': shares[0].deposit_deduction, 'shortfall_units': 0, 'allocations': saved['allocations'],
        'releases': saved['releases'], 'funding': saved['funding']}
    if allow_later and order.refund_records.count() > 3:
        latest = PointsRefundRoundEvidence.objects.filter(order=order).order_by('-posting__created_at', '-pk').first()
        from .points_round_receipt_usage import verify_locked
        if not latest or version.pk not in [row.pk for row in _prior_versions(latest)]:
            raise ValidationError('第三轮退款未包含在后续真实版本链中')
        verify_locked(latest)
    else:
        verify_release(order, posting, version.transfer_revision, calculation)
    return posting


def _prior_versions(version):
    seen, result = set(), []
    while version:
        if version.pk in seen:
            raise ValidationError('积分退款版本前链循环')
        seen.add(version.pk)
        result.append(version)
        version = version.previous
    return result
