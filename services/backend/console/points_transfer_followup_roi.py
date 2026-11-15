"""Period costs for verified two-person transferred points refunds."""
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from stores.db import transaction


def costs(order_id, start, end):
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation
    from orders.models import (Order, OrderAddonPayment, OrderOriginalShareTransfer,
                               OrderPointsTransferFollowupFundingRevision, OrderSegmentSettlementManifest)
    from orders.points_funding_plan import PointsFundingPlanError
    from orders.points_transfer_refund_access import after_transfer
    from orders.points_transfer_followup_termination import verify_closed_or_settled_locked
    from orders.referral_evidence import paid_referral_credits, ReferralEvidenceError
    from orders.segment_settlement import verify_finalized
    from orders.settlement import SettlementError
    try:
        with transaction.atomic():
            if getattr(settings, 'PAYROLL_EVENT_BARRIER_INTEGRATION_ENABLED', False):
                from console.payroll_event_barrier import lock_payment_refund_events
                lock_payment_refund_events()
            with after_transfer(order_id, phase='settlement') as order:
                if (order.is_gift_snapshot or not order.amount
                        or order.required_provider_count != 2
                        or not OrderOriginalShareTransfer.objects.filter(
                            previous_segment__attribution__order=order).exists()):
                    return None
                revisions = list(OrderPointsTransferFollowupFundingRevision.objects.select_for_update()
                    .select_related('first_posting__points_refund__refund', 'posting__points_refund__refund',
                                    'posting__proposal').filter(posting__points_refund__redemption__order=order)[:2])
                if len(revisions) != 1:
                    return None
                revision = revisions[0]
                first, second = revision.first_posting, revision.posting
                refunds = list(order.refund_records.order_by('created_at', 'pk'))
                prior = revision.prior_manifest
                if (len(refunds) != 2 or [first.points_refund.refund_id, second.points_refund.refund_id]
                        != [row.pk for row in refunds] or (prior is not None
                            and (not isinstance(prior, dict) or len(prior.get('people', [])) != 1))):
                    return None
                first_shares, second_shares = list(first.shares.all()), list(second.shares.all())
                paid_person = prior['people'][0] if prior else None
                second_accounts = [share.account_id for share in second_shares]
                if (not first_shares or not second_shares or len(set(second_accounts)) != len(second_accounts)
                        or set(second_accounts) != set(second.proposal.affected_accounts)
                        or (paid_person and paid_person.get('account_id') in second_accounts)):
                    return None
                verify_closed_or_settled_locked(second)
                manifest = OrderSegmentSettlementManifest.objects.select_for_update().filter(order=order).first()
                if not manifest:
                    return None
                partial_evidence = None
                if order.settled_at:
                    plan = verify_finalized(order, manifest)
                else:
                    from .points_transfer_partial_roi import verified_partial
                    plan, partial_evidence = verified_partial(order, manifest, start, end)
                addon_costs = {'provider': 0, 'gift_compensation': 0}
                addons = list(OrderAddonPayment.objects.select_for_update().filter(intent__order=order)
                              .select_related('intent', 'buyer_transaction__wallet').order_by('pk'))
                if addons:
                    from orders.points_transfer_followup_scope import addon_snapshot, addon_chain_before
                    from orders.points_transfer_followup_addon_version import latest_run_locked
                    latest = latest_run_locked(order, revision)
                    if latest:
                        history = latest.points_followup_snapshot['addon_history']
                    else:
                        history = addon_snapshot(order, first)
                        if not history:
                            history = addon_chain_before(order, first, second.created_at)
                    from .points_transfer_multi_addon_roi import period_costs
                    addon_costs = period_costs(order, manifest, history, addons, start, end,
                        main_event_at={first.created_at, second.created_at,
                                       refunds[0].created_at, refunds[1].created_at})
                if refunds[1].recovered_amount != sum(
                        share.wallet_deduction + share.deposit_deduction for share in second_shares):
                    return None
                for share in second_shares:
                    if share.pending_deduction + share.wallet_deduction + share.deposit_deduction != share.burden:
                        return None
                    wallet_tx = share.wallet_transaction
                    if share.wallet_deduction:
                        if (not wallet_tx or wallet_tx.order_id != order.pk or wallet_tx.tx_type != 'REFUND'
                                or wallet_tx.status != 'SUCCESS' or wallet_tx.wallet.account_id != share.account_id
                                or wallet_tx.amount != -share.wallet_deduction or wallet_tx.reversal_of_id
                                or wallet_tx.reversals.exists()
                                or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount):
                            return None
                    elif wallet_tx:
                        return None
                    deficit = share.deposit_deficit
                    if share.deposit_deduction:
                        store_tx = deficit.source_store_transaction if deficit else None
                        if (not deficit or deficit.refund_id != refunds[1].pk or deficit.account_id != share.account_id
                                or deficit.source_kind != 'DIRECT' or deficit.amount != share.deposit_deduction
                                or not store_tx or store_tx.order_id != order.pk or store_tx.tx_type != 'DEPOSIT_INCOME'
                                or store_tx.status != 'SUCCESS' or store_tx.amount != -share.deposit_deduction
                                or store_tx.reversal_of_id or store_tx.reversals.exists()
                                or store_tx.balance_after != store_tx.balance_before + store_tx.amount
                                or not store_tx.wallet.user_id
                                or store_tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME):
                            return None
                    elif deficit:
                        return None
                proof = first.points_refund
                payment = proof.redemption.payment
                if (not payment or payment.order_id != order.pk or payment.tx_type != 'PAY'
                        or payment.status != 'SUCCESS' or payment.amount != -order.amount
                        or payment.wallet.account_id != order.customer_account_id
                        or payment.wallet.user_id != order.customer_id or payment.reversal_of_id
                        or payment.reversals.exists() or payment.balance_after != payment.balance_before + payment.amount
                        or payment.created_at > refunds[0].created_at or refunds[0].created_at > refunds[1].created_at):
                    return None
                sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
                if not sources or sum(row.amount for row in sources) != order.amount:
                    return None
                validate_order_funding_sources(order, sources)
                for kind in ('prepaid', 'gift', 'compensation'):
                    if sum(row.refunded_amount for row in sources if row.kind == kind.upper()) != sum(
                            getattr(post.points_refund, f'{kind}_units') for post in (first, second)):
                        return None
                rate = order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0
                if type(rate) is not int or not 0 <= rate <= 100:
                    return None
                credits = paid_referral_credits(order, list(order.transactions.filter(status='SUCCESS',
                    tx_type__in=['INCOME', 'GIFT'], amount__gt=0, reversal_of__isnull=True).select_related('wallet')))
                if (len(credits) > 1 or any(tx.reversals.exists()
                        or tx.balance_after != tx.balance_before + tx.amount for tx in credits)):
                    return None
                credit = credits[0] if credits else None
                if (credit and (manifest.inviter_transaction_id != credit.pk or credit.amount != plan.inviter_income)
                        or (not credit and (manifest.inviter_transaction_id or (order.settled_at and plan.inviter_income)))):
                    return None
                running = order.amount
                from .roi_period import points_refund_event_time
                referral = running * rate // 100 if start <= payment.created_at < end else 0
                provider = sum(item.income for item in plan.segments) if start <= payment.created_at < end else 0
                gift = sum(row.amount for row in sources if row.kind in ('GIFT', 'COMPENSATION')) if start <= payment.created_at < end else 0
                for posting, refund in zip((first, second), refunds):
                    refund_at = points_refund_event_time(posting.points_refund)
                    if refund.amount > running:
                        return None
                    delta = running * rate // 100 - (running - refund.amount) * rate // 100
                    running -= refund.amount
                    if start <= refund_at < end:
                        provider -= sum(posting.shares.values_list('burden', flat=True))
                        if not credit or credit.created_at > refund_at:
                            referral -= delta
                        gift -= posting.points_refund.gift_units + posting.points_refund.compensation_units
                # A paid commission must equal the obligation at its actual
                # payment time; later refunds never recover it.
                if credit:
                    refund_times = [points_refund_event_time(post.points_refund) for post in (first, second)]
                    basis = order.amount - sum(row.amount for row, at in zip(refunds, refund_times) if at < credit.created_at)
                    if (credit.created_at < payment.created_at
                            or any(at == credit.created_at for at in refund_times)
                            or credit.amount != basis * rate // 100):
                        return None
                result = {'provider': provider + addon_costs['provider'], 'referral': referral,
                          'gift_compensation': gift + addon_costs['gift_compensation']}
                if partial_evidence:
                    result['verified_settlement'] = partial_evidence
                elif order.settled_at and addons:
                    # Finalized verification above checks the actual main and
                    # add-on postings; report their own wallet posting times.
                    from django.utils import timezone
                    people = list(manifest.people.select_related('income_transaction'))
                    addon_records = list(manifest.addon_settlements.select_related('income_transaction'))
                    transactions = [row.income_transaction for row in people if row.income_transaction_id]
                    transactions += [row.income_transaction for row in addon_records if row.income_transaction_id]
                    result['verified_settlement'] = {
                        'order_id': order.pk, 'observed_at': timezone.now().isoformat(),
                        'status': 'SETTLED',
                        'settled_segment_ids': sorted({pk for row in people for pk in row.segment_ids}),
                        'income_posted_in_period_units': sum(
                            tx.amount for tx in transactions if start <= tx.created_at < end),
                        'pending_segment_id': None, 'pending_main_units': 0, 'pending_addon_units': 0,
                    }
                return result
    except (DjangoValidationError, ValidationError, ObjectDoesNotExist, PointsFundingPlanError,
            SettlementError, ReferralEvidenceError, ValueError, TypeError, KeyError):
        return None
