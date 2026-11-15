"""Identify the continuing participant after an initial zero termination."""
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def terminated_before_transfer(order):
    """Return the real closed teammate for the zero-then-transfer slice."""
    from django.conf import settings
    from django.utils import timezone
    from .models import NegotiationExecution, OrderOriginalShareTransfer
    from .negotiation_zero import marked_execution_id
    from .ordinary_zero_followup_capability import eligible as zero_eligible
    if not getattr(settings, 'ORDINARY_ZERO_POST_ADDON_MYSQL_ENABLED', False):
        return None
    zero_id = marked_execution_id(order)
    if not zero_id:
        return None
    transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    if transfer:
        from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
        execution = verify_pretransfer_zero(order, before=transfer.created_at)
        if not execution:
            return None
        return execution.proposal.affected_accounts[0]
    if not zero_eligible(order):
        return None
    execution = NegotiationExecution.objects.filter(pk=zero_id, zero_order=order).select_related('zero_order', 'proposal').first()
    if not execution:
        return None
    from .ordinary_zero_followup_evidence import verified_first
    return verified_first(execution, boundary=timezone.now())[0].account_id


def handover_permitted(order, payment):
    """A's original add-on may pass to the one still-serving teammate."""
    from django.conf import settings
    from django.utils import timezone
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .ordinary_zero_followup_capability import eligible as zero_eligible
    from .negotiation_zero import marked_execution_id
    from .ordinary_zero_followup_evidence import verified_first
    from .models import NegotiationExecution, OrderAddonHandover

    if (not getattr(settings, 'ORDINARY_ZERO_POST_ADDON_MYSQL_ENABLED', False)
            or not zero_eligible(order, allow_pending=True) or order.status != order.Status.IN_SERVICE
            or order.settled_at or order.providers.filter(settled_at__isnull=False).exists()
            or not payment or payment.intent.order_id != order.pk
            or OrderAddonHandover.objects.filter(payment__intent__order=order, status='PENDING')
                .exclude(payment=payment).exists()):
        return False
    execution_id = marked_execution_id(order)
    execution = NegotiationExecution.objects.filter(pk=execution_id, zero_order=order).select_related(
        'zero_order', 'proposal').first() if execution_id else None
    if not execution:
        return False
    try:
        shares = verified_first(execution, boundary=timezone.now())
    except (DjangoValidationError, ValidationError, ValueError):
        return False
    active = list(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True))
    return bool(len(active) == 1 and active[0] and active[0] != shares[0].account_id
                and payment.intent.provider_id == shares[0].account_id
                and payment.buyer_transaction.created_at < execution.accepted_at)


def eligible(order, *, allow_unpaid=False, proposal=None, allow_open=False, payment_intent_id=None):
    """Limit the new add-on route to the real remaining zero-termination seat."""
    from django.utils import timezone
    from django.core.exceptions import ValidationError as DjangoValidationError
    from .ordinary_zero_followup_capability import eligible as zero_eligible
    from .negotiation_zero import marked_execution_id
    from .ordinary_zero_followup_evidence import verified_first
    from .models import NegotiationExecution, OrderAddonHandover

    if (not zero_eligible(order) or order.status != order.Status.IN_SERVICE or order.settled_at
            or (not allow_open and order.aftersales.filter(status='OPEN').exists())):
        return False
    execution_id = marked_execution_id(order)
    execution = NegotiationExecution.objects.filter(pk=execution_id, zero_order=order).select_related(
        'zero_order', 'proposal').first() if execution_id else None
    if not execution:
        return False
    try:
        shares = verified_first(execution, boundary=timezone.now())
    except (DjangoValidationError, ValidationError, ValueError):
        return False
    active = list(order.providers.filter(released_at__isnull=True, settled_at__isnull=True)
                  .values_list('provider_account_id', flat=True))
    if len(active) != 1 or not active[0] or active[0] == shares[0].account_id:
        return False
    if proposal is not None and proposal.affected_accounts != [active[0]]:
        return False
    intents_query = order.addon_intents.select_related('segment')
    if allow_unpaid and payment_intent_id is not None:
        from django.db.models import Q
        intents_query = intents_query.filter(Q(payment__isnull=False) | Q(pk=payment_intent_id))
        if not order.addon_intents.filter(pk=payment_intent_id).exists():
            return False
    elif not allow_unpaid:
        intents_query = intents_query.filter(payment__isnull=False)
    intents = list(intents_query.order_by('pk'))
    if not intents:
        return False
    for intent in intents:
        from .models import OrderAddonPayment
        payments = list(OrderAddonPayment.objects.filter(intent=intent).select_related('buyer_transaction'))
        if len(payments) > 1 or (not payments and not allow_unpaid):
            return False
        if not payments or payments[0].buyer_transaction.created_at >= execution.termination_processed_at:
            if (intent.provider_id != active[0] or intent.segment.provider_account_id != active[0]
                    or intent.segment.attribution.order_id != order.pk):
                return False
    from .ordinary_refunded_team_handover import verify as verify_handover
    for handover in OrderAddonHandover.objects.filter(payment__intent__order=order, status='ACCEPTED',
            accepted_at__gte=execution.accepted_at).select_related('payment__intent__order',
                'source_segment__attribution', 'target_segment__attribution'):
        try:
            verify_handover(handover)
        except (DjangoValidationError, ValidationError, ValueError):
            return False
    return True


def continuing_account_locked(order, *, allow_open_case=False):
    """Verify the original agreement and one remaining live service seat."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('零退款后加购必须在订单事务内核对')
    from .ordinary_zero_followup_capability import eligible
    from .negotiation_termination import continuing_team
    if (not eligible(order) or order.status != order.Status.IN_SERVICE or order.settled_at
            or (not allow_open_case and order.aftersales.filter(status='OPEN').exists())):
        raise ValidationError('当前订单不能增加零退款后的服务加购')
    receipt = continuing_team(order, verify=True)
    active = list(order.providers.filter(released_at__isnull=True, settled_at__isnull=True)
                  .values_list('provider_account_id', flat=True))
    affected = list(receipt.shares.values_list('account_id', flat=True)) if receipt else []
    if (not receipt or not receipt.zero_order_id or receipt.refund_id
            or len(active) != 1 or not active[0]
            or len(affected) != 1 or affected[0] == active[0]):
        raise ValidationError('零退款后的继续履约身份待核对')
    return active[0]


def verify_addon_refunds_before(order, first, boundary, *, witness_receipt_id=None, current_refund_id=None):
    """Read real add-on refund facts without taking cross-order financial locks."""
    from console.models import AdminAuditLog
    from engagement.funding_plan import RefundablePart, plan_refund
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from .models import OrderAddonFundingReleaseRun, OrderAddonPayment, OrderAddonRefund

    continuing = list(order.providers.exclude(provider_account_id__in=first.proposal.affected_accounts)
                      .values_list('provider_account_id', flat=True))
    if len(continuing) != 1 or not continuing[0]:
        raise ValidationError('零退款后加购责任人待核对')
    # Replay only payments that actually existed at the reconstructed time.
    # A later purchase must not change an earlier receipt's proof.
    for payment in OrderAddonPayment.objects.filter(
            intent__order=order, buyer_transaction__created_at__lt=boundary).order_by('pk'):
        verify_receipt(payment)
        allocations = list(payment.allocations.order_by('pk'))
        used = {item.pk: 0 for item in allocations}
        refunds = list(OrderAddonRefund.objects.filter(payment=payment, created_at__lt=boundary)
                       .select_related('proposal__case', 'buyer_transaction__wallet').order_by('created_at', 'pk'))
        for refund in refunds:
            proposal, tx = refund.proposal, refund.buyer_transaction
            people = list(proposal.participants.order_by('account_id'))
            decisions = list(proposal.decisions.values_list('account_id', 'role', 'accepted', 'decided_at'))
            expected = {(payment.intent.buyer_id, 'BOSS', True)} | {
                (person.account_id, 'PROVIDER', True) for person in people}
            if (refund.order_id != order.pk or proposal.payment_id != payment.pk or proposal.case.order_id != order.pk
                    or proposal.status != 'ACCEPTED' or proposal.amount != refund.amount or refund.amount <= 0
                    or not proposal.accepted_at or proposal.accepted_at > refund.created_at
                    or not people or len(decisions) != len(expected)
                    or {(account, role, accepted) for account, role, accepted, _ in decisions} != expected
                    or any(not at or at < proposal.created_at for _, _, _, at in decisions)
                    or max(at for _, _, _, at in decisions) != proposal.accepted_at
                    or (refund.created_at >= first.termination_processed_at
                        and {person.account_id for person in people} != {continuing[0]})
                    or not tx or tx.wallet.account_id != order.customer_account_id or tx.amount != refund.amount
                    or tx.tx_type != 'REFUND' or tx.status != 'SUCCESS' or tx.reversal_of_id or tx.reversals.exists()
                    or tx.balance_after != tx.balance_before + tx.amount
                    or tx.operator_account_id != refund.operator_id or tx.operator_id != refund.legacy_operator_id):
                raise ValidationError('零退款前后加购退款确认或流水待核对')
            planned = plan_refund([RefundablePart(item.pk, item.kind, item.amount, used[item.pk])
                                   for item in allocations], refund.amount)
            source_plan = [{'allocation_id': item.pk, 'kind': item.kind, 'amount_raw': planned[item.pk]}
                           for item in allocations if planned[item.pk]]
            if proposal.source_plan != source_plan:
                raise ValidationError('加购退款原付款构成待核对')
            for key, value in planned.items():
                used[key] += value
            verify_shares(refund)
            path = f'/api/admin/orders/{order.pk}/addons/payments/{payment.pk}/refund-proposals/'
            proposal_audit = list(AdminAuditLog.objects.filter(resource='addon-refund-proposal', object_id=str(proposal.pk),
                method='POST', path=path, status_code=200))
            refund_audit = list(AdminAuditLog.objects.filter(resource='addon-refund', object_id=str(refund.pk),
                method='POST', path=f'{path}{proposal.pk}/execute/', status_code=200))
            if (len(proposal_audit) != 1 or proposal_audit[0].operator_account_id != proposal.created_by_id
                    or len(refund_audit) != 1 or refund_audit[0].operator_account_id != refund.operator_id
                    or refund_audit[0].request_body != {'order_id': order.pk, 'payment_id': payment.pk,
                                                        'proposal_id': proposal.pk, 'amount': refund.amount}):
                raise ValidationError('加购退款客服审计待核对')
            if refund.created_at >= first.termination_processed_at and refund.pk != current_refund_id:
                # A zero-amount release still has a real refund funding run;
                # prove that run directly rather than inventing a RELEASE row.
                run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
                if not run:
                    raise ValidationError('加购退款资金版本缺失')
                from .ordinary_after_main_addon_receipt import verified_release
                verified_release(run, witness_receipt_id)
