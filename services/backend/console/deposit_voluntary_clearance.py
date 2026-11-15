"""Read-only verification of committed voluntary add-on handovers."""
from rest_framework.exceptions import ValidationError


def verify_history(payment, *, boundary):
    """Verify real parties, shares and saved funding events without taking write locks."""
    from console.models import AdminAuditLog
    from orders.addon_handover import current_responsibilities, _income
    from orders.addon_intents import _commission
    from orders.addon_offers import AddonSelectionError
    from orders.ordinary_transfer_pre_main_addon import verify_handover_at
    from orders.points_addon_receipt_usage import digest
    from orders.points_funding_allocation import plan_release
    from orders.refunded_addon_revision import digest as funding_digest
    from orders.models import (OrderAddonFundingReleaseRun, OrderPointsFundingAllocation,
                               OrderPointsFundingRelease, OrderPointsTransferFundingRelease,
                               PointsPostRefundTransferRelease, PointsNegotiationPosting)

    events = list(payment.handovers.filter(status='ACCEPTED').select_related(
        'source_segment__attribution', 'target_segment__attribution').order_by('version'))
    if not events:
        raise ValidationError('加购接替事实缺失')
    previous = None
    for event in events:
        if event.kind == 'STANDARD':
            verify_handover_at(payment, event, boundary)
            previous = event
            continue
        if event.kind != 'VOLUNTARY' or not event.accepted_at or not event.created_at < event.accepted_at < boundary:
            raise ValidationError('自愿接替时序异常')
        before, shares = current_responsibilities(payment, before=event.created_at)
        if (event.previous_id != (previous.pk if previous else None)
                or (before.pk if before else None) != event.previous_id):
            raise ValidationError('自愿接替前次版本异常')
        attempts = list(payment.handovers.filter(version__gt=previous.version if previous else 0,
            version__lt=event.version).order_by('version'))
        if ([row.version for row in attempts] != list(range(previous.version + 1 if previous else 1, event.version))
                or any(row.status not in ('REJECTED', 'SUPERSEDED') or row.previous_id != event.previous_id
                       or row.accepted_at for row in attempts)):
            raise ValidationError('自愿接替重试版本异常')
        # The actual rejected/invalidated attempt must have a durable decision
        # or refund invalidation; never infer it from the status alone.
        from orders.points_transfer_followup_handover_scope import _verify_rejected
        from orders.models import PointsHandoverRefundInvalidation
        for attempt in attempts:
            if attempt.status == 'REJECTED':
                _verify_rejected(payment, attempt, before=event.created_at)
            else:
                invalidation = PointsHandoverRefundInvalidation.objects.filter(handover=attempt).first()
                if (not invalidation or not invalidation.created_at or invalidation.created_at >= event.created_at
                        or invalidation.refund.order_id != payment.intent.order_id):
                    raise ValidationError('接替失效退款事实缺失')
                from orders.points_followup_handover_invalidation import capture, digest as invalidation_digest
                actual = capture(attempt, invalidation.refund)
                if (invalidation.snapshot != actual or invalidation.evidence_hash != invalidation_digest(actual)
                        or invalidation.created_at < invalidation.refund.created_at):
                    raise ValidationError('接替失效的实际退款和决定不一致')
        if (payment.refund_records.filter(created_at__gte=event.created_at, created_at__lte=event.accepted_at).exists()
                or payment.refund_earning_deductions.filter(refund__created_at__gte=event.created_at,
                                                            refund__created_at__lte=event.accepted_at).exists()):
            raise ValidationError('接替确认期间责任已变化')
        active = [row for row in shares if row['is_current']]
        source, target = event.source_segment, event.target_segment
        if (len(active) != 1 or active[0]['segment_id'] != source.pk
                or active[0]['basis'] != event.source_basis_before
                or source.attribution.order_id != payment.intent.order_id
                or target.attribution.order_id != payment.intent.order_id
                or source.provider_account_id == target.provider_account_id
                or not source.started_at or not target.started_at
                or source.started_at > event.created_at or target.started_at > event.created_at
                or (source.released_at and source.released_at <= event.accepted_at)
                or (target.released_at and target.released_at <= event.accepted_at)
                or not event.transferred_basis or event.retained_basis + event.transferred_basis != active[0]['basis']
                or not event.remaining_work or (event.retained_basis and not event.completed_work)):
            raise ValidationError('自愿接替履约事实异常')
        decisions = list(event.decisions.order_by('account_id').values_list('account_id', 'role', 'accepted', 'decided_at'))
        required = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                    (target.provider_account_id, 'PROVIDER', True)}
        if (len(decisions) != 3 or {(a, role, yes) for a, role, yes, _ in decisions} != required
                or any(not at or at < event.created_at for _, _, _, at in decisions)
                or max(at for _, _, _, at in decisions) != event.accepted_at):
            raise ValidationError('自愿接替三方确认异常')
        try:
            commission = _commission(target, lock=False)
        except AddonSelectionError as exc:
            raise ValidationError('自愿接替抽成快照异常') from exc
        source_part = active[0]
        expected = [row for row in shares if not row['is_current']]
        if event.retained_basis:
            original = _income(source_part['basis'], source_part['commission_snapshot'])
            retained = (_income(event.retained_basis, source_part['commission_snapshot'])
                        if source_part['income'] == original else
                        source_part['income'] * event.retained_basis // source_part['basis'])
            expected.append({**source_part, 'basis': event.retained_basis, 'income': retained, 'is_current': False})
        expected.append({'segment_id': target.pk, 'basis': event.transferred_basis,
            'income': _income(event.transferred_basis, commission), 'commission_snapshot': commission, 'is_current': True})
        rows = list(event.shares.order_by('position'))
        if ([row.position for row in rows] != list(range(len(rows))) or
                [(r.segment_id, r.basis, r.income, r.commission_snapshot, r.is_current) for r in rows] !=
                [(r['segment_id'], r['basis'], r['income'], r['commission_snapshot'], r['is_current']) for r in expected]):
            raise ValidationError('自愿接替逐段份额异常')
        path = f'/api/admin/orders/{payment.intent.order_id}/addons/payments/{payment.pk}/handovers/'
        audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(event.pk),
            path=path, method='POST', status_code=200))
        body = {'order_id': payment.intent.order_id, 'payment_id': payment.pk,
                'source_provider': source.provider_account_id, 'target_provider': target.provider_account_id,
                'retained_basis': event.retained_basis, 'completed_work': event.completed_work,
                'remaining_work': event.remaining_work}
        if len(audits) != 1 or audits[0].operator_account_id != event.created_by_id or audits[0].request_body != body:
            raise ValidationError('自愿接替客服审计异常')
        run = OrderAddonFundingReleaseRun.objects.filter(handover=event).first()
        if not run or not isinstance(run.refunded_main_snapshot, dict) or not isinstance(run.allocation_snapshot, list):
            raise ValidationError('自愿接替资金版本缺失')
        funding = run.refunded_main_snapshot
        posting = PointsNegotiationPosting.objects.filter(pk=funding.get('posting_id'),
            points_refund__redemption__order_id=payment.intent.order_id).first()
        from orders.grab_points_refunded_facts import points_refund_fact
        main_refunds = points_refund_fact(payment.intent.order, as_of=boundary)
        if (not posting or not event.accepted_at < run.created_at < boundary
                or not main_refunds or posting.points_refund.refund_id not in main_refunds.refund_ids
                or run.refunded_main_hash != funding_digest(run, funding)
                or not isinstance(funding.get('funding'), dict)
                or funding['funding'].get('required_units') != run.required_amount):
            raise ValidationError('自愿接替资金来源异常')
        normalized = []
        for item in run.allocation_snapshot:
            if not isinstance(item, list) or len(item) not in (3, 4, 5, 6):
                raise ValidationError('自愿接替到账分配异常')
            allocation = OrderPointsFundingAllocation.objects.select_related('receipt', 'redemption').filter(pk=item[0]).first()
            if not allocation or allocation.funding_order_id != payment.intent.order_id or allocation.amount != item[1]:
                raise ValidationError('自愿接替到账来源异常')
            from uuid import UUID
            from stores.context import current_store
            from stores.models import PlatformPointsFundingOperation, PlatformPassRepaymentOperation
            from stores.platform_pass_repayment import _claimed_receipt, _sha, RepaymentError
            from orders.models import OrderPointsFundingAction
            from users.models import PassRefundRepaymentAction
            import json
            try:
                key = allocation.request_key
                if not key.startswith('points:') or len(key) != 39:
                    raise ValueError('到账操作编号异常')
                operation = PlatformPointsFundingOperation.objects.using('default').select_related('claim').get(
                    pk=UUID(hex=key[7:]), claim__store__slug=current_store().slug)
                receipt = allocation.receipt
                _claimed_receipt(operation.claim, current_store().slug, receipt)
                action = OrderPointsFundingAction.objects.filter(pk=operation.tenant_action_id,
                    allocation=allocation, request_key=key).first()
                verification = PlatformPassRepaymentOperation.objects.using('default').filter(
                    claim=operation.claim, kind='VERIFY', completed_at__isnull=False,
                    rejected_at__isnull=True, voided_at__isnull=True, tenant_action_id__isnull=False)
                verified = any(PassRefundRepaymentAction.objects.filter(pk=row.tenant_action_id,
                    request_key=f'platform:{row.pk.hex}', receipt=receipt, kind='VERIFY',
                    actor=str(row.actor_id), allocation__isnull=True,
                    result={'receipt_id': receipt.pk, 'verified_by': receipt.verified_by,
                            'verified_at': receipt.verified_at.isoformat() if receipt.verified_at else None}).exists()
                    for row in verification)
                expected_action = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk,
                                   'order_id': payment.intent.order_id, 'amount': allocation.amount}
                if (not operation.completed_at or operation.rejected_at or operation.voided_at
                        or not receipt.verified_at or not receipt.verified_by or not verified or not action
                        or action.actor != str(operation.actor_id) or allocation.allocated_by != str(operation.actor_id)
                        or action.result != expected_action or operation.payload_digest != _sha(json.dumps(
                            [str(operation.claim_id), payment.intent.order_id, allocation.amount], separators=(',', ':')))):
                    raise ValueError('到账核实和分配结果异常')
            except (PlatformPointsFundingOperation.DoesNotExist, RepaymentError, ValueError, TypeError, AttributeError) as exc:
                raise ValidationError('自愿接替到账平台凭证异常') from exc
            from orders.models import OrderAddonFundingRelease
            prior_addon = sum(OrderAddonFundingRelease.objects.filter(allocation=allocation,
                run_id__lt=run.pk).values_list('amount', flat=True))
            if prior_addon != item[2]:
                raise ValidationError('自愿接替此前加购释放异常')
            transfers = list(OrderPointsTransferFundingRelease.objects.filter(allocation=allocation,
                revision__created_at__lt=run.created_at).order_by('pk'))
            mains = list(OrderPointsFundingRelease.objects.filter(allocation=allocation,
                created_at__lt=run.created_at).order_by('pk'))
            posts = list(PointsPostRefundTransferRelease.objects.filter(allocation=allocation,
                revision__created_at__lt=run.created_at).order_by('pk'))
            if ((item[3] if len(item) > 3 else []) != [[r.pk, r.amount] for r in transfers]
                    or (item[4] if len(item) > 4 else []) != [[r.pk, r.amount] for r in mains]
                    or (item[5] if len(item) > 5 else []) != [[r.pk, r.amount] for r in posts]):
                raise ValidationError('自愿接替到账释放历史异常')
            normalized.append([item[0], item[1], item[2] + sum(r.amount for r in transfers + mains + posts)])
        before_amount = sum(item[1] - item[2] for item in normalized)
        expected_release = max(before_amount - run.required_amount, 0)
        release = plan_release(allocations=normalized, amount=expected_release)
        if (run.allocated_before != before_amount or run.released_amount != expected_release
                or [(r.allocation_id, r.amount) for r in run.releases.order_by('-allocation_id')]
                != [(r.allocation_id, r.amount) for r in release]):
            raise ValidationError('自愿接替到账释放异常')
        snapshot = {'kind': 'POINTS_VOLUNTARY_HANDOVER_V1', 'order_id': payment.intent.order_id,
            'payment_id': payment.pk, 'handover_id': event.pk, 'previous_id': event.previous_id,
            'accepted_at': event.accepted_at.isoformat(),
            'decisions': [[a, role, yes, at.isoformat()] for a, role, yes, at in decisions],
            'shares': [[r.pk, r.segment_id, r.basis, r.income, r.commission_snapshot, r.is_current] for r in rows],
            'funding_hash': run.refunded_main_hash, 'allocated_before': run.allocated_before,
            'released_amount': run.released_amount}
        if run.points_handover_snapshot != snapshot or run.points_handover_hash != digest(snapshot):
            raise ValidationError('自愿接替确认快照异常')
        previous = event
    return events
