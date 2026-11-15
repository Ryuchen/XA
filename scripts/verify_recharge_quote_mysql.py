"""Verify real store MySQL tier pricing and lock serialization, with rollback."""
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
from engagement.models import RechargePackage
from engagement.recharge_pricing import LOCK_KEY, lock_recharge_packages, recharge_quote
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.models import SystemConfig

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
if settings.SETTINGS_MODULE != 'config.fronted_acceptance_settings':
    raise RuntimeError('仅允许已登记的隔离开发配置')
for alias, expected in EXPECTED.items():
    db = connections[alias]
    if db.vendor != 'mysql' or db.settings_dict['HOST'] != '127.0.0.1' or str(db.settings_dict['PORT']) != '13306':
        raise RuntimeError('数据库连接不在已登记的本机隔离范围')
    with db.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('数据库名称与已登记测试库不一致')


def verify(slug, alias):
    store = resolve_store(slug)
    if store.database_alias != alias:
        raise RuntimeError('门店与测试库映射不一致')
    marker = 'recharge-pricing-test-' + uuid.uuid4().hex
    with store_context(store):
        before = list(RechargePackage.objects.order_by('id').values_list('id', 'amount', 'gift_amount', 'active', 'points_rate'))
        sentinel, created = SystemConfig.objects.get_or_create(key=LOCK_KEY, defaults={'value': '', 'remark': marker})
    try:
        with store_context(store), transaction.atomic():
            lock_recharge_packages()
            RechargePackage.objects.all().update(active=False)
            for amount, gift in ((300000, 10000), (600000, 25000)):
                RechargePackage.objects.update_or_create(amount=amount, defaults={'gift_amount': gift, 'active': True})
            assert [recharge_quote(n)['gift_amount'] for n in (700000, 900000, 1200000)] == [25000, 35000, 50000]
            transaction.set_rollback(True)
        held, waiting, finished, release = (threading.Event() for _ in range(4))
        def worker(first):
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    if not first:
                        assert held.wait(8)
                        waiting.set()
                    lock_recharge_packages()
                    if first:
                        held.set()
                        assert release.wait(8)
                    else:
                        recharge_quote(1000)
                    transaction.set_rollback(True)
            finally:
                connections.close_all()
                if not first:
                    finished.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            one, two = pool.submit(worker, True), pool.submit(worker, False)
            try:
                assert waiting.wait(8)
                if finished.wait(1):
                    two.result(timeout=1)
                    raise AssertionError('充值档位锁未阻塞并发事务')
            finally:
                release.set()
            one.result(timeout=10); two.result(timeout=10)
        with store_context(store):
            assert list(RechargePackage.objects.order_by('id').values_list('id', 'amount', 'gift_amount', 'active', 'points_rate')) == before
    finally:
        if created:
            with store_context(store), transaction.atomic():
                row = SystemConfig.objects.select_for_update().get(pk=sentinel.pk, key=LOCK_KEY)
                if row.value != '' or row.remark != marker:
                    raise RuntimeError('测试锁记录已变化，保留供核对')
                row.delete()
    print(f'{slug}: greedy quote and MySQL lock verified; test writes rolled back')


verify('shenglang', 'default')
verify('xinan', 'store_b')
