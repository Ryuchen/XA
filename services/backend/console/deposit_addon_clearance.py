"""Add-on earnings and refunds that affect a provider's deposit clearance."""
from collections import defaultdict

from django.db import DatabaseError
from django.db.models import Q
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError


def addon_clearance(profile, wallet, *, now=None):
    """Return unsettled add-on segments and their actual closing timestamps."""
    from orders.addon_handover import current_responsibilities
    from orders.models import (OrderAddonPayment, OrderAddonRefund,
                               OrderAddonSettlement, OrderSegmentAddonSettlement)
    from wallet.models import RefundDepositDeficit, Transaction

    now = now or timezone.now()
    account_id, user_id = profile.account_id, profile.user_id
    if not account_id:
        return {'pending': 0, 'timestamps': [], 'income_transaction_ids': [], 'needs_review': True}
    payments = OrderAddonPayment.objects.filter(
        Q(intent__provider_id=account_id)
        | Q(handovers__status='ACCEPTED', handovers__shares__segment__provider_account_id=account_id)
        | Q(handovers__status='ACCEPTED', handovers__source_segment__provider_account_id=account_id)
        | Q(handovers__status='ACCEPTED', handovers__target_segment__provider_account_id=account_id)
    ).select_related('intent__order').distinct().order_by('pk')
    timestamps, income_transaction_ids, pending, needs_review = [], [], 0, False
    for payment in payments:
        try:
            _, shares = current_responsibilities(payment)
        except (ValidationError, ValueError, TypeError):
            needs_review = True
            continue
        expected = defaultdict(int)
        for share in shares:
            if share['provider_id'] == account_id:
                expected[share['segment_id']] += share['income']
        ordinary = OrderAddonSettlement.objects.filter(payment=payment,
            segment__provider_account_id=account_id).select_related('income_transaction__wallet')
        transferred = OrderSegmentAddonSettlement.objects.filter(payment=payment,
            segment__provider_account_id=account_id).select_related('income_transaction__wallet')
        records = defaultdict(list)
        for row in list(ordinary) + list(transferred):
            records[row.segment_id].append(row)
        if not shares and payment.handovers.filter(status='ACCEPTED').exists():
            # A completely refunded handover has no remaining add-on parts;
            # the actual settlement writer therefore need not create a zero
            # add-on row. Verify the accepted history and the person's real
            # service settlement, rather than inventing an add-on payout.
            from datetime import timedelta
            from orders.addon_payment import verify_receipt
            from orders.models import OrderSegmentPersonSettlement
            from .deposit_clearance import verified_segment_credit
            order = payment.intent.order
            handovers = list(payment.handovers.filter(status='ACCEPTED').select_related(
                'source_segment__attribution', 'target_segment__attribution').order_by('version'))
            refunds = list(payment.refund_records.order_by('created_at', 'pk'))
            own_segments = {segment.pk for event in handovers for segment in (event.source_segment, event.target_segment)
                            if segment.provider_account_id == account_id}
            if payment.intent.provider_id == account_id:
                own_segments.add(payment.intent.segment_id)
            try:
                verify_receipt(payment)
                if (not handovers or not refunds or sum(item.amount for item in refunds) != payment.intent.amount
                        or not order.settled_at or order.settled_at > now or not own_segments
                        or payment.handovers.filter(status='PENDING').exists() or records):
                    raise ValueError('已接替加购全退结清事实不完整')
                from .deposit_voluntary_clearance import verify_history
                verified = verify_history(payment, boundary=now + timedelta(microseconds=1))
                if [event.pk for event in verified] != [event.pk for event in handovers]:
                    raise ValueError('加购接替历史已变化')
                segmented, amount = verified_segment_credit(order, account_id)
                person = OrderSegmentPersonSettlement.objects.filter(
                    manifest__order=order, account_id=account_id).select_related(
                        'manifest', 'income_transaction__wallet').first()
                if (not segmented or amount is None or not person or person.amount != amount
                        or person.manifest.finalized_at != order.settled_at
                        or not isinstance(person.segment_ids, list) or not own_segments.issubset(set(person.segment_ids))
                        or not person.settled_at or timezone.is_naive(person.settled_at)
                        or person.settled_at > order.settled_at or person.settled_at > now
                        or any(item.created_at > order.settled_at for item in refunds)
                        or any(event.accepted_at > order.settled_at for event in handovers)
                        or (person.income_transaction_id and (not wallet or person.income_transaction.wallet_id != wallet.pk
                            or person.income_transaction.wallet.account_id != account_id
                            or (user_id is not None and person.income_transaction.wallet.user_id != user_id)))):
                    raise ValueError('已接替加购个人结清事实不完整')
                timestamps.extend([person.settled_at, order.settled_at,
                                   *(event.accepted_at for event in handovers)])
                if person.income_transaction_id:
                    timestamps.append(person.income_transaction.created_at)
            except (DatabaseError, DjangoValidationError, ValidationError, ValueError, TypeError, AttributeError, KeyError):
                needs_review = True
        # A fully refunded, never-handed-over add-on may have a real zero
        # settlement for its original segment even though its remaining
        # responsibilities are empty. Verify the original payment/refunds
        # and settlement parent before accepting that terminal event.
        if not shares and len(records) == 1 and not payment.handovers.exists():
            rows = next(iter(records.values()))
            if len(rows) == 1 and isinstance(rows[0], OrderAddonSettlement):
                row = rows[0]
                order = payment.intent.order
                refunds = list(payment.refund_records.all())
                parent_order = (row.order_settlement.order_id if row.order_settlement_id else
                    row.refund_settlement.posting.points_refund.redemption.order_id if row.refund_settlement_id else None)
                try:
                    from orders.addon_payment import verify_receipt
                    from wallet.models import ProviderReport
                    verify_receipt(payment)
                    valid_zero = (refunds and sum(item.amount for item in refunds) == payment.intent.amount
                        and row.segment_id == payment.intent.segment_id
                        and row.segment.attribution.order_id == order.pk
                        and row.segment.provider_account_id == account_id == payment.intent.provider_id
                        and row.report.order_id == order.pk and row.report.provider_account_id == account_id
                        and (user_id is None or row.report.provider_id == user_id)
                        and row.report.status == ProviderReport.Status.APPROVED
                        and row.report.submitted_at and row.report.audited_at
                        and row.report.submitted_at <= row.report.audited_at <= row.settled_at
                        and parent_order == order.pk and row.amount == 0 and not row.income_transaction_id
                        and order.settled_at and row.settled_at and not timezone.is_naive(row.settled_at)
                        and not timezone.is_naive(order.settled_at)
                        and payment.paid_at <= row.settled_at <= order.settled_at <= now
                        and all(item.created_at <= row.settled_at for item in refunds))
                except (ValidationError, ValueError, TypeError, AttributeError):
                    valid_zero = False
                if valid_zero:
                    timestamps.append(row.settled_at)
                    records.clear()
        for segment_id, amount in expected.items():
            rows = records.pop(segment_id, [])
            if not rows:
                if amount:
                    pending += 1
                continue
            if len(rows) != 1:
                needs_review = True
                continue
            row = rows[0]
            income = row.income_transaction
            if (row.amount != amount or not row.settled_at or timezone.is_naive(row.settled_at)
                    or row.settled_at > now or row.settled_at < payment.paid_at):
                needs_review = True
                continue
            if amount and (not income or not wallet or income.wallet_id != wallet.pk
                    or income.wallet.account_id != account_id
                    or (user_id is not None and income.wallet.user_id != user_id)
                    or income.order_id != payment.intent.order_id
                    or income.tx_type != Transaction.TxType.INCOME or income.status != Transaction.Status.SUCCESS
                    or income.amount != amount or income.balance_after != income.balance_before + income.amount
                    or income.reversal_of_id or income.reversals.exists() or not income.created_at
                    or timezone.is_naive(income.created_at) or income.created_at > now
                    or income.created_at < payment.paid_at):
                needs_review = True
                continue
            if not amount and income:
                needs_review = True
                continue
            timestamps.append(row.settled_at)
            if income:
                # Settlement captures its business timestamp before inserting
                # the credit. Both facts must have occurred before the wait.
                timestamps.append(income.created_at)
                income_transaction_ids.append(income.pk)
        if records:
            # A paid segment must still be represented by the current funding
            # facts; do not silently treat a changed settled share as zero.
            needs_review = True
    # A player can bear another player's add-on refund using their main
    # earnings, wallet or deposit without ever owning that add-on's shares.
    # Select the actual refund participant instead of restricting the refund
    # to payments discovered through the player's add-on ownership.
    refunds = OrderAddonRefund.objects.filter(
        proposal__participants__account_id=account_id).distinct()
    for refund in refunds.select_related('payment__intent', 'proposal', 'buyer_transaction__wallet'):
        person = refund.proposal.participants.select_related('provider_transaction__wallet').get(account_id=account_id)
        buyer = refund.buyer_transaction
        debit = person.provider_transaction
        deficits = list(RefundDepositDeficit.objects.filter(addon_refund=refund, profile=profile,
            account_id=account_id).select_related('source_store_transaction__wallet__user'))
        if (refund.proposal.payment_id != refund.payment_id or refund.payment.intent.order_id != refund.order_id
                or refund.proposal.status != 'ACCEPTED' or refund.proposal.amount != refund.amount
                or person.burden != (person.addon_pending_deduction + person.main_pending_deduction
                                     + person.wallet_deduction + person.deposit_deduction)
                or not buyer or buyer.wallet.account_id != refund.payment.intent.order.customer_account_id
                or buyer.tx_type != Transaction.TxType.REFUND or buyer.status != Transaction.Status.SUCCESS
                or buyer.amount != refund.amount or buyer.balance_after != buyer.balance_before + buyer.amount
                or buyer.reversal_of_id or buyer.reversals.exists()
                or bool(debit) != bool(person.wallet_deduction)
                or len(deficits) != (1 if person.deposit_deduction else 0)
                or not refund.created_at or timezone.is_naive(refund.created_at)
                or refund.created_at > now or refund.created_at < refund.payment.paid_at):
            needs_review = True
            continue
        if debit and (not wallet or debit.wallet_id != wallet.pk or debit.wallet.account_id != account_id
                or (user_id is not None and debit.wallet.user_id != user_id) or debit.tx_type != Transaction.TxType.REFUND
                or debit.status != Transaction.Status.SUCCESS or debit.amount != -person.wallet_deduction
                or debit.balance_after != debit.balance_before + debit.amount
                or debit.reversal_of_id or debit.reversals.exists()):
            needs_review = True
            continue
        if deficits:
            from django.conf import settings
            deficit = deficits[0]
            source = deficit.source_store_transaction
            if (deficit.source_kind != 'DIRECT' or deficit.amount != person.deposit_deduction
                    or not source or source.order_id != refund.order_id
                    or source.deposit_owner_id != account_id
                    or source.deposit_business_cycle != deficit.account_business_cycle
                    or source.tx_type != Transaction.TxType.DEPOSIT_INCOME
                    or source.status != Transaction.Status.SUCCESS or source.amount != -deficit.amount
                    or not source.wallet.user or source.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                    or source.balance_after != source.balance_before + source.amount
                    or source.reversal_of_id or source.reversals.exists()):
                needs_review = True
                continue
        events = [refund.created_at, buyer.created_at]
        if debit:
            events.append(debit.created_at)
        if deficits:
            events.extend([deficits[0].created_at, deficits[0].source_store_transaction.created_at])
        if any(not value or timezone.is_naive(value) or value > now or value < refund.payment.paid_at
               for value in events):
            needs_review = True
        else:
            timestamps.extend(events)
    return {'pending': pending, 'timestamps': timestamps,
            'income_transaction_ids': income_transaction_ids, 'needs_review': needs_review}
