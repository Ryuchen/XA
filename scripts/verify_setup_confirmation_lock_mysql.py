"""Exercise setup confirmation's row lock against a concurrent config writer."""
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
from console.config_readiness import _snapshot
from stores.context import resolve_store, store_context
from stores.db import transaction
from wallet.club_rules import DEPOSIT_PERCENT_KEY
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
    marker = 'setup-confirmation-lock-' + uuid.uuid4().hex
    created = None
    with store_context(store):
        row = SystemConfig.objects.filter(key=DEPOSIT_PERCENT_KEY).first()
        if row is None:
            created = SystemConfig.objects.create(key=DEPOSIT_PERCENT_KEY, value='0', remark=marker)
        original = _snapshot('deposit_percent')

    def exercise(first_confirms):
        held, attempting, finished, release = (threading.Event() for _ in range(4))
        def worker(first):
            close_old_connections()
            try:
                with store_context(store), transaction.atomic():
                    if not first:
                        assert held.wait(8)
                        attempting.set()
                    confirm = first_confirms if first else not first_confirms
                    if confirm:
                        assert _snapshot('deposit_percent', lock=True)['fingerprint'] == original['fingerprint']
                    else:
                        row = SystemConfig.objects.select_for_update().get(key=DEPOSIT_PERCENT_KEY)
                        row.value = '1' if row.value != '1' else '2'
                        row.save(update_fields=['value', 'updated_at'])
                    if first:
                        held.set()
                        assert release.wait(8)
                    transaction.set_rollback(True)
            finally:
                connections.close_all()
                if not first:
                    finished.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(worker, True), pool.submit(worker, False)
            try:
                assert attempting.wait(8), 'second transaction never attempted the lock'
                if finished.wait(1):
                    b.result(timeout=1)
                    raise AssertionError('conflicting transaction did not wait')
            finally:
                release.set()
            a.result(timeout=10)
            b.result(timeout=10)

    try:
        exercise(True)
        exercise(False)
        with store_context(store):
            assert _snapshot('deposit_percent')['fingerprint'] == original['fingerprint']
    finally:
        if created:
            with store_context(store), transaction.atomic():
                row = SystemConfig.objects.select_for_update().get(pk=created.pk, key=DEPOSIT_PERCENT_KEY)
                if row.value != '0' or row.remark != marker:
                    raise RuntimeError('测试配置已被外部修改，保留记录供核对')
                row.delete()
    print(f'{slug}: concurrent confirmation/config write serialized; writes rolled back')


verify('shenglang', 'default')
verify('xinan', 'store_b')
