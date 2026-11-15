"""Receipt prelocking for one isolated first points-negotiation release."""
from django.conf import settings
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from stores.db import transaction
from users.models import PassRefundRepaymentReceipt
from .models import Order, OrderOriginalShareTransfer, OrderPointsFundingAllocation
from .points_checkout_test import require_isolated_checkout


def eligible(order):
    from .points_business_access import permits
    formal = any(permits(order, phase=phase, kind=kind)
                 for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement'))
    if not formal and not getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False):
        return False
    if not formal:
        require_isolated_checkout()
    return bool(order and not order.is_gift_snapshot and not order.source_order_id
        and (not order.addon_intents.filter(payment__isnull=False).exists() or any(
            permits(order, phase=phase, kind='addon') for phase in ('funding', 'refund', 'report', 'settlement')))
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        and order.refund_records.count() <= 1)


def followup_eligible(order):
    """A bounded, untransferred dual-provider follow-up."""
    from .points_business_access import permits
    formal = any(permits(order, phase=phase, kind=kind)
                 for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement'))
    if not formal and (not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False)
            or not getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False)):
        return False
    if not formal:
        require_isolated_checkout()
    from .models import OrderPrestartHandoff
    return bool(order and not order.is_gift_snapshot and not order.source_order_id
        and order.providers.count() == 2 and (formal or not order.addon_intents.exists())
        and not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists()
        and not OrderPrestartHandoff.objects.filter(order=order).exists()
        and order.refund_records.count() <= 2)


def transfer_refund_eligible(order):
    """One transfer completed before every allocation; no transfer releases."""
    from .points_transfer_refund_access import permits
    formal = permits(order, phase='refund')
    if not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        return False
    if not formal:
        require_isolated_checkout()
    from .models import (OrderPointsTransferFundingRelease, OrderPointsTransferFundingRevision,
                         OrderPrestartHandoff)
    transfers = list(OrderOriginalShareTransfer.objects.filter(
        previous_segment__attribution__order=order).select_related('previous_segment').order_by('pk')) if order else []
    counts = {}
    for item in transfers:
        slot = item.previous_segment.slot
        counts[slot] = counts.get(slot, 0) + 1
    from .points_transfer_refund_access import _supported_addon
    if (not order or order.is_gift_snapshot or order.source_order_id or not _supported_addon(order, transfers)
            or order.refund_records.count() > (2 if formal and not order.addon_intents.exists() else 1)
            or not transfers or any(count > 2 for count in counts.values())
            or OrderPrestartHandoff.objects.filter(order=order).exists()):
        return False
    allocations = OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk))
    return True


def prelock_transfer_refund_receipts(order):
    """The order and money mutex must already be held."""
    from .points_transfer_refund_access import permits
    formal = permits(order, phase='refund')
    if not formal and not getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False):
        return
    if not OrderOriginalShareTransfer.objects.filter(previous_segment__attribution__order=order).exists():
        return
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('转单退款到账预锁须在门店事务中执行')
    ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('转单退款到账凭证缺失')
    if any(row.usage_version is not None for row in receipts) and not transfer_refund_eligible(order):
        raise ValidationError('该转单退款到账用途尚未接入')


def prelock_followup_receipts(order):
    """Caller already holds order and money locks, before any funding facts."""
    from .points_business_access import permits
    formal = any(permits(order, phase=phase, kind=kind)
                 for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement'))
    if not formal and not getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False):
        return
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('后续退款到账预锁须在门店事务中执行')
    ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('后续退款到账凭证缺失')
    if any(row.usage_version is not None for row in receipts) and not followup_eligible(order):
        raise ValidationError('该后续退款到账用途尚未接入')


def prelock_posting_receipts(posting):
    """Call before locking any posting/party facts; order precedes receipt."""
    from .points_transfer_refund_access import permits
    from .points_business_access import permits as service_permits
    formal_transfer = permits(phase='refund')
    formal_service = any(service_permits(phase=phase, kind=kind)
                         for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement'))
    formal = formal_transfer or formal_service
    if not (formal or getattr(settings, 'SERVICE_POINTS_FIRST_REFUND_USAGE_TESTING', False)
            or getattr(settings, 'SERVICE_POINTS_FOLLOWUP_RECEIPT_USAGE_TESTING', False)
            or getattr(settings, 'SERVICE_POINTS_TRANSFER_REFUND_RECEIPT_USAGE_TESTING', False)):
        return
    if not formal:
        require_isolated_checkout()
    if not transaction.get_connection().in_atomic_block:
        raise ValidationError('到账预锁必须在订单事务中执行')
    order_id = type(posting).objects.filter(pk=posting.pk).values_list('proposal__case__order_id', flat=True).first()
    if not order_id:
        raise ValidationError('积分协商订单不存在')
    order = Order.objects.select_for_update().get(pk=order_id)
    if formal_transfer and not permits(order, phase='refund'):
        raise ValidationError('转单退款凭证不属于当前订单')
    if formal_service and not any(service_permits(order, phase=phase, kind=kind)
                                  for kind in ('service', 'addon') for phase in ('funding', 'refund', 'report', 'settlement')):
        raise ValidationError('积分退款凭证不属于当前订单')
    from .financial_lock import lock_order_money
    lock_order_money()
    ids = set(OrderPointsFundingAllocation.objects.filter(
        Q(redemption__order_id=order.pk) | Q(service_order_id=order.pk)).values_list('receipt_id', flat=True))
    receipts = list(PassRefundRepaymentReceipt.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
    if len(receipts) != len(ids):
        raise ValidationError('订单到账凭证缺失')
    if any(row.usage_version is not None for row in receipts) and not (
            eligible(order) or followup_eligible(order) or transfer_refund_eligible(order)):
        raise ValidationError('该积分退款到账用途尚未接入')
