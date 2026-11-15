"""Verify each real add-on refund rounding amount at its event boundary."""
from django.core.exceptions import ValidationError
from django.db.models import Q


def facts(order, *, before=None):
    from .models import (NegotiationExecution, OrdinaryTransferFollowupRound,
                         OrderPointsFundingAllocation, OrderAddonFundingReleaseRun)
    from .addon_payment import verify_receipt
    from .addon_refund_share_allocation import verify as verify_shares
    from console.deposit_clearance import _verified_refund_execution
    from .negotiation_termination import verify_termination_evidence
    from .ordinary_transfer_followup import _hash

    events = NegotiationExecution.objects.filter(Q(refund__order=order) | Q(zero_order=order),
        termination_processed_at__isnull=False).select_related('refund', 'proposal').order_by('pk')
    if before is not None:
        events = events.filter(accepted_at__lt=before)
    events = list(events)
    rounds = list(OrdinaryTransferFollowupRound.objects.filter(order=order,
        execution_id__in=[event.pk for event in events]).order_by('sequence'))
    if events:
        if (len(rounds) != len(events) - 1
                or [round_row.sequence for round_row in rounds] != list(range(1, len(events)))
                or [round_row.execution_id for round_row in rounds] != [event.pk for event in events[1:]]
                or any(round_row.previous_id != previous.pk for round_row, previous in zip(rounds, events))
                or any(round_row.facts_hash != _hash(round_row.proposal_id, round_row.facts) for round_row in rounds)):
            raise ValidationError('逐轮协商与取整凭证不一致')
        for index, event in enumerate(events):
            if event.refund_id:
                if not _verified_refund_execution(event.refund):
                    raise ValidationError('取整对应的主单退款凭证缺失')
                verify_termination_evidence(event)
            elif index:
                from .ordinary_transfer_followup_zero import verify_termination
                verify_termination(event, lock=False)
            else:
                from .models import OrderTransferNegotiationEvidence, OrderOriginalShareTransfer
                if OrderTransferNegotiationEvidence.objects.filter(order=order, execution=event).exists():
                    from .ordinary_transfer_zero_receipt import verify as verify_zero
                    verify_zero(event)
                else:
                    from .ordinary_zero_pretransfer import verify as verify_zero_before_transfer
                    transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    verified = verify_zero_before_transfer(order, before=transfer.created_at) if transfer else None
                    if not verified or verified.pk != event.pk:
                        raise ValidationError('转单前零退款凭证缺失')

    query = order.addon_refunds.select_related('proposal', 'payment__intent').order_by('created_at', 'pk')
    if before is not None:
        query = query.filter(created_at__lt=before)
    result = []
    for refund in query:
        for event in events:
            started = event.refund.created_at if event.refund_id else event.accepted_at
            if started == refund.created_at or (started < refund.created_at <= event.termination_processed_at):
                raise ValidationError('主单协商和加购退款时序无法区分')
        if events and refund.created_at < (events[0].refund.created_at if events[0].refund_id else events[0].accepted_at):
            if refund.rounding_surplus and OrderPointsFundingAllocation.objects.filter(service_order=order,
                    created_at__lte=refund.created_at).exists():
                run = OrderAddonFundingReleaseRun.objects.filter(refund=refund).first()
                from .models import OrdinaryPostRefundTransferRevision
                revision = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    source_kind='ADDON_REFUND', source_id=refund.pk).first()
                if revision:
                    if (revision.created_at <= refund.created_at
                            or revision.created_at >= events[0].accepted_at):
                        raise ValidationError('主单协商前加购退款版本时序异常')
                    from .ordinary_post_refund_transfer import verify_locked as verify_revision
                    verify_revision(revision)
                    if run and not (revision.snapshot.get('pre_main_transfer_v1')
                                    and revision.snapshot.get('zero_pretransfer_execution_id')):
                        raise ValidationError('主单协商前加购退款资金版本重复')
                    if not run:
                        run = OrderAddonFundingReleaseRun.objects.filter(ordinary_transfer_revision=revision).first()
                if not run:
                    raise ValidationError('主单协商前加购退款的资金版本缺失')
                if run.refund_id:
                    from .models import OrderOriginalShareTransfer
                    first_transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    kind = (run.ordinary_prestart_snapshot.get('kind')
                            if isinstance(run.ordinary_prestart_snapshot, dict) else None)
                    pretransfer = (kind == 'ORDINARY_PRESTART_ADDON_V1' and first_transfer
                                   and run.created_at < first_transfer.created_at)
                    if not (kind == 'ORDINARY_TRANSFER_PRE_MAIN_ADDON_V1' or pretransfer):
                        raise ValidationError('主单协商前加购退款的资金版本来源异常')
                from .addon_funding_release import verify_run
                verify_run(run, _skip_usage=True)
        verify_receipt(refund.payment)
        people = list(refund.proposal.participants.all())
        decisions = list(refund.proposal.decisions.all())
        expected = {(refund.payment.intent.buyer_id, 'BOSS')} | {(person.account_id, 'PROVIDER') for person in people}
        basis = sum(person.basis for person in people)
        if (not people or not basis or len(decisions) != len(expected)
                or {(decision.account_id, decision.role) for decision in decisions} != expected
                or refund.proposal.status != 'ACCEPTED' or not refund.proposal.accepted_at
                or any(decision.accepted is not True or not decision.decided_at
                       or decision.decided_at < refund.proposal.created_at for decision in decisions)
                or max(decision.decided_at for decision in decisions) != refund.proposal.accepted_at
                or refund.proposal.accepted_at > refund.created_at
                or any(person.burden != ((refund.amount * person.basis + basis * 10 - 1) // (basis * 10)) * 10
                       or person.burden != person.addon_pending_deduction + person.main_pending_deduction
                            + person.wallet_deduction + person.deposit_deduction for person in people)
                or sum(person.burden for person in people) != refund.amount + refund.rounding_surplus):
            raise ValidationError('加购退款逐人取整及实际承担异常')
        verify_shares(refund)
        if refund.rounding_transaction_id:
            tx = refund.rounding_transaction
            if (tx.order_id != order.pk or tx.amount != refund.rounding_surplus
                    or tx.tx_type != 'SHOP_INCOME' or tx.status != 'SUCCESS'
                    or tx.balance_after != tx.balance_before + tx.amount or tx.reversal_of_id or tx.reversals.exists()):
                raise ValidationError('加购退款取整门店收入流水异常')
        result.append([refund.pk, refund.rounding_surplus])
    return result
