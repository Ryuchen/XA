"""Cost verified fully-settled transfers at actual payment/add-on events."""
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from orders.models import OrderPointsRedemption
    if not OrderPointsRedemption.objects.filter(order_id=order_id).exists():
        return _costs(order_id, start, end)
    # This scope proves and locks the shared receipt and the actual points
    # funding before the finalized segment plan can authorize period costs.
    from orders.points_transfer_refund_access import after_transfer
    from orders.settlement import SettlementError
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            with after_transfer(order_id, phase='settlement'):
                return _costs(order_id, start, end, points_addon_refund=True)
    except (DjangoValidationError, ValidationError, SettlementError, ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None


def _costs(order_id, start, end, *, points_addon_refund=False):
    from orders.models import (Order, OrderAddonPayment, OrderOriginalShareTransfer,
                               OrderPlatformSubsidy, OrderPointsRedemption, OrderSegmentSettlementManifest)
    from orders.financial_lock import lock_order_money
    from orders.segment_settlement import _ordinary_transfer_refund, verify_finalized
    from orders.settlement import SettlementError
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from engagement.models import OrderFundingAllocation
    from engagement.funding_wallet import validate_order_funding_sources
    from .roi_period import _verified_addon_costs
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or order.is_gift_snapshot or not order.amount
                    or (not order.settled_at and not points_addon_refund)
                    or order.refund_records.exists()
                    or (not points_addon_refund and OrderPointsRedemption.objects.filter(order=order).exists())
                    or OrderPlatformSubsidy.objects.filter(order=order).exists()
                    or not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
                return None
            lock_order_money()
            if _ordinary_transfer_refund(order):
                return None
            manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
            if not manifest:
                return None
            partial_evidence = None
            if order.settled_at:
                plan = verify_finalized(order, manifest)
            else:
                from .points_transfer_partial_roi import verified_partial
                plan, partial_evidence = verified_partial(order, manifest, start, end)
            payments = list(order.transactions.filter(tx_type='PAY', status='SUCCESS', amount__lt=0,
                reversal_of__isnull=True).select_related('wallet'))
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
            if (len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].wallet.user_id != order.customer_id or payments[0].reversals.exists()
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or order.funding_terms is None or not sources
                    or sum(row.amount for row in sources) != order.amount or any(row.refunded_amount for row in sources)):
                return None
            validate_order_funding_sources(order, sources)
            referral = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')),
                expected_amount=plan.inviter_income if order.settled_at else 0)
            if any(row.reversals.exists() for row in referral):
                return None
            addons = list(OrderAddonPayment.objects.filter(intent__order=order)
                          .select_related('intent', 'buyer_transaction__wallet'))
            addon_costs = _verified_addon_costs(addons, start, end)
            if addon_costs is None or any(row.buyer_transaction.created_at < payments[0].created_at for row in addons):
                return None
            if addons:
                # The finalized manifest verifies every payment/refund and
                # resulting share.  Attribute each confirmed handover at its
                # own time instead of limiting points orders to one add-on.
                from .roi_addon_handover import period_delta
                addon_costs['provider'] += period_delta(order, manifest, addons, start, end)
            paid_in_period = start <= payments[0].created_at < end
            # Segment income is the gross main-order liability. The add-on
            # refund helper independently assigns its actual entire burden to
            # the refund month, including any main-income deduction.
            main_income = (sum(segment.income for segment in plan.segments) if points_addon_refund or any(
                addon.refund_records.exists() for addon in addons)
                           else sum(person.income for person in plan.persons))
            result = {'provider': (main_income if paid_in_period else 0) + addon_costs['provider'],
                      'referral': (plan.inviter_income if paid_in_period else 0),
                      'gift_compensation': (sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION'))
                                            if paid_in_period else 0) + addon_costs['gift_compensation']}
            if partial_evidence:
                result['verified_settlement'] = partial_evidence
            return result
    except (DjangoValidationError, ValidationError, SettlementError, ReferralEvidenceError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
