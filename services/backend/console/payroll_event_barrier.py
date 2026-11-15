"""Per-store payroll event barrier; deliberately not wired to business writers.

A writer takes the shared lock first, before *any* business row lock, and
holds it through its outer transaction. Payroll confirmation/closeout takes
exclusive first, before PayrollLock or any business row. Do not upgrade a
shared lock in the same transaction. Both sides must be fully integrated
before this barrier can provide consistency.
"""
from django.conf import settings

from stores.context import current_database, current_store
from stores.db import transaction
from .payroll_models import PayrollEventBarrier


class PayrollEventBarrierError(RuntimeError):
    pass


def _acquire(*, exclusive):
    store = current_store()
    alias = current_database()
    if (store is None or store.database_alias != alias
            or (getattr(settings, 'ISOLATED_CONTROL_PLANE', False) and alias == 'default')):
        raise PayrollEventBarrierError('工资事件屏障必须绑定当前门店')
    connection = transaction.get_connection()
    if connection.alias != alias or not connection.in_atomic_block:
        raise PayrollEventBarrierError('工资事件屏障必须在当前门店事务内获取')
    if connection.vendor != 'mysql':
        raise PayrollEventBarrierError('工资事件屏障需要 MySQL 行锁')
    table = connection.ops.quote_name(PayrollEventBarrier._meta.db_table)
    mode = 'FOR UPDATE' if exclusive else 'LOCK IN SHARE MODE'
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT id FROM {table} WHERE id = %s {mode}', [1])
        if cursor.fetchone() is None:
            raise PayrollEventBarrierError('工资事件屏障未初始化，请先初始化门店业务库')


def lock_payment_refund_events():
    _acquire(exclusive=False)


def lock_payroll_events():
    _acquire(exclusive=True)
