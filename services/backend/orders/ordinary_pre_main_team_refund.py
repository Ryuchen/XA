"""Current-read proof of an ordinary pre-main add-on refund split by real seats."""
from django.core.exceptions import ValidationError


def verify(refund, earlier):
    from .addon_handover import current_responsibilities
    from .addon_payment import verify_receipt
    from .addon_refund import _allocate_refund_basis, _allocate_segment_burden
    from .models import OrderAddonPayment, OrderParticipationSegment
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    from .ordinary_team_pre_main_handover import verify as verify_handover

    order, payment, proposal = refund.order, refund.payment, refund.proposal
    from .addon_refund_share_allocation import verify as verify_share_allocations
    verify_share_allocations(refund)
    people = list(proposal.participants.order_by('account_id'))
    decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True)} | {(row.account_id, 'PROVIDER', True) for row in people}
    if (not people or len({row.account_id for row in people}) != len(people)
            or len(decisions) != len(expected) or {(a, role, answer) for a, role, answer, _ in decisions} != expected
            or not proposal.accepted_at or any(not at or at < proposal.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != proposal.accepted_at
            or sum(row.burden for row in people) != refund.amount + refund.rounding_surplus):
        raise ValidationError('主单退款前加购退款逐人确认异常')
    _, responsibilities = current_responsibilities(payment, before=refund.created_at, exclude_refund_id=refund.pk)
    own = {person.account_id: [row for row in responsibilities if row['provider_id'] == person.account_id]
           for person in people}
    weights = [(person.account_id, sum(row['basis'] for row in own[person.account_id])) for person in people]
    if any(not basis for _, basis in weights):
        raise ValidationError('加购退款本人历史份额缺失')
    portions = _allocate_refund_basis(refund.amount, weights)
    total = sum(basis for _, basis in weights)
    history = {}
    for old in earlier:
        if old.payment_id != payment.pk:
            continue
        for person in old.proposal.participants.all():
            for item in person.share_plan:
                key = (person.account_id, item['segment_id'])
                history[key] = history.get(key, 0) + item['burden'] - item['refund_basis']
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=refund.created_at).select_related('intent').order_by('pk'))
    payments.sort(key=lambda item: (item.pk != payment.pk, item.pk))
    for addon in payments:
        verify_receipt(addon)
        for handover in addon.handovers.filter(status='ACCEPTED', accepted_at__lt=refund.created_at):
            from .models import OrderOriginalShareTransfer
            if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                    created_at__lt=handover.created_at).exists():
                from .ordinary_transfer_pre_main_addon import verify_handover_at
                verify_handover_at(addon, handover, refund.created_at)
            else:
                verify_handover(handover)
    legacy = getattr(refund, 'zero_wallet_evidence', None)
    witnesses = list(refund.participant_zero_wallet_evidence.select_related('wallet').order_by('participant_id'))
    by_person = {row.participant_id: row for row in witnesses}
    used_witnesses = set()
    if legacy and witnesses:
        raise ValidationError('加购退款零余额凭证重复')
    for person in people:
        rows = own[person.account_id]
        basis = sum(row['basis'] for row in rows)
        burden = ((refund.amount * basis + total * 10 - 1) // (total * 10)) * 10
        grouped = {}
        for row in rows:
            if not row['basis']:
                continue
            segment = grouped.setdefault(row['segment_id'], {'basis': 0, 'income': 0})
            segment['basis'] += row['basis']
            segment['income'] += row['income']
        parts = _allocate_refund_basis(portions[person.account_id],
                                       [(segment_id, row['basis']) for segment_id, row in sorted(grouped.items())])
        entries = []
        for segment_id, row in sorted(grouped.items()):
            capacity = max(0, min(row['basis'], row['basis'] - history.get((person.account_id, segment_id), 0)))
            entries.append({'segment_id': segment_id, 'basis': row['basis'], 'income': row['income'],
                            'refund_basis': parts[segment_id], 'burden_capacity': capacity})
        latest = list(OrderParticipationSegment.objects.filter(pk__in=parts)
                      .order_by('-started_at', '-accepted_at', '-pk').values_list('pk', flat=True))
        excess = _allocate_segment_burden(entries, burden, latest)
        if (person.basis != basis or person.burden != burden or person.excess_burden != excess
                or person.share_plan != entries):
            raise ValidationError('主单退款前加购退款逐段份额异常')
        remaining, deductions = burden, []
        for addon in payments:
            _, addon_rows = current_responsibilities(addon, before=refund.created_at, exclude_refund_id=refund.pk)
            grouped = {}
            for row in addon_rows:
                if row['provider_id'] == person.account_id:
                    grouped[row['segment_id']] = grouped.get(row['segment_id'], 0) + row['income']
            for segment_id, amount in sorted(grouped.items()):
                take = min(remaining, amount)
                if take:
                    deductions.append((addon.pk, segment_id, take))
                    remaining -= take
        if (list(person.earning_deductions.order_by('pk').values_list('payment_id', 'segment_id', 'amount')) != deductions
                or sum(row[2] for row in deductions) != person.addon_pending_deduction):
            raise ValidationError('主单退款前加购逐段收益扣减异常')
        assignments = list(order.providers.filter(provider_account_id=person.account_id)[:2])
        if len(assignments) != 1:
            raise ValidationError('主单退款前陪玩身份异常')
        assignment = assignments[0]
        from .ordinary_prior_main_income import deduction_at
        expected_segments = deduction_at(order, person.account_id, refund.created_at, remaining)
        main = sum(value for _, value in expected_segments)
        segments = list(person.main_segment_deductions.order_by('pk').values_list('segment_id', 'amount'))
        if person.main_pending_deduction != main or segments != expected_segments:
            raise ValidationError('主单退款前本人逐段主单承担异常')
        remaining -= main
        tx = person.provider_transaction
        if person.wallet_deduction:
            if (not tx or tx.wallet.account_id != person.account_id or tx.wallet.user_id != assignment.provider_id
                    or tx.order_id is not None or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                    or tx.amount != -person.wallet_deduction or tx.balance_before < 0
                    or tx.balance_after != tx.balance_before + tx.amount
                    or person.wallet_deduction != min(remaining, tx.balance_before)
                    or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id
                    or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('主单退款前陪玩钱包扣款异常')
        elif tx:
            raise ValidationError('主单退款前存在多余钱包扣款')
        remaining -= person.wallet_deduction
        deficits = list(refund.deposit_deficits.filter(account_id=person.account_id)
                        .select_related('profile', 'source_store_transaction__wallet__user'))
        if remaining != person.deposit_deduction or len(deficits) != (1 if remaining else 0):
            raise ValidationError('主单退款前陪玩押金承担异常')
        if remaining:
            _verify_direct_deposit(deficits[0], refund=refund, account_id=person.account_id,
                                   user_id=assignment.provider_id, amount=remaining, addon=True)
        if remaining and not person.wallet_deduction:
            witness = by_person.get(person.pk) if witnesses else legacy
            if (not witness or witness.account_id != person.account_id or witness.wallet.account_id != person.account_id
                    or witness.wallet.user_id != assignment.provider_id or not witness.business_cycle
                    or witness.business_cycle != deficits[0].account_business_cycle or witness.balance != 0
                    or not witness.is_active or witness.created_at < refund.created_at):
                raise ValidationError('主单退款前零余额钱包事实异常')
            used_witnesses.add(person.pk)
    if (witnesses and set(by_person) != used_witnesses) or (legacy and not used_witnesses):
        raise ValidationError('主单退款前存在多余零余额凭证')
    return people
