"""Rebuild whole-order funding after a transferred negotiation round."""
from dataclasses import replace

from rest_framework.exceptions import ValidationError
from stores.db import transaction

from .models import NegotiationExecution, OrderAddonPayment, OrdinaryTransferFollowupRound
from .points_funding_plan import PointsFundingPlanError


def plan_locked(round_id, *, allocated, boundary=None):
    if not transaction.get_connection().in_atomic_block or type(allocated) is not int or allocated < 0:
        raise PointsFundingPlanError('追加协商补资须在订单事务内核对')
    row = (OrdinaryTransferFollowupRound.objects.select_for_update().select_related('order', 'execution__refund')
           .get(pk=round_id))
    order, execution, facts = row.order, row.execution, row.facts
    from .ordinary_transfer_followup import _hash
    if (not execution or row.facts_hash != _hash(row.proposal_id, facts)
            or facts.get('order_id') != order.pk or facts.get('previous_execution_id') != row.previous_id):
        raise PointsFundingPlanError('追加协商逐轮资金凭证缺失')
    from .negotiation_execution import verify_execution
    try:
        if execution.refund_id:
            verify_execution(execution)
        else:
            from .negotiation_zero import verify_zero_execution as verify_zero
            verify_zero(execution)
        prior_ids = facts['prior_execution_ids']
        prior = list(NegotiationExecution.objects.select_for_update().filter(pk__in=prior_ids).order_by('pk'))
        if len(prior) != len(set(prior_ids)) or row.previous_id not in prior_ids:
            raise ValidationError('追加协商前序凭证缺失')
        for receipt in prior:
            if receipt.refund_id:
                from .models import OrdinaryPostRefundTransferRevision, OrderOriginalShareTransfer
                pretransfer = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__positive_pretransfer_execution_id=receipt.pk).exists()
                if pretransfer:
                    from .ordinary_positive_pretransfer import verify as verify_positive_pretransfer
                    from django.core.exceptions import ValidationError as DjangoValidationError
                    transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    try:
                        original = verify_positive_pretransfer(order, before=transfer.created_at) if transfer else None
                    except DjangoValidationError as exc:
                        raise ValidationError('转单前正退款原协商凭证异常') from exc
                    if not original or original.pk != receipt.pk:
                        raise ValidationError('转单前正退款原协商凭证异常')
                else:
                    verify_execution(receipt)
            else:
                from .negotiation_zero import marked_execution_id
                from .models import OrdinaryPostRefundTransferRevision
                pretransfer = OrdinaryPostRefundTransferRevision.objects.filter(order=order,
                    snapshot__zero_pretransfer_execution_id=receipt.pk).exists()
                if receipt.pk == marked_execution_id(order) and pretransfer:
                    from .models import OrderOriginalShareTransfer
                    from .ordinary_zero_pretransfer import verify as verify_pretransfer_zero
                    transfer = OrderOriginalShareTransfer.objects.filter(
                        previous_segment__attribution__order=order).order_by('created_at', 'pk').first()
                    if not transfer or not verify_pretransfer_zero(order, before=transfer.created_at):
                        raise ValidationError('首次零退款转单前凭证缺失')
                else:
                    from .negotiation_zero import verify_zero_execution as verify_zero
                    verify_zero(receipt)
        from django.utils import timezone
        from .models import OrderOriginalShareTransfer
        event_boundary = boundary or timezone.now()
        if OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order,
                created_at__gt=execution.termination_processed_at, created_at__lt=event_boundary).exists():
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .ordinary_followup_transferred_addon_plan import plan_at
            try:
                return plan_at(order, row, boundary=event_boundary, allocated=allocated)
            except DjangoValidationError as exc:
                raise ValidationError('追加协商后转单加购资金待核对') from exc
        refund_query = order.refund_records.select_for_update()
        if boundary is not None:
            if refund_query.filter(created_at=boundary).exists():
                raise ValidationError('退款与补资释放时序待核对')
            refund_query = refund_query.filter(created_at__lt=boundary)
        refunds = list(refund_query.order_by('pk'))
        if ({refund.pk for refund in refunds} != {item.refund_id for item in [*prior, execution] if item.refund_id}
                or sum(item.amount for item in refunds) != facts['prior_refund_units'] +
                   (execution.refund.amount if execution.refund_id else 0)):
            raise ValidationError('主单累计退款不一致')
        sources = list(order.funding_allocations.select_for_update().select_related('lot').order_by('pk'))
        from engagement.funding_wallet import validate_order_funding_sources
        validate_order_funding_sources(order, sources)
        paid = {kind: sum(item.amount for item in sources if item.kind == kind)
                for kind in ('PREPAID', 'GIFT', 'COMPENSATION')}
        returned = {kind: sum(item.refunded_amount for item in sources if item.kind == kind) for kind in paid}
        if boundary is not None:
            from engagement.funding_plan import RefundablePart, plan_refund
            before = {item['id']: item for item in facts['funding_sources']}
            if set(before) != {item.pk for item in sources}:
                raise ValidationError('退款前的付款来源缺失')
            delta = plan_refund([RefundablePart(item.pk, item.kind, item.amount,
                                before[item.pk]['refunded_amount']) for item in sources],
                                execution.refund.amount if execution.refund_id else 0)
            returned = {kind: sum(before[item.pk]['refunded_amount'] + delta[item.pk]
                                  for item in sources if item.kind == kind) for kind in paid}
        if sum(paid.values()) != order.amount or sum(returned.values()) != sum(item.amount for item in refunds):
            raise ValidationError('主单累计退款来源不一致')
        shares = list(execution.shares.select_for_update())
        prior_external = sum(share.wallet_deduction + share.deposit_deduction
                             for receipt in prior for share in receipt.shares.select_for_update())
        current_external = sum(share.wallet_deduction + share.deposit_deduction for share in shares)
        addon_deductions = {int(key): value for key, value in facts['addon_main_deductions'].items()}
        retained = {int(key): value for key, value in facts['outstanding_income'].items()}
        for share in shares:
            retained[share.account_id] -= share.pending_deduction
        from .addon_main_deduction import deductions_at
        current_deductions = deductions_at(order, before=boundary)
        if any(current_deductions.get(account, 0) < amount for account, amount in addon_deductions.items()):
            raise ValidationError('加购退款的主单扣减记录已变化')
        for account, amount in current_deductions.items():
            extra = amount - addon_deductions.get(account, 0)
            if account not in retained or extra < 0:
                raise ValidationError('加购退款的主单收益归属待核对')
            retained[account] -= extra
        addon_deductions = current_deductions
        if any(value < 0 for value in retained.values()):
            raise ValidationError('追加退款后的陪玩收益异常')
        # The recorded outstanding earnings already exclude add-on deductions;
        # add them back here because the independent add-on history applies
        # those deductions exactly once when constructing the whole order.
        provider_before_addon = sum(retained.values()) + sum(addon_deductions.values())
        from .points_negotiation_preview import funding_after_refund
        main = funding_after_refund(principal_units=paid['PREPAID'], gift_units=paid['GIFT'],
            compensation_units=paid['COMPENSATION'], returned_prepaid_units=returned['PREPAID'],
            returned_gift_units=returned['GIFT'], returned_compensation_units=returned['COMPENSATION'],
            provider_income_units=provider_before_addon, pending_deduction_units=0,
            provider_external_units=prior_external + current_external,
            inviter_rate=order.inviter_commission_rate_snapshot if order.inviter_id or order.inviter_account_id else 0,
            allocated_units=allocated)
        history = {'addon_history': []}
        if OrderAddonPayment.objects.filter(intent__order=order).exists():
            from django.utils import timezone
            from django.core.exceptions import ValidationError as DjangoValidationError
            from .models import OrderPointsFundingAllocation
            from .ordinary_transfer_addon_history import verify_handovers
            history_boundary = boundary or timezone.now()
            original = prior[0]
            if order.addon_refunds.filter(created_at__lt=original.created_at).exists():
                from .ordinary_multi_prior_addon_facts import verify as verify_prior_addon_refunds
                try:
                    verify_prior_addon_refunds(order, original.created_at)
                except DjangoValidationError as exc:
                    raise ValidationError('主单协商前的加购退款资金历史待核对') from exc
            witness = OrderPointsFundingAllocation.objects.filter(service_order=order).order_by('receipt_id', 'pk').values_list('receipt_id', flat=True).first()
            for addon in OrderAddonPayment.objects.filter(intent__order=order,
                    buyer_transaction__created_at__lt=history_boundary):
                try:
                    verify_handovers(order, addon, before=history_boundary, witness_receipt_id=witness)
                except DjangoValidationError as exc:
                    raise ValidationError('追加协商的加购接替资金历史待核对') from exc
            from .addon_funding_history import combined_at
            lines = facts['segment_lines']
            segments = [(line['segment_id'], line['account_id'], line['basis'], line['fee'], line['income'])
                        for line in lines]
            history = combined_at(order, main, history_boundary, segment_shares=segments)
            from .refunded_addon_funding import combine_refunded_history
            # The history deducts the add-on share from the pre-add-on main
            # amount, so its limit is the retained amount plus that share.
            limits = {account: value + addon_deductions.get(account, 0) for account, value in retained.items()}
            combined = (combine_refunded_history(main, history, main_retained_by_account=limits)
                        if history.get('addon_history') else main)
        else:
            combined = main
        addon_refunds = order.addon_refunds.all()
        if boundary is not None:
            addon_refunds = addon_refunds.filter(created_at__lt=boundary)
        rounding = (sum(item.rounding_to_store_units for item in [*prior, execution])
                    + sum(addon_refunds.values_list('rounding_surplus', flat=True)))
        extra = max(rounding - combined.store_income_units, 0)
        if extra:
            required = combined.required_units + extra
            combined = replace(combined, store_income_units=combined.store_income_units + extra,
                required_units=required, release_units=max(allocated - required, 0),
                remaining_units=max(required - allocated, 0))
        return combined, history
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        raise PointsFundingPlanError('追加协商的逐轮资金凭证待核对') from exc
