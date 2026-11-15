"""Receipt-local event-time facts for one transferred add-on refund."""
from dataclasses import asdict

from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as RestValidationError


def verify_handover_at(payment, handover, boundary):
    from console.models import AdminAuditLog
    from .addon_handover import _income
    from .addon_handover import current_responsibilities
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    source, target = handover.source_segment, handover.target_segment
    if handover.previous_id:
        return verify_later_handover_at(payment, handover, boundary)
    if (handover.status != 'ACCEPTED'
            or not handover.created_at or not handover.accepted_at or handover.accepted_at >= boundary
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                                              created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                                              refund__created_at__lte=handover.accepted_at).exists()
            or source.pk != payment.intent.segment_id or source.attribution.order_id != payment.intent.order_id
            or target.attribution.order_id != payment.intent.order_id
            or source.provider_account_id == target.provider_account_id
            or not handover.transferred_basis or not handover.remaining_work
            or (handover.retained_basis and not handover.completed_work)
            or (handover.retained_basis and not source.started_at)
            or not target.started_at or target.started_at > handover.created_at
            or (target.released_at and target.released_at < handover.accepted_at)):
        raise ValidationError('转单加购接替历史异常')
    if handover.version > 1:
        from .ordinary_transfer_handover_history import verify_history
        verify_history(payment, before_version=handover.version, boundary=handover.created_at)
    previous, parts = current_responsibilities(payment, before=handover.created_at)
    active = [item for item in parts if item['is_current']]
    if (previous or len(active) != 1 or active[0]['segment_id'] != source.pk
            or active[0]['basis'] != handover.source_basis_before
            or handover.retained_basis + handover.transferred_basis != active[0]['basis']):
        raise ValidationError('转单加购首次接替的退款后份额异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, accepted) for a, role, accepted, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('转单加购接替三方确认异常')
    try:
        commission = _commission(target, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('转单加购接替抽成异常') from exc
    rows = []
    if handover.retained_basis:
        original = _income(active[0]['basis'], active[0]['commission_snapshot'])
        retained = (_income(handover.retained_basis, active[0]['commission_snapshot'])
                    if active[0]['income'] == original else
                    active[0]['income'] * handover.retained_basis // active[0]['basis'])
        rows.append((source.pk, handover.retained_basis,
                     retained, active[0]['commission_snapshot'], False))
    rows.append((target.pk, handover.transferred_basis, _income(handover.transferred_basis, commission), commission, True))
    actual = list(handover.shares.order_by('position'))
    if ([row.position for row in actual] != list(range(len(rows)))
            or [(row.segment_id, row.basis, row.income, row.commission_snapshot, row.is_current)
                for row in actual] != rows):
        raise ValidationError('转单加购接替分段收益异常')
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{payment.intent.order_id}/addons/payments/{payment.pk}/handovers/',
        method='POST', status_code=200))
    body = {'order_id': payment.intent.order_id, 'payment_id': payment.pk,
            'source_provider': source.provider_account_id, 'target_provider': target.provider_account_id,
            'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
            'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('转单加购接替审计异常')


def verify_later_handover_at(payment, handover, boundary):
    from console.models import AdminAuditLog
    from .addon_handover import current_responsibilities, _income
    from .addon_intents import _commission
    from .addon_offers import AddonSelectionError
    previous, shares = current_responsibilities(payment, before=handover.created_at)
    if (handover.status != 'ACCEPTED' or not previous
            or previous.pk != handover.previous_id or handover.version <= previous.version
            or not handover.accepted_at or handover.accepted_at >= boundary
            or previous.accepted_at >= handover.created_at
            or payment.refund_records.filter(created_at__gte=handover.created_at,
                                             created_at__lte=handover.accepted_at).exists()
            or payment.refund_earning_deductions.filter(refund__created_at__gte=handover.created_at,
                                             refund__created_at__lte=handover.accepted_at).exists()):
        raise ValidationError('后续转单加购接替时序异常')
    from .ordinary_transfer_handover_history import verify_history
    verify_history(payment, before_version=handover.version, boundary=handover.created_at)
    active = [row for row in shares if row['is_current']]
    source, target = handover.source_segment, handover.target_segment
    if (len(active) != 1 or active[0]['segment_id'] != source.pk or active[0]['basis'] != handover.source_basis_before
            or source.attribution.order_id != payment.intent.order_id or target.attribution.order_id != payment.intent.order_id
            or source.provider_account_id == target.provider_account_id
            or not target.started_at or target.started_at > handover.created_at
            or (target.released_at and target.released_at < handover.accepted_at)
            or not handover.transferred_basis or handover.retained_basis + handover.transferred_basis != active[0]['basis']
            or not handover.remaining_work or (handover.retained_basis and not handover.completed_work)):
        raise ValidationError('后续转单加购接替份额异常')
    decisions = list(handover.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
    expected = {(payment.intent.buyer_id, 'BOSS', True), (source.provider_account_id, 'PROVIDER', True),
                (target.provider_account_id, 'PROVIDER', True)}
    if (len(decisions) != 3 or {(a, role, answer) for a, role, answer, _ in decisions} != expected
            or any(not at or at < handover.created_at for _, _, _, at in decisions)
            or max(at for _, _, _, at in decisions) != handover.accepted_at):
        raise ValidationError('后续转单加购接替三方确认异常')
    try:
        commission = _commission(target, allow_refund_review=True, lock=False)
    except AddonSelectionError as exc:
        raise ValidationError('后续转单加购接替抽成异常') from exc
    source_row = active[0]
    expected_rows = [(item['segment_id'], item['basis'], item['income'], item['commission_snapshot'], False)
                     for item in shares if not item['is_current']]
    if handover.retained_basis:
        full = _income(source_row['basis'], source_row['commission_snapshot'])
        retained = (_income(handover.retained_basis, source_row['commission_snapshot']) if source_row['income'] == full
                    else source_row['income'] * handover.retained_basis // source_row['basis'])
        expected_rows.append((source.pk, handover.retained_basis, retained, source_row['commission_snapshot'], False))
    expected_rows.append((target.pk, handover.transferred_basis, _income(handover.transferred_basis, commission), commission, True))
    actual = list(handover.shares.order_by('position'))
    if ([item.position for item in actual] != list(range(len(expected_rows)))
            or [(item.segment_id, item.basis, item.income, item.commission_snapshot, item.is_current)
                for item in actual] != expected_rows):
        raise ValidationError('后续转单加购接替收益异常')
    audits = list(AdminAuditLog.objects.filter(resource='addon-handover', object_id=str(handover.pk),
        path=f'/api/admin/orders/{payment.intent.order_id}/addons/payments/{payment.pk}/handovers/', method='POST', status_code=200))
    body = {'order_id': payment.intent.order_id, 'payment_id': payment.pk,
        'source_provider': source.provider_account_id, 'target_provider': target.provider_account_id,
        'retained_basis': handover.retained_basis, 'completed_work': handover.completed_work,
        'remaining_work': handover.remaining_work}
    if len(audits) != 1 or audits[0].operator_account_id != handover.created_by_id or audits[0].request_body != body:
        raise ValidationError('后续转单加购接替审计异常')


def capture(run):
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from wallet.models import Transaction
    from .addon_funding_history import combined_at
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from .models import OrderAddonPayment, OrderOriginalShareTransfer, OrderSegmentFinancialSnapshot
    from .ordinary_refund_receipt_evidence import _verify_direct_deposit
    from .points_funding_plan import plan_service_funding
    from .ordinary_transfer_event_scope import segment_lines_at
    from .settlement import ProviderShare, SettlementError

    refund, event_payment, event_handover = run.refund, run.payment, run.handover
    if sum(bool(item) for item in (refund, event_payment, event_handover)) != 1 or not run.created_at:
        raise ValidationError('转单加购资金事件不完整')
    payment = refund.payment if refund else event_payment if event_payment else event_handover.payment
    order = payment.intent.order
    event_at = refund.created_at if refund else payment.buyer_transaction.created_at if event_payment else event_handover.accepted_at
    if event_at >= run.created_at:
        raise ValidationError('转单加购资金事件时序异常')
    from .ordinary_transfer_event_scope import transfers_at
    transfers = transfers_at(order, event_at)
    from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
    verify_pretransfer_zero(order, before=min(item.created_at for item in transfers))
    payments = list(OrderAddonPayment.objects.filter(intent__order=order,
        buyer_transaction__created_at__lt=run.created_at).select_related('intent', 'buyer_transaction'))
    refunds = list(order.addon_refunds.filter(created_at__lt=run.created_at).order_by('created_at', 'pk'))
    if (not payments or payment.pk not in {item.pk for item in payments}
            or (refund and (not refunds or refunds[-1].pk != refund.pk))
            or (not refund and any(row.created_at >= event_at for row in refunds))
            or len({row.created_at for row in refunds}) != len(refunds)
            or order.refund_records.filter(created_at__lte=run.created_at).exists()
            or OrderPointAllocation.objects.filter(order=order).exists()):
        raise ValidationError('转单加购退款历史范围尚未接通')
    try:
        segment_lines = segment_lines_at(order, run.created_at)
        for payment in payments:
            verify_receipt(payment)
    except (SettlementError, RestValidationError) as exc:
        raise ValidationError('转单加购付款或逐段事实异常') from exc
    for payment in payments:
        handovers = list(payment.handovers.filter(status='ACCEPTED', accepted_at__lt=run.created_at))
        if payment.handovers.filter(status='PENDING', created_at__lt=run.created_at).exists():
            raise ValidationError('转单加购接替历史尚未接通')
        for handover in handovers:
            verify_handover_at(payment, handover, run.created_at)
    for item in refunds:
        verify_shares(item)
        people = list(item.proposal.participants.select_related('provider_transaction__wallet'))
        decisions = list(item.proposal.decisions.all())
        expected = {(item.payment.intent.buyer_id, 'BOSS')} | {(row.account_id, 'PROVIDER') for row in people}
        total = sum(row.basis for row in people)
        if (not total or len(decisions) != len(expected)
                or {(row.account_id, row.role) for row in decisions} != expected
                or any(row.accepted is not True or not row.decided_at for row in decisions)
                or max(row.decided_at for row in decisions) != item.proposal.accepted_at
                or item.proposal.accepted_at > item.created_at
                or any(row.burden != ((item.amount * row.basis + total * 10 - 1) // (total * 10)) * 10 for row in people)
                or sum(row.burden for row in people) != item.amount + item.rounding_surplus):
            raise ValidationError('转单加购退款逐人确认或取整异常')
        for person in people:
            tx = person.provider_transaction
            if bool(tx) != bool(person.wallet_deduction):
                raise ValidationError('加购退款钱包流水缺失')
            if tx and (tx.wallet.account_id != person.account_id or tx.amount != -person.wallet_deduction
                    or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS'
                    or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('加购退款钱包流水异常')
            deficits = list(item.deposit_deficits.filter(account_id=person.account_id)
                            .select_related('profile', 'source_store_transaction__wallet__user'))
            if len(deficits) != (1 if person.deposit_deduction else 0):
                raise ValidationError('加购退款押金凭证异常')
            if deficits:
                _verify_direct_deposit(deficits[0], refund=item, account_id=person.account_id,
                    user_id=deficits[0].profile.user_id, amount=person.deposit_deduction, addon=True)
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    try:
        validate_order_funding_sources(order, sources)
    except RestValidationError as exc:
        raise ValidationError('主单付款来源异常') from exc
    paid = list(Transaction.objects.filter(order=order, tx_type='PAY', status='SUCCESS').select_related('wallet'))
    if (sum(row.amount for row in sources) != order.amount or len(paid) != 1
            or paid[0].amount != -order.amount or paid[0].wallet.account_id != order.customer_account_id
            or paid[0].balance_after != paid[0].balance_before + paid[0].amount
            or paid[0].reversal_of_id or paid[0].reversals.exists()):
        raise ValidationError('主单付款流水异常')
    snaps = {row.segment_id: row for row in OrderSegmentFinancialSnapshot.objects.filter(segment__attribution__order=order)}
    shares = [ProviderShare(line.basis, snaps[line.segment_id].commission_type,
        snaps[line.segment_id].commission_rate, line.fee if snaps[line.segment_id].commission_type == 'FIXED' else 0,
        line.income) for line in segment_lines]
    main = plan_service_funding(deal_units=order.amount, point_discount_units=0, shares=shares,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        principal_units=sum(row.amount for row in sources if row.kind == 'PREPAID'),
        gift_units=sum(row.amount for row in sources if row.kind == 'GIFT'),
        compensation_units=sum(row.amount for row in sources if row.kind == 'COMPENSATION'))
    lines = [(row.segment_id, row.account_id, row.basis, row.fee, row.income) for row in segment_lines]
    historical = combined_at(order, main, run.created_at, segment_shares=lines)
    margin = historical['store_income_units'] - historical['additional_store_funding_units']
    required = max(sum(item.rounding_surplus for item in refunds) - margin, 0)
    historical['additional_store_funding_units'] = required
    historical['store_income_units'] = margin + required
    if required != run.required_amount:
        raise ValidationError('转单加购退款资金需求异常')
    if refund:
        return {'kind': 'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1', 'order_id': order.pk,
            'refund_id': refund.pk, 'transfer_id': transfers[-1].pk, 'boundary': run.created_at.isoformat(),
            'lines': [list(row) for row in lines], 'main': asdict(main), 'funding': historical}
    if event_handover:
        return {'kind': 'ORDINARY_TRANSFER_PRE_MAIN_HANDOVER_V1', 'order_id': order.pk,
            'handover_id': event_handover.pk, 'transfer_id': transfers[-1].pk, 'boundary': run.created_at.isoformat(),
            'lines': [list(row) for row in lines], 'main': asdict(main), 'funding': historical}
    return {'kind': 'ORDINARY_TRANSFER_PRE_MAIN_PAYMENT_V1', 'order_id': order.pk,
        'payment_id': event_payment.pk, 'transfer_id': transfers[-1].pk, 'boundary': run.created_at.isoformat(),
        'lines': [list(row) for row in lines], 'main': asdict(main), 'funding': historical}
