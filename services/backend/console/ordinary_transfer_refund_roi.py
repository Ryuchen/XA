"""Period costs from one verified ordinary transferred-order refund."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from orders.models import (Order, OrderAddonPayment, OrderOriginalShareTransfer, OrderPointsRedemption,
                               OrderSegmentSettlementManifest, OrderTransferNegotiationEvidence,
                               OrdinaryTransferFollowupRound)
    from orders.financial_lock import lock_order_money
    from orders.negotiation_execution import verify_execution
    from orders.negotiation_termination import verify_termination_evidence
    from orders.negotiation_transfer_evidence import verify_locked
    from orders.ordinary_transfer_capability import permits
    from orders.referral_refund_funding import related_receipt_ids
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.settlement import SettlementError
    from engagement.models import OrderFundingAllocation
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import verify_allocations_locked
    from .roi_period import _verified_addon_costs
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or order.is_gift_snapshot or not order.amount
                    or OrderPointsRedemption.objects.filter(order=order).exists()
                    or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            for receipt_id in sorted(related_receipt_ids(order)):
                _locked_receipt(receipt_id)
                verify_allocations_locked(receipt_id)
            if not permits(order):
                return None
            refunds = list(order.refund_records.order_by('pk'))
            if any(row.amount <= 0 for row in refunds):
                return None
            evidence = (OrderTransferNegotiationEvidence.objects.select_related('execution__proposal__case',
                        'execution__refund').filter(order=order).first())
            if not evidence:
                return None
            execution = evidence.execution
            events = []
            refund_index = 0
            if execution.refund_id:
                if not refunds or execution.refund_id != refunds[0].pk:
                    return None
                shares = verify_execution(execution)
                verify_termination_evidence(execution)
                events.append((refunds[0], list(shares)))
                refund_index = 1
            else:
                from orders.negotiation_zero import verify_zero_execution, verify_zero_termination
                verify_zero_execution(execution)
                verify_zero_termination(execution)
            evidence = verify_locked(execution)
            previous = execution
            followups = list(OrdinaryTransferFollowupRound.objects.filter(order=order, execution__isnull=False)
                             .select_related('execution__refund', 'settlement_revision').order_by('sequence'))
            from orders.ordinary_transfer_followup import _hash
            from orders.ordinary_transfer_followup_revision import verify_locked as verify_revision
            for number, round_ in enumerate(followups, 1):
                current = round_.execution
                if (round_.sequence != number or round_.previous_id != previous.pk
                        or round_.facts_hash != _hash(round_.proposal_id, round_.facts)
                        or not current or not current.termination_processed_at):
                    return None
                if current.refund_id:
                    if refund_index >= len(refunds) or current.refund_id != refunds[refund_index].pk:
                        return None
                    current_shares = verify_execution(current)
                    verify_termination_evidence(current)
                    events.append((refunds[refund_index], list(current_shares)))
                    refund_index += 1
                else:
                    from orders.ordinary_transfer_followup_zero import verify_execution as verify_zero
                    from orders.ordinary_transfer_followup_zero import verify_termination as verify_zero_end
                    verify_zero(current)
                    verify_zero_end(current)
                verify_revision(round_.settlement_revision)
                previous = current
            if refund_index != len(refunds):
                return None
            snapshot = evidence.snapshot
            lines = snapshot.get('lines') if isinstance(snapshot, dict) else None
            segments = snapshot.get('segments') if isinstance(snapshot, dict) else None
            if (not isinstance(lines, list) or not lines or not isinstance(segments, list)
                    or any(not isinstance(row, dict) or type(row.get('income')) is not int or row['income'] < 0 for row in lines)):
                return None
            current_ids = set(order.original_attribution.segments.values_list('pk', flat=True))
            snapshot_ids = {row.get('id') for row in segments if isinstance(row, dict)}
            manifest = OrderSegmentSettlementManifest.objects.filter(order=order, finalized_at__isnull=False).first()
            final_plan = None
            if current_ids != snapshot_ids:
                if not order.settled_at or not manifest:
                    return None
                from dataclasses import asdict
                from orders.segment_settlement import verify_finalized
                final_plan = verify_finalized(order, manifest)
                lines = [asdict(line) for line in final_plan.segments]
                if not lines or any(type(row.get('income')) is not int or row['income'] < 0 for row in lines):
                    return None
            payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0,
                reversal_of__isnull=True).select_related('wallet'))
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
            if (len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].wallet.user_id != order.customer_id or payments[0].reversals.exists()
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or order.funding_terms is None or not sources or sum(row.amount for row in sources) != order.amount):
                return None
            validate_order_funding_sources(order, sources)
            returned = {row.pk: 0 for row in sources}
            gift_refunds = {}
            for current_refund in refunds:
                split = plan_refund([RefundablePart(row.pk, row.kind, row.amount, returned[row.pk])
                                     for row in sources], current_refund.amount)
                gift_refunds[current_refund.pk] = sum(split[row.pk] for row in sources
                                                      if row.kind in ('GIFT', 'COMPENSATION'))
                for row in sources:
                    returned[row.pk] += split[row.pk]
            if any(row.refunded_amount != returned[row.pk] for row in sources):
                return None
            rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
            if type(rate) is not int or not 0 <= rate <= 100:
                return None
            original_referral = order.amount * rate // 100
            remaining = order.amount - sum(row.amount for row in refunds)
            if remaining < 0:
                return None
            net_referral = remaining * rate // 100
            from .roi_period import ordinary_refund_event_time
            credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
            if any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount for tx in credits):
                return None
            if order.settled_at:
                if not manifest:
                    return None
                if final_plan is None:
                    from orders.segment_settlement import verify_finalized
                    final_plan = verify_finalized(order, manifest)
            if credits:
                if (len(credits) != 1 or not manifest or manifest.inviter_transaction_id != credits[0].pk
                        or credits[0].amount != net_referral
                        or (refunds and credits[0].created_at <= ordinary_refund_event_time(refunds[-1]))):
                    return None
            elif order.settled_at and net_referral:
                return None
            addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                          .select_related('intent', 'buyer_transaction__wallet'))
            addon_costs = _verified_addon_costs(addons, start, end)
            if addon_costs is None:
                return None
            if addons and (order.settled_at or any(addon.handovers.filter(status='ACCEPTED').exists() for addon in addons)):
                if not manifest or not order.settled_at:
                    return None
                from .roi_addon_handover import period_delta
                addon_costs['provider'] += period_delta(order, manifest, addons, start, end)
            paid = start <= payments[0].created_at < end
            if followups and not order.settled_at:
                return None
            # The payment-period value is the original segment liability;
            # each verified refund changes it only in that refund's period.
            provider_cost = sum(row['income'] for row in lines) if paid else 0
            referral_cost = original_referral if paid else 0
            gift_cost = sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if paid else 0
            running_paid = order.amount
            for current_refund, current_shares in events:
                delta = running_paid * rate // 100 - (running_paid - current_refund.amount) * rate // 100
                running_paid -= current_refund.amount
                if start <= ordinary_refund_event_time(current_refund) < end:
                    provider_cost -= sum(row.burden for row in current_shares)
                    referral_cost -= delta
                    gift_cost -= gift_refunds[current_refund.pk]
            return {'provider': provider_cost + addon_costs['provider'], 'referral': referral_cost,
                    'gift_compensation': gift_cost + addon_costs['gift_compensation']}
    except (DjangoValidationError, ValidationError, FundingPlanError, SettlementError, ReferralEvidenceError,
            ObjectDoesNotExist, KeyError, TypeError, ValueError):
        return None
