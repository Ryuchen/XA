"""Serialize cross-wallet order money operations before any wallet/profile lock.

An order's customer may be another order's inviter, so a customer-first lock
cannot be globally sorted. This short transaction mutex removes that cycle.
Single-wallet withdrawal/release operations never acquire or wait on this row.
"""
from django.db import transaction
from .models import OrderFinancialLock


def lock_order_money():
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('订单资金互斥必须在业务事务内')
    OrderFinancialLock.objects.get_or_create(pk=1)
    OrderFinancialLock.objects.select_for_update().get(pk=1)
