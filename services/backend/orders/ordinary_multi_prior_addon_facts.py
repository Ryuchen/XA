"""Current-read evidence for add-on refund events before a main refund."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify(order, boundary):
    from console.models import AdminAuditLog
    from engagement.funding_plan import RefundablePart, plan_refund
    from .addon_funding_release import verify_run
    from .addon_payment import verify_receipt
    from .models import OrderAddonFundingReleaseRun, OrderAddonPayment, OrderAddonRefund, OrderPointsFundingAllocation
    from .ordinary_addon_prior_refund import verify_cumulative_sources

    refunds = list(OrderAddonRefund.objects.filter(order=order, created_at__lt=boundary)
                   .select_related('payment__intent', 'proposal__case').order_by('created_at', 'pk'))
    if (not refunds or len({row.created_at for row in refunds}) != len(refunds)
            or OrderAddonRefund.objects.filter(order=order, created_at=boundary).exists()):
        raise ValidationError('普通主单前多笔加购退款时序异常')
    previous_at, earlier = None, []
    for refund in refunds:
        payment, proposal, case = refund.payment, refund.proposal, refund.proposal.case
        if (payment.intent.order_id != order.pk or proposal.payment_id != payment.pk or case.order_id != order.pk
                or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount or refund.amount <= 0
                or (refund.addon_pending_deduction + refund.main_pending_deduction
                    + refund.wallet_deduction + refund.deposit_deduction != refund.amount + refund.rounding_surplus)
                or not proposal.case_request_id or not proposal.accepted_at
                or proposal.created_at < proposal.case_opened_at or proposal.accepted_at > refund.created_at
                or (previous_at and refund.created_at <= previous_at)
                or order.providers.filter(settled_at__lte=refund.created_at).exists()):
            raise ValidationError('普通主单前加购退款责任或时序异常')
        try:
            verify_receipt(payment)
        except RestValidationError as exc:
            raise ValidationError('普通主单前加购付款或退款流水异常') from exc
        buyer_tx = refund.buyer_transaction
        if (not buyer_tx or buyer_tx.wallet.account_id != order.customer_account_id
                or buyer_tx.amount != refund.amount or buyer_tx.tx_type != 'REFUND' or buyer_tx.status != 'SUCCESS'
                or buyer_tx.balance_after != buyer_tx.balance_before + buyer_tx.amount
                or buyer_tx.operator_account_id != refund.operator_id or buyer_tx.operator_id != refund.legacy_operator_id
                or buyer_tx.reversal_of_id or buyer_tx.reversals.exists()):
            raise ValidationError('普通主单前加购退款老板流水异常')
        from .models import OrderAddonHandover, PointsHandoverRefundInvalidation
        from .ordinary_pending_handover_refund import capture as capture_invalidation
        from .points_handover_refund_invalidation import digest as invalidation_digest
        superseded = OrderAddonHandover.objects.filter(payment__intent__order=order,
            status='SUPERSEDED', created_at__lte=refund.created_at)
        for handover in superseded:
            event = PointsHandoverRefundInvalidation.objects.filter(handover=handover).select_related(
                'handover__payment__intent__order', 'handover__source_segment',
                'handover__target_segment', 'refund').first()
            if (not event or event.refund.created_at > refund.created_at
                    or not isinstance(event.snapshot, dict)
                    or event.snapshot.get('kind') != 'ORDINARY_PENDING_REFUND_V1'
                    or event.evidence_hash != invalidation_digest(event.snapshot)
                    or capture_invalidation(event.handover, event.refund) != event.snapshot):
                raise ValidationError('此前失效的普通加购接替凭证异常')
        from .models import OrderAddonHandover
        team_handover = OrderAddonHandover.objects.filter(payment__intent__order=order,
            status='ACCEPTED', accepted_at__lt=refund.created_at).exists()
        if team_handover:
            from .ordinary_pre_main_team_refund import verify as verify_team_refund
            people = verify_team_refund(refund, earlier)
            evidence = getattr(refund, 'zero_wallet_evidence', None)
        else:
            people = list(proposal.participants.all())
            decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
            burden = ((refund.amount + 9) // 10) * 10
            if (len(people) != 1 or people[0].account_id != payment.intent.provider_id
                    or people[0].burden != burden or refund.rounding_surplus != burden - refund.amount
                    or len(decisions) != 2
                    or {(a, role, answer) for a, role, answer, _ in decisions} != {
                        (payment.intent.buyer_id, 'BOSS', True), (payment.intent.provider_id, 'PROVIDER', True)}
                    or any(not at or at < proposal.created_at for _, _, _, at in decisions)
                    or max(at for _, _, _, at in decisions) != proposal.accepted_at):
                raise ValidationError('普通主单前加购退款确认或逐段承担异常')
            previous_basis = sum(row.amount for row in earlier if row.payment_id == payment.pk)
            previous_income = sum(payment.refund_earning_deductions.filter(
                refund__created_at__lt=refund.created_at).values_list('amount', flat=True))
            basis = payment.intent.amount - previous_basis
            income = payment.provider_income - previous_income
            previous_rounding = sum(entry['burden'] - entry['refund_basis'] for old in earlier
                if old.payment_id == payment.pk for participant in old.proposal.participants.all()
                for entry in participant.share_plan if participant.account_id == payment.intent.provider_id
                and entry['segment_id'] == payment.intent.segment_id)
            capacity = max(0, min(basis, basis - previous_rounding))
            if (basis < refund.amount or income < 0 or people[0].basis != basis
                    or people[0].share_plan != [{'segment_id': payment.intent.segment_id, 'basis': basis,
                        'income': income, 'refund_basis': refund.amount, 'burden_capacity': capacity,
                        'burden': min(burden, capacity)}]
                    or people[0].excess_burden != max(burden - capacity, 0)):
                raise ValidationError('普通主单前加购退款的本人剩余份额异常')
            candidates = list(OrderAddonPayment.objects.filter(intent__order=order,
                buyer_transaction__created_at__lt=refund.created_at).select_related('intent', 'buyer_transaction').order_by('pk'))
            candidates.sort(key=lambda item: (item.pk != payment.pk, item.pk))
            remaining, expected_deductions = burden, []
            for addon in candidates:
                prior_handovers = list(addon.handovers.filter(created_at__lte=refund.created_at))
                if prior_handovers:
                    from .models import PointsHandoverRefundInvalidation
                    from .ordinary_pending_handover_refund import capture
                    from .points_handover_refund_invalidation import digest
                    for handover in prior_handovers:
                        event = PointsHandoverRefundInvalidation.objects.filter(handover=handover).select_related(
                            'handover__payment__intent__order', 'handover__source_segment',
                            'handover__target_segment', 'refund').first()
                        if (not event or event.refund.created_at > refund.created_at
                                or not isinstance(event.snapshot, dict)
                                or event.snapshot.get('kind') != 'ORDINARY_PENDING_REFUND_V1'
                                or event.evidence_hash != digest(event.snapshot)
                                or capture(event.handover, event.refund) != event.snapshot):
                            raise ValidationError('普通主单前加购接替尚未接入')
                verify_receipt(addon)
                if addon.intent.provider_id != payment.intent.provider_id:
                    continue
                used = sum(addon.refund_earning_deductions.filter(refund__created_at__lt=refund.created_at)
                           .values_list('amount', flat=True))
                available = addon.provider_income - used
                if available < 0:
                    raise ValidationError('普通主单前加购收益占用异常')
                amount = min(remaining, available)
                if amount:
                    expected_deductions.append((addon.pk, addon.intent.segment_id, amount))
                    remaining -= amount
            if (list(people[0].earning_deductions.order_by('pk').values_list('payment_id', 'segment_id', 'amount'))
                    != expected_deductions or sum(row[2] for row in expected_deductions) != refund.addon_pending_deduction):
                raise ValidationError('普通主单前退款未按本人逐付款收益承担')
            assignments = list(order.providers.filter(provider_account_id=payment.intent.provider_id)[:2])
            if len(assignments) != 1:
                raise ValidationError('普通主单前退款本人履约席位异常')
            assignment = assignments[0]
            from .ordinary_prior_main_income import deduction_at
            expected_parts = deduction_at(order, payment.intent.provider_id, refund.created_at, remaining)
            main_amount = sum(value for _, value in expected_parts)
            main_parts = list(people[0].main_segment_deductions.order_by('pk').values_list('segment_id', 'amount'))
            if refund.main_pending_deduction != main_amount or main_parts != expected_parts:
                raise ValidationError('普通主单前退款未按本人主单收益承担')
            remaining -= main_amount
            wallet_tx = people[0].provider_transaction
            if refund.wallet_deduction:
                if (not wallet_tx or wallet_tx.wallet.account_id != payment.intent.provider_id
                        or wallet_tx.wallet.user_id != assignment.provider_id or wallet_tx.order_id is not None
                        or wallet_tx.tx_type != 'REFUND' or wallet_tx.status != 'SUCCESS'
                        or wallet_tx.amount != -refund.wallet_deduction or wallet_tx.balance_before < 0
                        or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                        or refund.wallet_deduction != min(remaining, wallet_tx.balance_before)
                        or wallet_tx.operator_account_id != refund.operator_id
                        or wallet_tx.operator_id != refund.legacy_operator_id
                        or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()):
                    raise ValidationError('普通主单前退款钱包扣款异常')
            elif wallet_tx:
                raise ValidationError('普通主单前退款存在多余钱包流水')
            remaining -= refund.wallet_deduction
            deficits = list(refund.deposit_deficits.select_related('profile', 'source_store_transaction__wallet__user'))
            if remaining != refund.deposit_deduction or len(deficits) != (1 if remaining else 0):
                raise ValidationError('普通主单前退款押金缺口异常')
            if remaining:
                from .ordinary_refund_receipt_evidence import _verify_direct_deposit
                _verify_direct_deposit(deficits[0], refund=refund, account_id=payment.intent.provider_id,
                                       user_id=assignment.provider_id, amount=remaining, addon=True)
            evidence = getattr(refund, 'zero_wallet_evidence', None)
            if remaining and not refund.wallet_deduction:
                if (not evidence or evidence.account_id != payment.intent.provider_id
                        or evidence.wallet.account_id != payment.intent.provider_id
                        or evidence.wallet.user_id != assignment.provider_id
                        or evidence.business_cycle != deficits[0].account_business_cycle
                        or not evidence.business_cycle or evidence.balance != 0 or not evidence.is_active
                        or evidence.created_at < refund.created_at):
                    raise ValidationError('普通主单前退款零余额钱包事实异常')
            elif evidence:
                raise ValidationError('普通主单前退款存在多余零余额钱包事实')
        sources = list(payment.allocations.order_by('pk'))
        used_sources = {row.pk: 0 for row in sources}
        for previous in earlier:
            if previous.payment_id == payment.pk:
                for entry in previous.proposal.source_plan:
                    if entry['allocation_id'] not in used_sources:
                        raise ValidationError('普通主单前退款来源不属于原付款')
                    used_sources[entry['allocation_id']] += entry['amount_raw']
        planned = plan_refund([RefundablePart(row.pk, row.kind, row.amount, used_sources[row.pk])
                               for row in sources], refund.amount)
        if proposal.source_plan != [{'allocation_id': row.pk, 'kind': row.kind, 'amount_raw': planned[row.pk]}
                                    for row in sources if planned[row.pk]]:
            raise ValidationError('普通主单前加购退款原付款来源异常')
        verify_cumulative_sources(payment, sources)
        entries = [item for item in case.history if isinstance(item, dict)
                   and item.get('created_at') == proposal.case_opened_at.isoformat()
                   and item.get('client_request_id') == proposal.case_request_id] if isinstance(case.history, list) else []
        if (len([item for item in entries if item.get('status') == 'OPEN']) != 1
                or len([item for item in entries if item.get('status') == 'RESOLVED']) != 1
                or any(not set(row.account_id for row in people).issubset(
                       set(item.get('affected_provider_account_ids') or [])) for item in entries)):
            raise ValidationError('普通主单前加购售后凭证异常')
        path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/refund-proposals/'
        proposed = list(AdminAuditLog.objects.filter(resource='addon-refund-proposal', object_id=str(proposal.pk),
            method='POST', path=path, status_code=200))
        posted = list(AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk),
            method='POST', path=f'{path}{proposal.pk}/execute/', status_code=200))
        if (len(proposed) != 1 or proposed[0].operator_account_id != proposal.created_by_id
                or proposed[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                    'amount': refund.amount, 'explanation': proposal.explanation}
                or len(posted) != 1 or posted[0].operator_account_id != refund.operator_id
                or posted[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                    'proposal_id': proposal.pk, 'amount': refund.amount}):
            raise ValidationError('普通主单前加购退款客服审计异常')
        run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
        allocations = list(OrderPointsFundingAllocation.objects.filter(service_order=order,
                           created_at__lt=refund.created_at).order_by('pk'))
        if OrderPointsFundingAllocation.objects.filter(service_order=order, created_at=refund.created_at).exists():
            raise ValidationError('加购退款与补资分配时序无法区分')
        if run:
            empty_prestart_version = bool(not allocations and run.allocation_snapshot == []
                and run.allocated_before == 0 and run.released_amount == 0
                and isinstance(run.ordinary_prestart_snapshot, dict)
                and run.ordinary_prestart_snapshot.get('kind') in
                    ('ORDINARY_PRESTART_ADDON_V1', 'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1'))
            if ((not allocations and not empty_prestart_version) or not run.ordinary_prestart_snapshot or not run.ordinary_prestart_hash
                    or run.created_at <= refund.created_at or run.created_at >= boundary
                    or (evidence and evidence.created_at > run.created_at)
                    or refund.participant_zero_wallet_evidence.filter(created_at__gt=run.created_at).exists()
                    or [row[0] for row in run.allocation_snapshot] != [row.pk for row in allocations]
                    or OrderPointsFundingAllocation.objects.filter(service_order=order,
                        created_at__gte=refund.created_at, created_at__lte=run.created_at).exists()):
                raise ValidationError('普通主单前加购退款资金版本异常')
            try:
                verify_run(run, _skip_usage=True)
            except RestValidationError as exc:
                raise ValidationError('普通主单前加购退款资金版本异常') from exc
            previous_at = run.created_at
        else:
            # The real writer does not create a release version when the
            # locked order has no allocations; later allocations are separate.
            if allocations:
                raise ValidationError('普通主单前加购退款资金版本缺失')
            previous_at = refund.created_at
        earlier.append(refund)
    return refunds
