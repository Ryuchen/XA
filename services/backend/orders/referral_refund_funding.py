"""Receipt-backed store liability for already-paid referral commission."""
from django.conf import settings
from django.core.exceptions import ValidationError
from stores.db import transaction

from .models import (Order, OrderRefund, ReferralRefundFunding, ReferralRefundFundingAction,
                     ReferralRefundFundingRelease)


def enabled():
    return (getattr(settings, 'REFERRAL_REFUND_FUNDING_ENABLED', False) is True
            and getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
            and transaction.get_connection().vendor == 'mysql')


def continuation_enabled():
    return (getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True
            and transaction.get_connection().vendor == 'mysql')


def related_receipt_ids(order):
    from django.db.models import Q
    from .models import OrderPointsFundingAllocation
    ids = set(ReferralRefundFunding.objects.filter(order=order).values_list('receipt_id', flat=True))
    ids.update(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    return ids


def _verified_platform_completion(item, action):
    import json
    from uuid import UUID
    from stores.context import current_store
    from stores.models import PlatformReferralFundingOperation
    from stores.platform_pass_repayment import _claimed_receipt, _sha
    from stores.platform_points_funding import _verified_receipt
    store = current_store()
    try:
        if not store or not item.request_key.startswith('referral:'):
            raise ValueError('invalid binding')
        operation_id = UUID(hex=item.request_key[len('referral:'):])
        operation = PlatformReferralFundingOperation.objects.select_related('claim__store').get(pk=operation_id)
        expected = _sha(json.dumps([str(operation.claim_id), item.order_id, item.amount,
                                    item.target_refund_amount], separators=(',', ':')))
        if (operation.claim.store.slug != store.slug or operation.claim.voided_at is not None
                or operation.claim.tenant_receipt_id != item.receipt_id or operation.order_id != item.order_id
                or operation.amount != item.amount or str(operation.actor_id) != item.allocated_by
                or operation.target_refund_amount != item.target_refund_amount
                or operation.payload_digest != expected or operation.tenant_action_id != action.pk
                or not operation.completed_at or operation.rejected_at):
            raise ValueError('invalid binding')
        receipt = _claimed_receipt(operation.claim, store.slug)
        _verified_receipt(operation.claim, receipt)
    except Exception as exc:
        # A control-plane outage or pending intent never authorizes a refund.
        raise ValidationError('平台到账补资操作尚未完成或无法核验') from exc


def required_for_refund(order, refund_amount):
    from .referral_evidence import paid_referral_credits, ReferralEvidenceError
    from .refunds import RefundError, posted_settlement, proportional_targets
    from .points_service_payment import needs_integrated_processing
    from .direct_payment_models import DirectPaymentIntent
    from .models import OrderOriginalShareTransfer, OrderPrestartHandoff
    from engagement.confirmation import refund_within_aftersale_window
    if (not posted_settlement(order) or order.payment_status not in (Order.PaymentStatus.PAID, Order.PaymentStatus.PARTIALLY_REFUNDED)
            or order.status != Order.Status.COMPLETED
            or needs_integrated_processing(order) or DirectPaymentIntent.objects.filter(order=order).exists()
            or not refund_within_aftersale_window(order)
            or order.addon_intents.exists() or order.transfer_limit_seats.filter(transfer_count__gt=0).exists()
            or OrderPrestartHandoff.objects.filter(order=order).exists()
            or OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()):
        raise ValidationError('该订单的推荐佣金退款来源尚待核对')
    from .segment_settlement import reject_legacy_refund
    try:
        reject_legacy_refund(order)
    except RefundError as exc:
        raise ValidationError('逐段收益的推荐佣金退款尚待核对') from exc
    credits = list(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True,
        tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).order_by('pk'))
    try:
        referral = paid_referral_credits(order, credits, expected_amount=order.inviter_commission)
    except ReferralEvidenceError as exc:
        raise ValidationError(str(exc)) from exc
    if (sum(row.amount for row in credits) != order.amount
            or order.transactions.filter(status='SUCCESS', reversal_of__in=referral).exists()):
        raise ValidationError('已发推荐佣金或原结算流水待核对')
    refunds = list(order.refund_records.order_by('pk'))
    if refunds:
        verify_referral_history(order, credits, referral, refunds)
    prior = sum(row.amount for row in refunds)
    if type(refund_amount) is not int or refund_amount <= 0 or prior + refund_amount > order.amount:
        raise ValidationError('退款金额已变化')
    if not referral:
        return 0
    weights = {row.pk: row.amount for row in credits}
    previous, target = proportional_targets(weights, prior), proportional_targets(weights, prior + refund_amount)
    return sum(target[row.pk] - previous[row.pk] for row in referral)


def reserve_locked(*, order_id, receipt_id, amount, refund_amount, request_key, actor, resume=False):
    existing_snapshot = ReferralRefundFunding.objects.filter(request_key=request_key).exists()
    if (not transaction.get_connection().in_atomic_block
            or not (enabled() or ((existing_snapshot or resume) and continuation_enabled()))):
        raise ValidationError('推荐佣金退款补资尚未开放')
    from .financial_lock import lock_order_money
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import record_referral_locked, verify_allocations_locked
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('订单不存在')
    lock_order_money()
    receipt_ids = related_receipt_ids(order)
    receipt_ids.add(receipt_id)
    receipts = {pk: _locked_receipt(pk) for pk in sorted(receipt_ids)}
    for pk in sorted(receipt_ids):
        verify_allocations_locked(pk)
    receipt = receipts[receipt_id]
    existing = ReferralRefundFunding.objects.select_for_update().filter(request_key=request_key).first()
    if existing:
        action = ReferralRefundFundingAction.objects.filter(allocation=existing).first()
        if (existing.order_id != order.pk or existing.receipt_id != receipt.pk or existing.amount != amount
                or existing.target_refund_amount != refund_amount or existing.allocated_by != actor or not action or action.actor != actor
                or action.request_key != request_key or action.result != {'receipt_id': receipt.pk,
                    'allocation_id': existing.pk, 'order_id': order.pk, 'amount': amount, 'refund_amount': refund_amount}):
            raise ValidationError('原补资操作凭据不一致')
        verify_allocations_locked(receipt.pk)
        return existing, action
    if (type(amount) is not int or amount <= 0 or amount != required_for_refund(order, refund_amount)
            or ReferralRefundFunding.objects.filter(order=order, refund__isnull=True, release__isnull=True).exists()):
        raise ValidationError('推荐佣金补资金额或订单状态已变化')
    allocation = ReferralRefundFunding.objects.create(order=order, receipt=receipt, amount=amount,
        target_refund_amount=refund_amount,
        request_key=request_key, allocated_by=actor)
    result = {'receipt_id': receipt.pk, 'allocation_id': allocation.pk, 'order_id': order.pk,
              'amount': amount, 'refund_amount': refund_amount}
    action = ReferralRefundFundingAction.objects.create(request_key=request_key, allocation=allocation,
                                                         actor=actor, result=result)
    record_referral_locked(allocation)
    verify_allocations_locked(receipt.pk)
    return allocation, action


def prelock(order):
    """Acquire receipt before any wallet; caller holds order and money mutex."""
    allocations = list(ReferralRefundFunding.objects.filter(order=order).order_by('pk'))
    if not allocations:
        return None
    if not continuation_enabled():
        raise ValidationError('推荐佣金补资记录暂无法使用')
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import verify_allocations_locked
    for receipt_id in sorted(related_receipt_ids(order)):
        _locked_receipt(receipt_id)
        verify_allocations_locked(receipt_id)
    return allocations


def checked_allocation(order, *, refund_amount, referral_amount, lock=False, exclude_refund_id=None):
    allocations = list(ReferralRefundFunding.objects.filter(order=order, refund__isnull=True,
                                                              release__isnull=True).order_by('pk'))
    if (not continuation_enabled() or len(allocations) != 1 or referral_amount <= 0):
        raise ValidationError('推荐佣金退款补资待核对')
    item = allocations[0]
    if item.refund_id or item.amount != referral_amount or item.target_refund_amount != refund_amount:
        raise ValidationError('推荐佣金退款补资与原佣金不匹配')
    action = ReferralRefundFundingAction.objects.filter(allocation=item).first()
    if (not action or action.request_key != item.request_key or action.actor != item.allocated_by
            or action.result != {'receipt_id': item.receipt_id, 'allocation_id': item.pk,
                                  'order_id': order.pk, 'amount': item.amount,
                                  'refund_amount': item.target_refund_amount}):
        raise ValidationError('推荐佣金补资操作凭据不完整')
    _verified_platform_completion(item, action)
    if lock:
        from users.receipt_usage_ledger import _locked_receipt
        from users.receipt_usage_sources import verify_allocations_locked
        _locked_receipt(item.receipt_id)
        verify_allocations_locked(item.receipt_id)
        item = ReferralRefundFunding.objects.select_for_update().get(pk=item.pk)
        if item.refund_id:
            raise ValidationError('推荐佣金补资已使用')
    return item


def verify_referral_history(order, credits, referral, refunds):
    """Rebuild each past store-funded portion from immutable order refunds."""
    from .refunds import proportional_targets
    weights = {row.pk: row.amount for row in credits}
    previous, used_ids = 0, set()
    for refund in refunds:
        before = proportional_targets(weights, previous)
        previous += refund.amount
        if previous > order.amount:
            raise ValidationError('历史退款超过支付金额')
        after = proportional_targets(weights, previous)
        expected = sum(after[row.pk] - before[row.pk] for row in referral)
        funding = ReferralRefundFunding.objects.filter(refund=refund, order=order).first()
        if not expected:
            if funding:
                raise ValidationError('历史推荐佣金补资金额异常')
            continue
        if (not funding or funding.amount != expected or funding.target_refund_amount != refund.amount
                or hasattr(funding, 'release')):
            raise ValidationError('历史推荐佣金补资凭据不完整')
        action = ReferralRefundFundingAction.objects.filter(allocation=funding).first()
        expected_action = {'receipt_id': funding.receipt_id, 'allocation_id': funding.pk,
                           'order_id': order.pk, 'amount': funding.amount,
                           'refund_amount': refund.amount}
        if (not action or action.actor != funding.allocated_by or action.request_key != funding.request_key
                or action.result != expected_action):
            raise ValidationError('历史推荐佣金补资操作异常')
        _verified_platform_completion(funding, action)
        used_ids.add(funding.pk)
    if ReferralRefundFunding.objects.filter(order=order, refund__isnull=False).exclude(pk__in=used_ids).exists():
        raise ValidationError('存在无法关联的推荐佣金补资')


def verify_replay(refund):
    """Recheck the exact durable funding on a successful request replay."""
    order = refund.order
    if not ReferralRefundFunding.objects.filter(refund=refund).exists():
        return
    from .financial_lock import lock_order_money
    from .referral_evidence import paid_referral_credits, ReferralEvidenceError
    lock_order_money()
    prelock(order)
    credits = list(order.transactions.filter(status='SUCCESS', reversal_of__isnull=True,
        tx_type__in=['INCOME', 'GIFT', 'SHOP_INCOME'], amount__gt=0).order_by('pk'))
    try:
        referral = paid_referral_credits(order, credits, expected_amount=order.inviter_commission)
    except ReferralEvidenceError as exc:
        raise ValidationError('推荐佣金原始凭据不一致') from exc
    verify_referral_history(order, credits, referral, list(order.refund_records.order_by('pk')))


def release_locked(*, allocation_id, request_key, actor, reason):
    if not continuation_enabled() or not transaction.get_connection().in_atomic_block:
        raise ValidationError('推荐佣金补资释放暂不可用')
    if (not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 200
            or not isinstance(actor, str) or not actor or not isinstance(request_key, str) or not request_key):
        raise ValidationError('补资释放原因或操作凭证无效')
    from .financial_lock import lock_order_money
    from users.receipt_usage_ledger import _locked_receipt
    from users.receipt_usage_sources import record_referral_release_locked, verify_allocations_locked
    order_id = ReferralRefundFunding.objects.filter(pk=allocation_id).values_list('order_id', flat=True).first()
    order = Order.objects.select_for_update().filter(pk=order_id).first()
    if not order:
        raise ValidationError('原补资订单不存在')
    lock_order_money()
    for receipt_id in sorted(related_receipt_ids(order)):
        _locked_receipt(receipt_id)
        verify_allocations_locked(receipt_id)
    allocation = ReferralRefundFunding.objects.select_for_update().get(pk=allocation_id, order=order)
    action = ReferralRefundFundingAction.objects.filter(allocation=allocation).first()
    if not action:
        raise ValidationError('原补资操作缺失')
    _verified_platform_completion(allocation, action)
    existing = ReferralRefundFundingRelease.objects.select_for_update().filter(allocation=allocation).first()
    expected = {'allocation_id': allocation.pk, 'receipt_id': allocation.receipt_id,
                'order_id': order.pk, 'amount': allocation.amount}
    if existing:
        if (existing.request_key != request_key or existing.actor != actor or existing.reason != reason.strip()
                or existing.result != expected):
            raise ValidationError('原释放请求与已有凭据不一致')
        verify_allocations_locked(allocation.receipt_id)
        return existing
    if allocation.refund_id:
        raise ValidationError('该补资已绑定实际退款，不能释放')
    release = ReferralRefundFundingRelease.objects.create(allocation=allocation, request_key=request_key,
        actor=actor, reason=reason.strip(), result=expected)
    record_referral_release_locked(release)
    verify_allocations_locked(allocation.receipt_id)
    return release
