"""Current-read proof of a third-refund provider's wallet/deposit debits."""
from django.conf import settings
from django.core.exceptions import ValidationError


def verify(order, posting, share):
    proof = posting.points_refund
    if (share.assignment.order_id != order.pk or share.assignment.provider_account_id != share.account_id
            or share.pending_deduction != min(share.burden, share.original_income)
            or share.wallet_deduction != min(share.burden - share.pending_deduction, share.wallet_available_before)
            or share.deposit_deduction != share.burden - share.pending_deduction - share.wallet_deduction
            or share.deposit_deduction > share.deposit_paid_before):
        raise ValidationError('第三轮本人收益、钱包及押金承担顺序异常')
    terms = posting.proposal.points_terms
    if terms.addon_pending_plan is not None:
        if terms.round_context is not None:
            from .points_third_addon_responsibilities import current as current_responsibilities
        else:
            from .addon_handover import current_responsibilities
        from rest_framework.exceptions import ValidationError as RestValidationError
        records = list(share.addon_deductions.select_related('payment__intent', 'segment', 'addon_share')
                       .order_by('position'))
        plan = terms.addon_pending_plan
        if (terms.round_context is not None and isinstance(terms.round_context, dict)
                and 'provider_recoveries' in terms.round_context and isinstance(plan, list)):
            from .models import OrderParticipationSegment
            segment_ids = {item.get('segment_id') for item in plan if isinstance(item, dict)}
            owners = dict(OrderParticipationSegment.objects.filter(
                pk__in=segment_ids, attribution__order=order).values_list('pk', 'provider_account_id'))
            if set(owners) != segment_ids:
                raise ValidationError('多轮加购承担的履约段不属于本单')
            plan = [item for item in plan if owners[item['segment_id']] == share.account_id]
        actual = [{'payment_id': row.payment_id, 'segment_id': row.segment_id,
                   'addon_share_id': row.addon_share_id, 'income_before': row.income_before,
                   'amount': row.amount} for row in records]
        if (not isinstance(plan, list) or actual != plan
                or [row.position for row in records] != list(range(len(records)))
                or sum(row.amount for row in records) + sum(
                    share.segment_deductions.values_list('amount', flat=True)) != share.pending_deduction):
            raise ValidationError('第三轮已确认逐份额承担凭证不一致')
        for row in records:
            if (row.payment.intent.order_id != order.pk or row.segment.attribution.order_id != order.pk
                    or row.segment.provider_account_id != share.account_id
                    or row.payment.buyer_transaction.created_at >= posting.created_at):
                raise ValidationError('第三轮加购承担不属于本人实际付款及履约段')
            try:
                _, responsibilities = current_responsibilities(row.payment, before=posting.created_at, with_share_id=True)
            except RestValidationError as exc:
                raise ValidationError('第三轮加购历史责任待核对') from exc
            matching = [item for item in responsibilities if item['segment_id'] == row.segment_id
                        and item['provider_id'] == share.account_id and item.get('share_id') == row.addon_share_id]
            if len(matching) != 1 or matching[0]['income'] != row.income_before or row.amount > row.income_before:
                raise ValidationError('第三轮加购原始份额或实际余额不一致')
    elif share.addon_deductions.exists():
        raise ValidationError('第三轮存在未确认的加购承担')
    wallet_tx, deficit = share.wallet_transaction, share.deposit_deficit
    if share.wallet_deduction:
        if (not wallet_tx or wallet_tx.order_id != order.pk or wallet_tx.wallet.account_id != share.account_id
                or wallet_tx.wallet.user_id != share.assignment.provider_id or wallet_tx.tx_type != 'REFUND'
                or wallet_tx.status != 'SUCCESS' or wallet_tx.amount != -share.wallet_deduction
                or wallet_tx.balance_before != share.wallet_available_before
                or wallet_tx.balance_after != wallet_tx.balance_before + wallet_tx.amount
                or wallet_tx.operator_account_id != posting.operator_id or wallet_tx.operator_id != proof.refund.operator_id
                or wallet_tx.reversal_of_id or wallet_tx.reversals.exists()):
            raise ValidationError('第三轮本人钱包扣款流水异常')
    elif wallet_tx:
        raise ValidationError('第三轮存在多余钱包扣款')
    if share.deposit_deduction:
        store_tx = deficit.source_store_transaction if deficit else None
        if (not deficit or deficit.refund_id != proof.refund_id or deficit.addon_refund_id
                or deficit.source_kind != 'DIRECT' or deficit.source_provider_transaction_id
                or deficit.profile.account_id != share.account_id or deficit.profile.user_id != share.assignment.provider_id
                or deficit.account_id != share.account_id or deficit.amount != share.deposit_deduction
                or deficit.direct_key != f'{proof.refund_id}:{deficit.profile_id}' or not store_tx
                or store_tx.order_id != order.pk or store_tx.tx_type != 'DEPOSIT_INCOME'
                or store_tx.status != 'SUCCESS' or store_tx.amount != -deficit.amount
                or store_tx.balance_after != store_tx.balance_before + store_tx.amount
                or not store_tx.wallet.user_id or store_tx.wallet.user.username != settings.PLATFORM_SYSTEM_USERNAME
                or store_tx.operator_account_id != posting.operator_id or store_tx.operator_id != proof.refund.operator_id
                or store_tx.reversal_of_id or store_tx.reversals.exists()):
            raise ValidationError('第三轮本人押金扣款或缺口流水异常')
    elif deficit:
        raise ValidationError('第三轮存在多余押金缺口')
