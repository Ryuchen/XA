"""Verified period costs for a transferred points order's first refund."""
from dataclasses import asdict

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from orders.models import (Order, OrderAddonHandover, OrderAddonPayment, OrderOriginalShareTransfer,
                               PointsNegotiationPosting, PointsPostRefundTransferRevision,
                               OrderSegmentSettlementManifest)
    from orders.financial_lock import lock_order_money
    from orders.points_business_access import case_refund_scope
    from orders.points_negotiation_execution import verify_posting_locked
    from orders.points_negotiation_termination import verify_termination_locked
    from orders.points_transfer_refund_evidence import verify_locked, reconstruct_funding_locked
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.settlement import SettlementError
    from engagement.models import OrderFundingAllocation
    from engagement.funding_wallet import validate_order_funding_sources
    from .roi_period import _verified_addon_costs
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or not order.amount or order.is_gift_snapshot
                    or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            refunds = list(order.refund_records.order_by('pk'))
            if len(refunds) != 1:
                return None
            refund = refunds[0]
            posting = (PointsNegotiationPosting.objects.filter(points_refund__refund=refund)
                       .select_related('proposal__case', 'points_refund__redemption__payment').first())
            if not posting or not isinstance(posting.participants, dict):
                return None
            voluntary = OrderAddonHandover.objects.filter(payment__intent__order=order,
                kind='VOLUNTARY', status='ACCEPTED').exists()
            if voluntary:
                # Reporting must use the read/settlement scope: the refund
                # scope intentionally rejects voluntary handovers outside the
                # original mutation transaction.
                from orders.points_transfer_refund_access import after_transfer
                scope = after_transfer(order.pk, phase='settlement')
            else:
                scope = case_refund_scope(posting.proposal.case_id)
            with scope:
                posting = verify_posting_locked(posting)
                if voluntary:
                    from orders.points_negotiation_termination import _verify_termination_from_verified_posting
                    _verify_termination_from_verified_posting(posting, facts_only=True)
                else:
                    verify_termination_locked(posting)
                facts = verify_locked(order.pk, posting.participants, boundary=posting.created_at)
                transfers = facts.get('transfers')
                if not transfers:
                    return None
                history = reconstruct_funding_locked(order.pk, posting.participants, refund_id=refund.pk,
                    through_transfer_id=transfers[-1]['id'], boundary=posting.created_at)
                gross_income = sum(line[4] for line in history.segment_shares)
                initial_ids = {row['id'] for row in facts['segments']}
                current_ids = set(order.original_attribution.segments.values_list('pk', flat=True))
                manifest = OrderSegmentSettlementManifest.objects.filter(order=order).first()
                addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                              .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
                addon_costs = {'provider': 0, 'gift_compensation': 0}
                if order.addon_intents.exists():
                    if not addons or not manifest:
                        return None
                    partial_evidence = None
                    if order.settled_at:
                        from orders.segment_settlement import verify_finalized
                        verify_finalized(order, manifest)
                    else:
                        from .points_transfer_partial_roi import verified_partial
                        _, partial_evidence = verified_partial(order, manifest, start, end)
                    version = PointsPostRefundTransferRevision.objects.filter(order=order).order_by('-pk').first()
                    history_addons = (version.snapshot.get('addon_history') if version else
                        getattr(getattr(posting, 'funding_release_evidence', None), 'addon_funding_snapshot', None))
                    from orders.points_transfer_addon_events import later_runs
                    later = later_runs(order, posting)
                    if version:
                        later = [row for row in later if row.created_at > version.created_at]
                    if later:
                        latest = later[-1].refunded_main_snapshot
                        if (not isinstance(latest, dict)
                                or (version and latest.get('transfer_revision_id') != version.pk)
                                or (not version and latest.get('transfer_origin_posting_id') != posting.pk)):
                            return None
                        history_addons = latest.get('addon_history')
                    from .points_transfer_multi_addon_roi import period_costs
                    addon_costs = period_costs(order, manifest, history_addons, addons, start, end,
                                               main_event_at={posting.created_at, refund.created_at})
                if current_ids != initial_ids:
                    if not manifest:
                        return None
                    if order.settled_at:
                        from orders.segment_settlement import verify_finalized
                        plan = verify_finalized(order, manifest)
                    elif addons:
                        from .points_transfer_partial_roi import verified_partial
                        plan, partial_evidence = verified_partial(order, manifest, start, end)
                    else:
                        return None
                    gross_income = sum(asdict(line)['income'] for line in plan.segments)
                proof = posting.points_refund
                payment = proof.redemption.payment
                from .roi_period import points_refund_event_time
                refund_at = points_refund_event_time(proof)
                if (not payment or payment.order_id != order.pk or payment.tx_type != 'PAY'
                        or payment.status != 'SUCCESS' or payment.amount != -order.amount
                        or payment.wallet.account_id != order.customer_account_id
                        or payment.wallet.user_id != order.customer_id or payment.reversal_of_id
                        or payment.reversals.exists() or payment.balance_after != payment.balance_before + payment.amount):
                    return None
                if any(addon.buyer_transaction.created_at < payment.created_at for addon in addons):
                    return None
                sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
                if not sources or sum(row.amount for row in sources) != order.amount:
                    return None
                validate_order_funding_sources(order, sources)
                for kind in ('prepaid', 'gift', 'compensation'):
                    if sum(row.refunded_amount for row in sources if row.kind == kind.upper()) != getattr(proof, f'{kind}_units'):
                        return None
                rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
                if type(rate) is not int or not 0 <= rate <= 100:
                    return None
                original_referral = order.amount * rate // 100
                net_referral = (order.amount - refund.amount) * rate // 100
                credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                    tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
                if any(tx.reversals.exists() or tx.balance_after != tx.balance_before + tx.amount for tx in credits):
                    return None
                if order.settled_at:
                    if not manifest:
                        return None
                    from orders.segment_settlement import verify_finalized
                    verify_finalized(order, manifest)
                if credits:
                    if (len(credits) != 1 or not manifest or manifest.inviter_transaction_id != credits[0].pk
                            or credits[0].amount != net_referral or credits[0].created_at <= refund_at):
                        return None
                elif order.settled_at and net_referral:
                    return None
                paid, refunded = start <= payment.created_at < end, start <= refund_at < end
                result = {'provider': (gross_income if paid else 0)
                                    - (sum(posting.shares.values_list('burden', flat=True)) if refunded else 0)
                                    + addon_costs['provider'],
                        'referral': (original_referral if paid else 0)
                                    - (original_referral - net_referral if refunded else 0),
                        'gift_compensation': (sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION'))
                                              if paid else 0)
                                             - (proof.gift_units + proof.compensation_units if refunded else 0)
                                             + addon_costs['gift_compensation']}
                if addons and not order.settled_at:
                    result['verified_settlement'] = partial_evidence
                if order.settled_at and manifest and addons:
                    people = list(manifest.people.select_related('income_transaction'))
                    addon_records = list(manifest.addon_settlements.select_related('income_transaction'))
                    transactions = [row.income_transaction for row in people if row.income_transaction_id]
                    transactions += [row.income_transaction for row in addon_records if row.income_transaction_id]
                    from django.utils import timezone
                    result['verified_settlement'] = {'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
                        'status': 'SETTLED', 'settled_segment_ids': sorted({pk for row in people for pk in row.segment_ids}),
                        'income_posted_in_period_units': sum(tx.amount for tx in transactions if start <= tx.created_at < end),
                        'pending_segment_id': None, 'pending_main_units': 0, 'pending_addon_units': 0}
                return result
    except (DjangoValidationError, ValidationError, ReferralEvidenceError, SettlementError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
