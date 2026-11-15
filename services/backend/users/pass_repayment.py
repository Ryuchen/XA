"""Internal ledger primitives for offline pass-refund repayment.

This module is not an HTTP interface. A future platform entry point must check
the finance grant and store scope, and coordinate platform-wide receipt
deduplication before using it. All amounts are integer internal wallet units.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Sum
from django.utils import timezone

from stores.context import current_store
from stores.db import connection, transaction
from .models import PassRefundRepaymentAllocation, PassRefundRepaymentReceipt, ProviderPassRefund


def _context(actor, request_key=None):
    if current_store() is None:
        raise ValidationError('必须指定门店上下文')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 64:
        raise ValidationError('缺少操作人')
    if request_key is not None and (not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 64):
        raise ValidationError('缺少有效请求编号')


def _amount(value):
    if type(value) is not int or not 0 < value <= 2147483647:
        raise ValidationError('金额必须为正整数记账单位')


def points_funding_table_exists():
    # Older installations lack this table; once present, errors reading it
    # must propagate rather than silently making its allocations disappear.
    from orders.models import OrderPointsFundingAllocation
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
    if OrderPointsFundingAllocation._meta.db_table in tables:
        return True
    # A missing table is only compatible with databases that have not applied
    # this migration. Otherwise treating missing records as zero could spend
    # funds already allocated to points orders.
    from stores.context import current_database
    recorder = MigrationRecorder.Migration.objects.using(current_database())
    if 'django_migrations' not in tables or recorder.filter(app='orders', name='0124_orderpointsfundingallocation').exists():
        raise DatabaseError('积分补资分配表缺失，须先核对数据库')
    return False


def register_receipt(*, actor, request_key, external_reference, amount, received_at, evidence):
    _context(actor, request_key)
    _amount(amount)
    if (not isinstance(external_reference, str) or not external_reference.strip() or len(external_reference) > 128
            or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4000
            or not isinstance(received_at, datetime) or timezone.is_naive(received_at)):
        raise ValidationError('请填写到账时间、凭证和外部流水号')
    external_reference, evidence = external_reference.strip(), evidence.strip()
    with transaction.atomic():
        existing = PassRefundRepaymentReceipt.objects.filter(request_key=request_key).first()
        if existing:
            if (existing.external_reference, existing.amount, existing.received_at, existing.evidence, existing.registered_by) != (
                    external_reference, amount, received_at, evidence, actor):
                raise ValidationError('请求编号已用于其他到账记录')
            return existing
        if PassRefundRepaymentReceipt.objects.filter(external_reference=external_reference).exists():
            raise ValidationError('该门店已登记此外部流水号')
        return PassRefundRepaymentReceipt.objects.create(request_key=request_key, external_reference=external_reference,
            amount=amount, received_at=received_at, evidence=evidence, registered_by=actor)


def verify_receipt(*, actor, receipt_id):
    _context(actor)
    with transaction.atomic():
        receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt_id)
        if not receipt.verified_at:
            receipt.verified_by, receipt.verified_at = actor, timezone.now()
            receipt.save(update_fields=['verified_by', 'verified_at'])
        from django.conf import settings
        if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True:
            from .receipt_usage_ledger import initialize_empty_locked
            initialize_empty_locked(receipt.pk)
        return receipt


def allocate(*, actor, request_key, receipt_id, refund_id, amount):
    _context(actor, request_key)
    _amount(amount)
    with transaction.atomic():
        # Lock order is receipt then refund, consistently for every allocation.
        receipt = PassRefundRepaymentReceipt.objects.select_for_update().get(pk=receipt_id)
        from django.conf import settings
        if getattr(settings, 'RECEIPT_USAGE_LEDGER_ENABLED', False) is True and receipt.usage_version is None:
            raise ValidationError('到账用途账本尚未建立，请先核对到账')
        from .receipt_usage_guard import isolated_allocation_state_locked
        ledger = isolated_allocation_state_locked(receipt)
        refund = ProviderPassRefund.objects.select_for_update().get(pk=refund_id)
        existing_query = PassRefundRepaymentAllocation.objects.filter(request_key=request_key)
        existing = (existing_query.select_for_update() if ledger else existing_query).first()
        if existing:
            if (existing.receipt_id, existing.refund_id, existing.amount, existing.allocated_by) != (
                    receipt_id, refund_id, amount, actor):
                raise ValidationError('请求编号已用于其他核销')
            return existing
        if not receipt.verified_at:
            raise ValidationError('到账尚未核实')
        used = (ledger.pass_units + ledger.order_units + ledger.referral_units
                - ledger.referral_released_units - ledger.released_units) if ledger else (
            receipt.allocations.aggregate(value=Sum('amount'))['value'] or 0)
        if ledger is None and points_funding_table_exists():
            from orders.points_funding_release import receipt_points_used_locked
            from rest_framework.exceptions import ValidationError as RestValidationError
            try:
                used += receipt_points_used_locked(receipt)
            except RestValidationError as exc:
                raise ValidationError('订单补资释放凭证待核对') from exc
        repaid = (sum(refund.repayment_allocations.select_for_update().values_list('amount', flat=True)) if ledger
                  else refund.repayment_allocations.aggregate(value=Sum('amount'))['value'] or 0)
        if amount > receipt.amount - used:
            raise ValidationError('核销金额超过到账未分配金额')
        if amount > refund.shortfall_amount - repaid:
            raise ValidationError('核销金额超过退款剩余缺口')
        allocation = PassRefundRepaymentAllocation.objects.create(request_key=request_key, receipt=receipt,
            refund=refund, amount=amount, allocated_by=actor)
        if ledger:
            from .receipt_usage_sources import record_pass_locked, verify_allocations_locked
            record_pass_locked(allocation)
            verify_allocations_locked(receipt.pk)
        return allocation
