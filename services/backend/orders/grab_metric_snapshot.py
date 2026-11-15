"""Read all ranking metrics in one independent store-local MySQL snapshot."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timezone

from django.db import DatabaseError
from stores.context import current_store, store_context
from stores.db import connection, transaction


class MetricSnapshotUnavailable(Exception):
    pass


def read_metrics(account_ids, order_id):
    from .grab_clock_contract import database_id
    from .models import Order
    store = current_store()
    if not store or connection.vendor != 'mysql':
        raise MetricSnapshotUnavailable('本店排序快照不可用')
    alias = store.database_alias
    try:
        identity = database_id()
        with connection.cursor() as cursor:
            cursor.execute('SELECT DATABASE()')
            database_name = cursor.fetchone()[0]
        if not database_name:
            raise MetricSnapshotUnavailable('本店数据库名称不可用')
    except (DatabaseError, ValueError) as exc:
        raise MetricSnapshotUnavailable('本店排序快照暂不可用') from exc

    def read():
        from .grab_original_facts import original_amount_facts
        from .grab_ranking import evaluation_facts
        with store_context(store):
            try:
                if connection.alias != alias or database_id() != identity:
                    raise MetricSnapshotUnavailable('排序快照数据库身份不一致')
                if connection.in_atomic_block or not connection.get_autocommit():
                    raise MetricSnapshotUnavailable('排序快照需要独立连接')
                with connection.cursor() as cursor:
                    cursor.execute('SELECT DATABASE()')
                    if cursor.fetchone()[0] != database_name:
                        raise MetricSnapshotUnavailable('排序快照门店数据库不一致')
                    cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        table = connection.ops.quote_name(Order._meta.db_table)
                        cursor.execute(f'SELECT UTC_TIMESTAMP(6), id FROM {table} WHERE id = %s', [order_id])
                        row = cursor.fetchone()
                    if not row:
                        raise MetricSnapshotUnavailable('排序订单不在本店快照中')
                    as_of = row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None else row[0]
                    original = original_amount_facts(account_ids, as_of=as_of)
                    ratings = evaluation_facts(account_ids, as_of=as_of)
                    return as_of, identity, original, ratings
            finally:
                connection.close()

    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='grab-snapshot') as executor:
            return executor.submit(read).result()
    except (DatabaseError, ValueError) as exc:
        raise MetricSnapshotUnavailable('本店排序快照暂不可用') from exc
