"""Recover post-closeout refund facts from confirmed versioned payroll sources."""
import logging
from django.db import DatabaseError
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from orders.models import OrderAddonPayment, OrderAddonRefund, OrderRefund
from wallet.models import Transaction
from .payroll_calculation import raw_to_fen
from .payroll_models import (PayrollReemploymentPlan, PayrollReemploymentRefundWaiver, PayrollRun,
                             PayrollSeparationSettlement)
from .payroll_refund_performance import addon_payment_time, refund_event_time
from .payroll_refund_ledger import _lock
from stores.db import transaction

logger = logging.getLogger(__name__)


def _payment(source):
    origin, source_id = source.get('origin'), source.get('id')
    if type(source_id) is not int or source_id <= 0:
        raise ValidationError('已确认工资的付款来源待核对')
    if origin == 'ORDER':
        tx = Transaction.objects.select_related('order', 'wallet').filter(pk=source_id).first()
        if (not tx or not tx.order_id or tx.order_id != source.get('order_id')
                or tx.tx_type != 'PAY' or tx.status != 'SUCCESS' or tx.amount >= 0
                or tx.reversal_of_id or tx.reversals.exists()
                or tx.wallet.account_id != tx.order.customer_account_id
                or tx.amount != -tx.order.amount or tx.balance_after != tx.balance_before + tx.amount):
            raise ValidationError('已确认工资主单付款凭证待核对')
        moment, amount = tx.created_at, raw_to_fen(-tx.amount)
        refunds = OrderRefund.objects.filter(order_id=tx.order_id, amount__gt=0)
    elif origin == 'ADDON':
        payment = OrderAddonPayment.objects.select_related('intent__order', 'buyer_transaction__wallet').filter(pk=source_id).first()
        if not payment or payment.intent.order_id != source.get('order_id'):
            raise ValidationError('已确认工资加购付款凭证待核对')
        moment, amount = addon_payment_time(payment), raw_to_fen(payment.intent.amount)
        refunds = OrderAddonRefund.objects.filter(payment=payment, amount__gt=0)
    else:
        raise ValidationError('已确认工资付款类型待核对')
    if source.get('occurred_at') != moment.isoformat() or source.get('amount_fen') != amount:
        raise ValidationError('已确认工资付款快照与流水不一致')
    return refunds


def capture_reemployment_refund_waivers(separation):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('重入绩效免追须在工资事务内记录')
    _lock()
    count = 0
    runs = PayrollRun.objects.filter(month__lte=separation.final_run.month,
        confirmed_at__lte=separation.confirmed_at).order_by('month', 'pk')
    for run in runs:
        line = next((item for item in run.snapshot.get('lines', []) if item.get('account') == separation.account_id), None)
        if not line:
            continue
        for source in line.get('reemployment_performance_sources', []):
            plan = PayrollReemploymentPlan.objects.select_related('reemployment__separation__final_run').filter(pk=source.get('plan_id'),
                reemployment_id=source.get('reemployment_id'), reemployment__account_id=separation.account_id).first()
            if not plan or source.get('plan_snapshot') != plan.plan_snapshot:
                logger.warning('重入免追方案来源待核对 settlement_id=%s run_id=%s', separation.pk, run.pk)
                continue
            for payment in source.get('sources', []):
                if payment.get('kind') != 'PAYMENT':
                    continue
                try:
                    with transaction.atomic():
                        moment = parse_datetime(payment.get('occurred_at', ''))
                        start = parse_datetime(source.get('period_start', ''))
                        end = parse_datetime(source.get('period_end', ''))
                        if not moment or not start or not end or not start <= moment < end or start != plan.effective_at:
                            raise ValidationError('已确认工资的付款区间待核对')
                        refunds = list(_payment(payment).order_by('pk'))
                except (ValidationError, DatabaseError, TypeError, ValueError):
                    logger.exception('重入免追付款来源待重试 settlement_id=%s run_id=%s payment_id=%s',
                                     separation.pk, run.pk, payment.get('id'))
                    continue
                for refund in refunds:
                    try:
                        with transaction.atomic():
                            refund_at = refund_event_time(refund)
                            if refund_at <= separation.confirmed_at:
                                continue
                            # A later reentry and closeout must not take ownership
                            # of an earlier employment period's confirmed sale.
                            # Use the first closeout after this source and its
                            # confirmed salary, before the actual refund event.
                            chosen = PayrollSeparationSettlement.objects.filter(
                                account_id=separation.account_id,
                                final_run__month__gte=plan.reemployment.separation.final_run.month,
                                confirmed_at__gt=moment,
                                confirmed_at__lt=refund_at).order_by('confirmed_at', 'pk').first()
                            if not chosen or chosen.pk != separation.pk or chosen.confirmed_at < run.confirmed_at:
                                continue
                            field = 'addon_refund' if isinstance(refund, OrderAddonRefund) else 'refund'
                            row, created = PayrollReemploymentRefundWaiver.objects.get_or_create(plan=plan, **{field: refund},
                                defaults={'settlement': separation, 'source_run': run, 'source_payment_id': payment['id']})
                            if row.settlement_id != separation.pk or row.source_payment_id != payment['id']:
                                raise ValidationError('重入退款免追与原付款或结清不一致')
                        count += int(created)
                    except (ValidationError, DatabaseError, TypeError, ValueError):
                        logger.exception('重入免追退款来源待重试 settlement_id=%s run_id=%s refund_id=%s',
                                         separation.pk, run.pk, refund.pk)
    return count
