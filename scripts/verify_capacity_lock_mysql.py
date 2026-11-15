"""Verify store capacity shared/exclusive row locks in registered local MySQL only.

Creates one uniquely marked, unconfigured SystemConfig row only when no such
row exists. Both test writes roll back; cleanup removes only that exact row
after verifying its id, value and marker. It never modifies a configured rule.
"""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.fronted_acceptance_settings')
import django
django.setup()
from django.conf import settings
from django.db import close_old_connections, connections
from orders.capacity import CONFIG_KEY, UNCONFIGURED, current_capacity_rules, lock_capacity_rule_for_admission
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import SystemConfig

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
if settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings':
    raise RuntimeError('仅允许已登记的隔离开发配置')
for alias, expected in EXPECTED.items():
    connection = connections[alias]
    if (connection.vendor != 'mysql' or connection.settings_dict['HOST'] != '127.0.0.1'
            or str(connection.settings_dict['PORT']) != '13306'):
        raise RuntimeError('数据库连接不在已登记的本机隔离范围')
    with connection.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('数据库名称与已登记测试库不一致')


def run_store(slug):
    store = resolve_store(slug)
    if store.database_alias != {'shenglang': 'default', 'xinan': 'store_b'}[slug]:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'capacity-lock-test-' + uuid.uuid4().hex
    with store_context(store):
        if SystemConfig.objects.filter(key=CONFIG_KEY).exists():
            raise RuntimeError(f'{slug} 已有配置，拒绝改写；请在无配置的隔离库验证')
        created = SystemConfig.objects.create(key=CONFIG_KEY, value=UNCONFIGURED, remark=marker)

    def exercise(first_is_reader):
        held, attempting, finished, release = (threading.Event() for _ in range(4))
        def worker(first):
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    if not first:
                        if not held.wait(8):
                            raise AssertionError('first lock was not acquired')
                        attempting.set()
                    reader = first_is_reader if first else not first_is_reader
                    if reader:
                        lock_capacity_rule_for_admission()
                        assert current_capacity_rules()['version'] == 0
                    else:
                        row = SystemConfig.objects.select_for_update().get(pk=created.pk, key=CONFIG_KEY)
                        row.value = '{"max_concurrent_orders":2,"version":1}'
                        row.save(update_fields=['value'])
                    if first:
                        held.set()
                        if not release.wait(8):
                            raise AssertionError('first lock was not released')
                    transaction.set_rollback(True)
            finally:
                connections.close_all()
                if not first:
                    finished.set()
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(worker, True)
            second = workers.submit(worker, False)
            try:
                if not attempting.wait(8):
                    raise AssertionError('second transaction did not attempt its lock')
                if finished.wait(1):
                    second.result(timeout=1)
                    raise AssertionError('conflicting transaction did not wait')
            finally:
                release.set()
            first.result(timeout=10)
            second.result(timeout=10)

    try:
        exercise(True)   # An admission blocks a rule update.
        exercise(False)  # A rule update blocks a new admission.
        first_held, second_held, release = (threading.Event() for _ in range(3))
        def shared_reader(signal):
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    lock_capacity_rule_for_admission()
                    signal.set()
                    if not release.wait(8):
                        raise AssertionError('shared locks did not resume')
                    transaction.set_rollback(True)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(shared_reader, first_held)
            second = workers.submit(shared_reader, second_held)
            try:
                if not first_held.wait(8) or not second_held.wait(8):
                    raise AssertionError('independent shared rule locks blocked each other')
            finally:
                release.set()
            first.result(timeout=10)
            second.result(timeout=10)
    finally:
        with store_context(store), transaction.atomic():
            row = SystemConfig.objects.select_for_update().get(pk=created.pk, key=CONFIG_KEY)
            if row.value != UNCONFIGURED or row.remark != marker:
                raise RuntimeError('测试配置已被外部修改，保留记录供核对')
            row.delete()
    with store_context(store):
        assert not SystemConfig.objects.filter(pk=created.pk).exists()
    print(f'{slug}: shared/exclusive capacity locks verified; isolated row removed')


run_store('shenglang')
run_store('xinan')
