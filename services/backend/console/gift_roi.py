"""Period costs of verified principal-funded gifts and actual refunds."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from engagement.models import PointFundingLot
    from orders.models import Order, OrderPointsRedemption
    from orders.financial_lock import lock_order_money
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.referral_refund_funding import prelock, verify_referral_history
    from orders.refunds import proportional_targets, RefundError
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if (not order or not order.is_gift_snapshot or not order.amount or not order.settled_at
                    or not order.completed_at or order.settled_at != order.completed_at
                    or OrderPointsRedemption.objects.filter(order=order).exists() or order.addon_intents.exists()):
                return None
            lock_order_money()
            prelock(order)
            originals = list(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True)
                             .select_related('wallet', 'wallet__user'))
            payments = [row for row in originals if row.tx_type == 'GIFT' and row.amount < 0]
            credits = [row for row in originals if row.tx_type in ('INCOME', 'GIFT', 'SHOP_INCOME') and row.amount > 0]
            if (len(payments) != 1 or payments[0].amount != -order.amount
                    or payments[0].wallet.account_id != order.customer_account_id
                    or payments[0].wallet.user_id != order.customer_id or payments[0].reversals.exists()
                    or payments[0].balance_after != payments[0].balance_before + payments[0].amount
                    or sum(row.amount for row in credits) != order.amount
                    or any(row.balance_after != row.balance_before + row.amount for row in credits)):
                return None
            sources = list(order.point_allocations.select_related('lot'))
            if (not sources or sum(row.amount for row in sources) != order.amount
                    or any(row.lot.account_id != order.customer_account_id or row.lot.kind not in (
                        PointFundingLot.Kind.PREPAID, PointFundingLot.Kind.RECHARGE)
                        or row.lot.source_key.startswith('wallet:') or row.lot.source_key.endswith(':prior-balance')
                        or (row.lot.kind == PointFundingLot.Kind.RECHARGE and row.source_gift_amount != 0)
                        for row in sources)):
                return None
            referral = paid_referral_credits(order, credits, expected_amount=order.inviter_commission)
            if any(row.reversals.exists() for row in referral):
                return None
            referral_ids = {row.pk for row in referral}
            provider = [row for row in credits if row.pk not in referral_ids and row.tx_type in ('GIFT', 'INCOME')]
            if (sum(row.amount for row in provider) != order.provider_income
                    or sum(row.amount for row in credits if row.tx_type == 'SHOP_INCOME') != order.shop_income
                    or any(row.wallet.account_id != order.provider_account_id or row.wallet.user_id != order.provider_id
                           for row in provider)
                    or any(row.wallet.user is None or row.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                           for row in credits if row.tx_type == 'SHOP_INCOME')):
                return None
            refunds = list(order.refund_records.order_by('pk'))
            if any(row.negotiated_allocation for row in refunds):
                return None
            if referral and refunds:
                verify_referral_history(order, credits, referral, refunds)
            weights = {row.pk: row.amount for row in credits}
            cumulative = 0
            paid_in_period = start <= payments[0].created_at < end
            provider_cost = sum(row.amount for row in provider) if paid_in_period else 0
            for refund in refunds:
                from .roi_period import gift_refund_event_time
                refund_at = gift_refund_event_time(refund)
                before = proportional_targets(weights, cumulative)
                cumulative += refund.amount
                after = proportional_targets(weights, cumulative)
                lines = list(refund.recovery_lines.filter(original_transaction_id__in=weights)
                             .select_related('reversal_transaction'))
                by_original = {line.original_transaction_id: line for line in lines}
                for credit in credits:
                    due = 0 if credit.pk in referral_ids else after[credit.pk] - before[credit.pk]
                    line = by_original.get(credit.pk)
                    if not due:
                        if line:
                            return None
                        continue
                    if (not line or line.amount != -due or line.wallet_id != credit.wallet_id
                            or line.reversal_transaction.reversal_of_id != credit.pk
                            or line.reversal_transaction.order_id != order.pk
                            or line.reversal_transaction.wallet_id != credit.wallet_id
                            or line.reversal_transaction.status != 'SUCCESS'
                            or line.reversal_transaction.amount != -due
                            or line.reversal_transaction.balance_after != line.reversal_transaction.balance_before - due
                            or line.reversal_transaction.reversals.exists()):
                        return None
                    if credit in provider and start <= refund_at < end:
                        provider_cost -= due
            targets = proportional_targets({row.pk: row.amount for row in sources}, cumulative)
            if any(row.refunded_amount != targets[row.pk] for row in sources):
                return None
            return {'provider': provider_cost, 'referral': sum(row.amount for row in referral) if paid_in_period else 0,
                    'gift_compensation': 0}
    except (DjangoValidationError, ValidationError, ReferralEvidenceError, RefundError,
            ObjectDoesNotExist, ValueError, KeyError, TypeError):
        return None
