"""Historical funding facts for ordinary add-ons after a pre-service handoff.

This is evidence preparation, not a capability grant or a funds operation.
"""
import hashlib
import json
from dataclasses import asdict

from rest_framework.exceptions import ValidationError
from stores.db import transaction


def capture(run):
    """Rebuild the exact combined obligation at one paid/refunded event."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('加购补资历史核验必须在门店事务内执行')
    if sum(bool(value) for value in (run.payment_id, run.refund_id, run.handover_id)) != 1 or not run.created_at:
        raise ValidationError('加购补资事件不完整')
    payment = run.payment if run.payment_id else run.refund.payment if run.refund_id else run.handover.payment
    order = payment.intent.order
    if run.refund_id and run.refund.order_id != order.pk:
        raise ValidationError('加购退款与订单不一致')
    from .ordinary_prestart_addon import supports as prestart_supported
    from .ordinary_untransferred_scope import supports as original_seats_supported
    from .models import OrderOriginalShareTransfer
    first_transfer = OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
    historical_transfer = False
    if first_transfer and run.created_at < first_transfer.created_at:
        from .negotiation_zero import marked_execution_id
        zero_id = marked_execution_id(order)
        if zero_id:
            from .ordinary_zero_pretransfer import verify as verify_first
            original = verify_first(order, before=first_transfer.created_at)
        elif order.refund_records.filter(created_at__lt=first_transfer.created_at).exists():
            from .ordinary_positive_pretransfer import verify as verify_first
            original = verify_first(order, before=first_transfer.created_at)
        else:
            original = None
        if original:
            historical_transfer = original.created_at > run.created_at
        elif not order.refund_records.filter(created_at__lt=first_transfer.created_at).exists():
            # The original add-on event may precede an ordinary service transfer
            # without any preceding main refund.  Replay the real transfer chain
            # before selecting the segments that existed at this event; today's
            # assignments and retained bases have already changed.
            from datetime import timedelta
            from .ordinary_transfer_event_scope import segment_lines_at
            from .models import OrderOriginalShareTransfer
            last_transfer = (OrderOriginalShareTransfer.objects.filter(
                previous_segment__attribution__order=order).order_by('-created_at', '-pk').first())
            segment_lines_at(order, last_transfer.created_at + timedelta(microseconds=1), allow_unstarted=True)
            historical_transfer = True
    if not (historical_transfer or prestart_supported(order) or original_seats_supported(order, boundary=run.created_at)):
        raise ValidationError('服务前改派的陪玩及收益快照待核对')
    # This version records events before any main refund. Later refunds may
    # change allocation counters but must not rewrite the original payment.
    if order.refund_records.filter(created_at__lte=run.created_at).exists():
        raise ValidationError('主单退款前的加购补资时序待核对')
    if payment.buyer_transaction.created_at >= run.created_at:
        raise ValidationError('加购付款与补资时序待核对')
    if run.refund_id and run.refund.created_at >= run.created_at:
        raise ValidationError('加购退款与补资时序待核对')
    if run.handover_id:
        from .ordinary_team_pre_main_handover import verify as verify_handover
        verify_handover(run.handover)
        if run.handover.accepted_at >= run.created_at:
            raise ValidationError('加购接替与补资时序待核对')
    from .ordinary_team_pre_main_handover import verify as verify_team_handover
    from .models import OrderAddonHandover
    for handover in OrderAddonHandover.objects.filter(payment__intent__order=order,
            status='ACCEPTED', accepted_at__lt=run.created_at).select_related(
                'payment__intent__order', 'source_segment__attribution', 'target_segment__attribution'):
        verify_team_handover(handover)
    from .models import OrderProvider
    from .points_funding_plan import plan_service_funding
    from .settlement import ProviderShare
    from engagement.funding_wallet import validate_order_funding_sources
    from engagement.models import OrderFundingAllocation, OrderPointAllocation
    from wallet.models import Transaction
    from club_accounts.models import LegacyAccountMap
    from .addon_funding_history import combined_at
    from .models import OrderSegmentFinancialSnapshot
    # This reconstruction is also called while holding a shared receipt lock.
    # Read facts without acquiring an order/profile/wallet lock in reverse.
    payments = list(Transaction.objects.filter(order=order, tx_type=Transaction.TxType.PAY,
        status=Transaction.Status.SUCCESS).select_related('wallet'))
    if (len(payments) != 1 or payments[0].wallet.account_id != order.customer_account_id
            or payments[0].wallet.user_id != order.customer_id or payments[0].amount != -order.amount
            or payments[0].reversal_of_id or payments[0].reversals.exists()
            or payments[0].balance_after != payments[0].balance_before + payments[0].amount):
        raise ValidationError('主单付款流水待核对')
    from django.db.models import Q
    snapshots = list(OrderSegmentFinancialSnapshot.objects.filter(
        segment__attribution__order=order, segment__accepted_at__lt=run.created_at)
        .filter(Q(segment__released_at__isnull=True) | Q(segment__released_at__gt=run.created_at))
        .select_related('segment').order_by('segment__slot'))
    if historical_transfer:
        from types import SimpleNamespace
        current_accounts = {row.provider_account_id: row for row in OrderProvider.objects.filter(order=order)}
        if (len(snapshots) != order.required_provider_count
                or len({row.segment.provider_account_id for row in snapshots}) != len(snapshots)):
            raise ValidationError('加购事件的原履约段快照待核对')
        assignments = []
        for snap in snapshots:
            owner = current_accounts.get(snap.segment.provider_account_id)
            if not owner:
                raise ValidationError('加购事件的原陪玩身份待核对')
            assignments.append(SimpleNamespace(pk=owner.pk, provider_id=owner.provider_id,
                provider_account_id=owner.provider_account_id, settlement_base=snap.incoming_basis,
                commission_type=snap.commission_type, commission_rate=snap.commission_rate,
                commission_fixed=snap.commission_fixed, provider_income=snap.income_at_entry))
        assignments.sort(key=lambda item: item.pk)
    else:
        assignments = list(OrderProvider.objects.filter(order=order, created_at__lt=run.created_at)
            .filter(Q(released_at__isnull=True) | Q(released_at__gt=run.created_at)).order_by('pk'))
    if any(not row.provider_id or not LegacyAccountMap.objects.filter(account_id=row.provider_account_id,
               legacy_user_id=row.provider_id).exists() for row in assignments):
        raise ValidationError('陪玩账号归属待核对')
    sources = list(OrderFundingAllocation.objects.filter(order=order).select_related('lot'))
    if not sources or OrderPointAllocation.objects.filter(order=order).exists():
        raise ValidationError('主单付款来源待核对')
    validate_order_funding_sources(order, sources)
    if sum(row.amount for row in sources) != order.amount:
        raise ValidationError('主单付款来源金额待核对')
    shares_plan = [ProviderShare(row.settlement_base, row.commission_type, row.commission_rate,
                   row.commission_fixed, row.provider_income) for row in assignments]
    main = plan_service_funding(deal_units=order.amount, point_discount_units=0, shares=shares_plan,
        inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
        principal_units=sum(row.amount for row in sources if row.kind == 'PREPAID'),
        gift_units=sum(row.amount for row in sources if row.kind == 'GIFT'),
        compensation_units=sum(row.amount for row in sources if row.kind == 'COMPENSATION'))
    if (main.inviter_income_units != order.inviter_commission
            or (not historical_transfer and
                order.shop_income != max(order.amount - order.provider_income - order.inviter_commission, 0))):
        raise ValidationError('主单分账快照待核对')
    shares = [(row.segment_id, row.segment.provider_account_id, row.incoming_basis,
               row.incoming_basis - row.income_at_entry, row.income_at_entry) for row in snapshots]
    historical = combined_at(order, main, run.created_at, segment_shares=shares)
    margin = historical['store_income_units'] - historical['additional_store_funding_units']
    rounding = sum(order.addon_refunds.filter(created_at__lt=run.created_at)
                   .values_list('rounding_surplus', flat=True))
    historical['additional_store_funding_units'] = max(rounding - margin, 0)
    historical['store_income_units'] = margin + historical['additional_store_funding_units']
    if historical.get('additional_store_funding_units') != run.required_amount:
        raise ValidationError('加购补资金额与历史分账不一致')
    facts = {'kind': 'ORDINARY_PRESTART_ADDON_V1', 'order_id': order.pk,
        'payment_id': run.payment_id, 'refund_id': run.refund_id,
        'boundary': run.created_at.isoformat(), 'main': asdict(main),
        'shares': [list(row) for row in shares], 'funding': historical}
    if run.handover_id:
        facts['handover_id'] = run.handover_id
    evidence = getattr(run.refund, 'zero_wallet_evidence', None) if run.refund_id else None
    if evidence:
        facts['zero_wallet'] = [evidence.pk, evidence.wallet_id, evidence.account_id, evidence.business_cycle,
                                evidence.balance, evidence.frozen_amount, evidence.is_active,
                                evidence.created_at.isoformat()]
    participant_evidence = (list(run.refund.participant_zero_wallet_evidence.order_by('participant_id'))
                            if run.refund_id else [])
    if participant_evidence:
        facts['participant_zero_wallets'] = [[row.pk, row.participant_id, row.wallet_id, row.account_id,
            row.business_cycle, row.balance, row.frozen_amount, row.is_active, row.created_at.isoformat()]
            for row in participant_evidence]
    return facts


def digest(facts):
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()
