"""Verified period costs for ordinary refunds retaining paid referral income."""
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from django.conf import settings
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from orders.models import Order, OrderPointsRedemption, OrderOriginalShareTransfer, OrderPrestartHandoff
    from orders.financial_lock import lock_order_money
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.referral_refund_funding import prelock, verify_referral_history
    from orders.refunds import proportional_targets, RefundError
    from engagement.models import OrderFundingAllocation
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.funding_plan import FundingPlanError, RefundablePart, plan_refund
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or not order.amount or order.is_gift_snapshot or not order.settled_at
                    or order.addon_intents.exists() or OrderPointsRedemption.objects.filter(order=order).exists()
                    or OrderPrestartHandoff.objects.filter(order=order).exists()
                    or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
                    or order.transfer_limit_seats.filter(transfer_count__gt=0).exists()):
                return None
            lock_order_money()
            prelock(order)
            refunds = list(order.refund_records.order_by('pk'))
            if not refunds or any(row.negotiated_allocation for row in refunds):
                return None
            txs = list(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True).select_related('wallet', 'wallet__user'))
            payments = [tx for tx in txs if tx.tx_type == 'PAY' and tx.amount < 0]
            credits = [tx for tx in txs if tx.tx_type in ('INCOME', 'SHOP_INCOME', 'GIFT') and tx.amount > 0]
            if (len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].wallet.user_id != order.customer_id
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or payments[0].reversals.exists() or sum(tx.amount for tx in credits) != order.amount
                    or any(tx.balance_after != tx.balance_before + tx.amount for tx in credits)):
                return None
            referral = paid_referral_credits(order, credits, expected_amount=order.inviter_commission)
            if not referral or any(tx.reversals.exists() for tx in referral):
                return None
            verify_referral_history(order, credits, referral, refunds)
            sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot').order_by('pk'))
            if order.funding_terms is None or not sources or sum(row.amount for row in sources) != order.amount:
                return None
            validate_order_funding_sources(order, sources)
            returned = {row.pk: 0 for row in sources}
            gift_cost = sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if start <= payments[0].created_at < end else 0
            referral_ids = {tx.pk for tx in referral}
            provider_ids = {tx.pk for tx in credits if tx.pk not in referral_ids and tx.tx_type in ('INCOME', 'GIFT')}
            providers = set(order.providers.values_list('provider_id', 'provider_account_id'))
            providers.add((order.provider_id, order.provider_account_id))
            if (any((tx.wallet.user_id, tx.wallet.account_id) not in providers for tx in credits if tx.pk in provider_ids)
                    or any(not tx.wallet.user or tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                           for tx in credits if tx.tx_type == 'SHOP_INCOME')):
                return None
            weights = {tx.pk: tx.amount for tx in credits}
            cumulative = 0
            provider_cost = sum(tx.amount for tx in credits if tx.pk in provider_ids) if start <= payments[0].created_at < end else 0
            for refund in refunds:
                from .roi_period import ordinary_refund_event_time
                refund_at = ordinary_refund_event_time(refund)
                before = proportional_targets(weights, cumulative)
                cumulative += refund.amount
                after = proportional_targets(weights, cumulative)
                lines = list(refund.recovery_lines.filter(original_transaction_id__in=weights)
                             .select_related('reversal_transaction'))
                by_original = {line.original_transaction_id: line for line in lines}
                for tx in credits:
                    expected = 0 if tx.pk in referral_ids else after[tx.pk] - before[tx.pk]
                    line = by_original.get(tx.pk)
                    if not expected:
                        if line:
                            return None
                        continue
                    if (not line or line.amount != -expected or line.wallet_id != tx.wallet_id
                            or line.reversal_transaction.reversal_of_id != tx.pk
                            or line.reversal_transaction.order_id != order.pk
                            or line.reversal_transaction.wallet_id != tx.wallet_id
                            or line.reversal_transaction.status != 'SUCCESS'
                            or line.reversal_transaction.amount != -expected
                            or line.reversal_transaction.reversals.exists()
                            or line.reversal_transaction.balance_after != line.reversal_transaction.balance_before - expected):
                        return None
                    if tx.pk in provider_ids and start <= refund_at < end:
                        provider_cost -= expected
                parts = [RefundablePart(row.pk, row.kind, row.amount, returned[row.pk]) for row in sources]
                allocation = plan_refund(parts, refund.amount)
                for row in sources:
                    returned[row.pk] += allocation[row.pk]
                    if start <= refund_at < end and row.kind in ('GIFT', 'COMPENSATION'):
                        gift_cost -= allocation[row.pk]
            if any(row.refunded_amount != returned[row.pk] for row in sources):
                return None
            return {'provider': provider_cost,
                    'referral': sum(tx.amount for tx in referral) if start <= payments[0].created_at < end else 0,
                    'gift_compensation': gift_cost}
    except (DjangoValidationError, ValidationError, FundingPlanError, RefundError, ReferralEvidenceError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
