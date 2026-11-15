"""Opt-in real MySQL races in uniquely named shadow tables; no business rows changed.

Runs the actual platform registration/void code against the registered local
acceptance databases. The only existing row accessed is the store directory
lock anchor. Synthetic identities and repayment rows live in owned tables.
"""
import json
import os
import sys
import threading
import unicodedata
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services' / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.fronted_acceptance_settings'
import django  # noqa: E402
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from django.utils import timezone  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from club_accounts.models import ClubAccount  # noqa: E402
from stores.context import store_context  # noqa: E402
from stores.models import (PlatformAccount, PlatformAuditEvent, PlatformPassRepaymentClaim,
                           PlatformPassRepaymentOperation, PlatformRoleGrant, Store)  # noqa: E402
from stores.platform_auth import issue_tokens  # noqa: E402
from stores.platform_pass_repayment import RepaymentError, _parse, _sha, act, register  # noqa: E402
from stores.platform_pass_repayment_recovery import recover, void_pending  # noqa: E402
from users.models import (PassRefundRepaymentAction, PassRefundRepaymentAllocation, PassRefundRepaymentReceipt,
                          ProviderPassPurchase, ProviderPassRefund)  # noqa: E402
from wallet.models import Transaction  # noqa: E402

EXPECTED = {'default': 'xa_test_fronted_acceptance_20261104', 'store_b': 'xa_test_store_preview_b_20261104'}
for alias, expected in EXPECTED.items():
    db = connections[alias]
    if db.vendor != 'mysql' or db.settings_dict['HOST'] != '127.0.0.1' or str(db.settings_dict['PORT']) != '13306':
        raise RuntimeError('隔离测试数据库连接不匹配')
    with db.cursor() as cursor:
        cursor.execute('SELECT DATABASE()')
        if cursor.fetchone()[0] != expected:
            raise RuntimeError('隔离测试数据库名称不匹配')
store = Store.objects.only('id', 'slug', 'database_alias', 'initialized_at').get(slug='xinan')
if store.database_alias != 'store_b' or not store.initialized_at:
    raise RuntimeError('兴安隔离测试门店配置不匹配')

nonce = uuid.uuid4().hex[:12]
directory_models = [PlatformAccount, PlatformRoleGrant, PlatformAuditEvent,
                    PlatformPassRepaymentClaim, PlatformPassRepaymentOperation]
tenant_stubs = [ClubAccount, ProviderPassPurchase, Transaction]
tenant_models = [ProviderPassRefund, PassRefundRepaymentReceipt, PassRefundRepaymentAllocation, PassRefundRepaymentAction]
all_models = directory_models + tenant_stubs + tenant_models
original = {model: (model._meta.db_table, model._meta.constraints) for model in all_models}
created = []
workers = []
for index, model in enumerate(all_models):
    model._meta.db_table = f'xa_tmp_pr_{nonce}_{index}'
    constraints = [constraint.clone() for constraint in model._meta.constraints]
    for number, constraint in enumerate(constraints):
        constraint.name = f'pr_{nonce}_{index}_{number}'
    model._meta.constraints = constraints


def table_exists(alias, model):
    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s',
                       [model._meta.db_table])
        return bool(cursor.fetchone()[0])


def make_claim(actor, reference):
    payload = {'request_id': str(uuid.uuid4()), 'external_reference': reference, 'amount': 500,
               'received_at': timezone.now().isoformat(), 'evidence': '隔离并发测试'}
    request_id, ref, amount, received_at, evidence = _parse(payload)
    content = json.dumps([store.slug, ref, amount, received_at.isoformat(), evidence],
                         ensure_ascii=False, separators=(',', ':'))
    claim = PlatformPassRepaymentClaim.objects.create(request_id=request_id,
        external_reference_digest=_sha(unicodedata.normalize('NFKC', ref).casefold()),
        payload_digest=_sha(content), store=store, actor=actor)
    return claim, payload


def run_race(claim, payload, token, other_token, winner):
    import stores.platform_pass_repayment as module
    import stores.platform_pass_repayment_recovery as recovery
    entered, release = threading.Event(), threading.Event()
    results = {}
    attempted = {'registration': threading.Event(), 'void': threading.Event()}

    def worker(name, fn):
        try:
            for alias in EXPECTED:
                with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 8')
            attempted[name].set()
            results[name] = fn()['status']
        except RepaymentError as error:
            results[name] = error.code
        except Exception as error:
            results[name] = 'ERROR:' + type(error).__name__
        finally:
            for alias in EXPECTED:
                connections[alias].close()

    original_register = module.register_receipt
    original_select = PassRefundRepaymentReceipt.objects.select_for_update

    def hold_register(*args, **kwargs):
        entered.set()
        if not release.wait(8): raise RuntimeError('登记线程等待超时')
        return original_register(*args, **kwargs)

    def hold_select(*args, **kwargs):
        if threading.current_thread().name == 'void-winner':
            entered.set()
            if not release.wait(8): raise RuntimeError('作废线程等待超时')
        return original_select(*args, **kwargs)

    registration = threading.Thread(name='registration', target=worker,
        args=('registration', lambda: register(token, store.slug, payload)))
    void = threading.Thread(name='void-winner', target=worker,
        args=('void', lambda: void_pending(other_token, store.slug, 'claim', claim.pk, '隔离测试核实无记录')))
    workers.extend((registration, void))
    hook = patch.object(module, 'register_receipt', side_effect=hold_register) if winner == 'registration' else patch.object(
        recovery.PassRefundRepaymentReceipt.objects, 'select_for_update', side_effect=hold_select)
    try:
        with hook:
            first, second = (registration, void) if winner == 'registration' else (void, registration)
            first.start()
            if not entered.wait(8): raise RuntimeError('未进入预定事务边界')
            second.start()
            if not attempted['void' if winner == 'registration' else 'registration'].wait(8):
                raise RuntimeError('第二个事务未开始')
            second.join(timeout=0.5)
            if not second.is_alive(): raise RuntimeError('第二个事务未等待目录行锁')
            release.set()
            first.join(timeout=12); second.join(timeout=12)
            if first.is_alive() or second.is_alive(): raise RuntimeError('并发事务未结束')
    finally:
        release.set()
        for thread in (registration, void):
            if thread.is_alive(): thread.join(timeout=12)
    expected = {'registration': 'RECORDED', 'void': 'REPAYMENT_REVIEW'} if winner == 'registration' else {
        'registration': 'REPAYMENT_VOIDED', 'void': 'VOIDED'}
    if results != expected:
        raise RuntimeError('并发结果不符合预期: ' + repr(results))
    claim.refresh_from_db()
    with store_context(store):
        count = PassRefundRepaymentReceipt.objects.filter(request_key=f'platform:{claim.pk.hex}').count()
    if count != (1 if winner == 'registration' else 0):
        raise RuntimeError('门店到账记录数量不正确')
    if winner == 'void':
        replacement = {**payload, 'request_id': str(uuid.uuid4())}
        if register(token, store.slug, replacement)['status'] != 'RECORDED':
            raise RuntimeError('作废后同流水未能重新登记')


def run_verify_race(claim, token, other_token, winner):
    import stores.platform_pass_repayment as module
    import stores.platform_pass_repayment_recovery as recovery
    request_id = uuid.uuid4()
    digest = _sha(json.dumps([str(claim.pk), 'VERIFY', None, None], separators=(',', ':')))
    operation = PlatformPassRepaymentOperation.objects.create(request_id=request_id, claim=claim,
        actor=claim.actor, kind='VERIFY', payload_digest=digest)
    entered, release = threading.Event(), threading.Event()
    results = {}
    attempted = {'verification': threading.Event(), 'void': threading.Event()}

    def worker(name, fn):
        try:
            for alias in EXPECTED:
                with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 8')
            attempted[name].set()
            results[name] = fn()['status']
        except RepaymentError as error:
            results[name] = error.code
        except Exception as error:
            results[name] = 'ERROR:' + type(error).__name__
        finally:
            for alias in EXPECTED: connections[alias].close()

    original_verify = module.verify_receipt
    original_select = PassRefundRepaymentAction.objects.select_for_update

    def hold_verify(*args, **kwargs):
        entered.set()
        if not release.wait(8): raise RuntimeError('核实线程等待超时')
        return original_verify(*args, **kwargs)

    def hold_select(*args, **kwargs):
        if threading.current_thread().name == 'void-operation-winner':
            entered.set()
            if not release.wait(8): raise RuntimeError('作废线程等待超时')
        return original_select(*args, **kwargs)

    verify = threading.Thread(name='verification', target=worker,
        args=('verification', lambda: act(token, store.slug, claim.pk, 'VERIFY', {'request_id': str(request_id)})))
    void = threading.Thread(name='void-operation-winner', target=worker,
        args=('void', lambda: void_pending(other_token, store.slug, 'operation', operation.pk, '隔离测试核实无记录')))
    workers.extend((verify, void))
    hook = patch.object(module, 'verify_receipt', side_effect=hold_verify) if winner == 'verification' else patch.object(
        recovery.PassRefundRepaymentAction.objects, 'select_for_update', side_effect=hold_select)
    try:
        with hook:
            first, second = (verify, void) if winner == 'verification' else (void, verify)
            first.start()
            if not entered.wait(8): raise RuntimeError('未进入预定事务边界')
            second.start()
            if not attempted['void' if winner == 'verification' else 'verification'].wait(8):
                raise RuntimeError('第二个事务未开始')
            second.join(timeout=0.5)
            if not second.is_alive(): raise RuntimeError('第二个事务未等待目录行锁')
            release.set()
            first.join(timeout=12); second.join(timeout=12)
            if first.is_alive() or second.is_alive(): raise RuntimeError('并发事务未结束')
    finally:
        release.set()
        for thread in (verify, void):
            if thread.is_alive(): thread.join(timeout=12)
    expected = {'verification': 'COMPLETED', 'void': 'REPAYMENT_REVIEW'} if winner == 'verification' else {
        'verification': 'REPAYMENT_VOIDED', 'void': 'VOIDED'}
    if results != expected:
        raise RuntimeError('核实与作废的并发结果不符合预期: ' + repr(results))
    operation.refresh_from_db()
    with store_context(store):
        receipt = PassRefundRepaymentReceipt.objects.get(pk=claim.tenant_receipt_id)
        action_count = PassRefundRepaymentAction.objects.filter(request_key=f'platform:{operation.pk.hex}').count()
    if (bool(receipt.verified_at), action_count) != ((True, 1) if winner == 'verification' else (False, 0)):
        raise RuntimeError('核实事实或操作记录不正确')


def run_allocate_race(claim, refund, token, other_token, winner):
    import stores.platform_pass_repayment as module
    import stores.platform_pass_repayment_recovery as recovery
    request_id, amount = uuid.uuid4(), 200
    digest = _sha(json.dumps([str(claim.pk), 'ALLOCATE', refund.pk, amount], separators=(',', ':')))
    operation = PlatformPassRepaymentOperation.objects.create(request_id=request_id, claim=claim,
        actor=claim.actor, kind='ALLOCATE', payload_digest=digest)
    entered, release = threading.Event(), threading.Event()
    results = {}
    attempted = {'allocation': threading.Event(), 'void': threading.Event()}

    def worker(name, fn):
        try:
            for alias in EXPECTED:
                with connections[alias].cursor() as cursor: cursor.execute('SET SESSION innodb_lock_wait_timeout = 8')
            attempted[name].set()
            results[name] = fn()['status']
        except RepaymentError as error:
            results[name] = error.code
        except Exception as error:
            results[name] = 'ERROR:' + type(error).__name__
        finally:
            for alias in EXPECTED: connections[alias].close()

    original_allocate = module.allocate
    original_select = PassRefundRepaymentAction.objects.select_for_update

    def hold_allocate(*args, **kwargs):
        entered.set()
        if not release.wait(8): raise RuntimeError('分配线程等待超时')
        return original_allocate(*args, **kwargs)

    def hold_select(*args, **kwargs):
        if threading.current_thread().name == 'void-allocation-winner':
            entered.set()
            if not release.wait(8): raise RuntimeError('作废线程等待超时')
        return original_select(*args, **kwargs)

    allocate_thread = threading.Thread(name='allocation', target=worker,
        args=('allocation', lambda: act(token, store.slug, claim.pk, 'ALLOCATE',
                                      {'request_id': str(request_id), 'refund_id': refund.pk, 'amount': amount})))
    void = threading.Thread(name='void-allocation-winner', target=worker,
        args=('void', lambda: void_pending(other_token, store.slug, 'operation', operation.pk, '隔离测试核实无记录')))
    workers.extend((allocate_thread, void))
    hook = patch.object(module, 'allocate', side_effect=hold_allocate) if winner == 'allocation' else patch.object(
        recovery.PassRefundRepaymentAction.objects, 'select_for_update', side_effect=hold_select)
    try:
        with hook:
            first, second = (allocate_thread, void) if winner == 'allocation' else (void, allocate_thread)
            first.start()
            if not entered.wait(8): raise RuntimeError('未进入预定事务边界')
            second.start()
            if not attempted['void' if winner == 'allocation' else 'allocation'].wait(8):
                raise RuntimeError('第二个事务未开始')
            second.join(timeout=0.5)
            if not second.is_alive(): raise RuntimeError('第二个事务未等待目录行锁')
            release.set()
            first.join(timeout=12); second.join(timeout=12)
            if first.is_alive() or second.is_alive(): raise RuntimeError('并发事务未结束')
    finally:
        release.set()
        for thread in (allocate_thread, void):
            if thread.is_alive(): thread.join(timeout=12)
    expected = {'allocation': 'COMPLETED', 'void': 'REPAYMENT_REVIEW'} if winner == 'allocation' else {
        'allocation': 'REPAYMENT_VOIDED', 'void': 'VOIDED'}
    if results != expected:
        raise RuntimeError('分配与作废的并发结果不符合预期: ' + repr(results))
    with store_context(store):
        allocated = list(PassRefundRepaymentAllocation.objects.filter(request_key=f'platform:{operation.pk.hex}')
                         .values_list('refund_id', 'amount'))
        action_count = PassRefundRepaymentAction.objects.filter(request_key=f'platform:{operation.pk.hex}').count()
    if (allocated, action_count) != (([(refund.pk, amount)], 1) if winner == 'allocation' else ([], 0)):
        raise RuntimeError('核销分配或操作记录不正确')


def disconnect_worker(alias, hook, fn):
    """Kill only the connection ID announced by this test's paused worker."""
    entered, release = threading.Event(), threading.Event()
    announced, result = {}, {}

    def pause():
        with connections[alias].cursor() as cursor:
            cursor.execute('SELECT CONNECTION_ID(), DATABASE()')
            connection_id, db_name = cursor.fetchone()
        if db_name != EXPECTED[alias]: raise RuntimeError('工作线程数据库不匹配')
        announced['id'] = int(connection_id)
        entered.set()
        if not release.wait(8): raise RuntimeError('故障注入等待超时')

    def worker():
        try:
            result['value'] = fn()['status']
        except RepaymentError as error:
            result['value'] = error.code
        except Exception as error:
            result['value'] = 'ERROR:' + type(error).__name__
        finally:
            for name in EXPECTED: connections[name].close()

    thread = threading.Thread(name='isolated-disconnect-worker', target=worker)
    workers.append(thread)
    try:
        with hook(pause):
            thread.start()
            if not entered.wait(8): raise RuntimeError('工作线程未到达指定故障位置')
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT CONNECTION_ID()')
                controller_id = int(cursor.fetchone()[0])
                target_id = announced['id']
                if target_id == controller_id or target_id <= 0:
                    raise RuntimeError('拒绝断开非工作线程连接')
                cursor.execute('SELECT USER, DB FROM information_schema.processlist WHERE ID = %s', [target_id])
                row = cursor.fetchone()
                if not row or row[0] != connections[alias].settings_dict['USER'] or row[1] != EXPECTED[alias]:
                    raise RuntimeError('工作连接身份或数据库不匹配，拒绝断开')
                # target_id comes only from the paused worker's own connection.
                cursor.execute('KILL CONNECTION %s', [target_id])
            release.set()
            thread.join(timeout=12)
            if thread.is_alive(): raise RuntimeError('故障工作线程未结束')
    finally:
        release.set()
        if thread.is_alive(): thread.join(timeout=12)
    if result.get('value') != 'REPAYMENT_RESULT_UNCERTAIN':
        raise RuntimeError('连接中断未返回待核对状态: ' + repr(result))


def run_disconnect_checks(actor, token, other_token, refund):
    import stores.platform_pass_repayment as module

    # The tenant receipt commits before the directory completion audit.
    claim, payload = make_claim(actor, f'shadow-{nonce}-directory-disconnect')
    original_audit = PlatformAuditEvent.objects.create
    def audit_hook(pause):
        def wrapped(**kwargs):
            if kwargs.get('action') == 'pass_repayment.register_recorded': pause()
            return original_audit(**kwargs)
        return patch.object(module.PlatformAuditEvent.objects, 'create', side_effect=wrapped)
    disconnect_worker('default', audit_hook, lambda: register(token, store.slug, payload))
    claim.refresh_from_db()
    if claim.tenant_receipt_id is not None: raise RuntimeError('断开后目录不应伪称已完成')
    with store_context(store):
        if PassRefundRepaymentReceipt.objects.filter(request_key=f'platform:{claim.pk.hex}').count() != 1:
            raise RuntimeError('门店已提交的到账记录缺失或重复')
    if recover(other_token, store.slug, 'claim', claim.pk)['status'] != 'RECORDED':
        raise RuntimeError('另一名财务未能恢复已提交的到账')
    if register(token, store.slug, payload)['status'] != 'RECORDED':
        raise RuntimeError('原到账请求恢复后未能幂等重试')

    # Kill the tenant connection after INSERT but before its commit.
    claim2, payload2 = make_claim(actor, f'shadow-{nonce}-tenant-disconnect')
    original_create = PassRefundRepaymentReceipt.objects.create
    def tenant_hook(pause):
        def wrapped(**kwargs):
            result = original_create(**kwargs)
            pause()
            return result
        return patch.object(PassRefundRepaymentReceipt.objects, 'create', side_effect=wrapped)
    disconnect_worker('store_b', tenant_hook, lambda: register(token, store.slug, payload2))
    with store_context(store):
        if PassRefundRepaymentReceipt.objects.filter(request_key=f'platform:{claim2.pk.hex}').exists():
            raise RuntimeError('门店未提交事务没有回滚')
    try:
        recover(other_token, store.slug, 'claim', claim2.pk)
        raise RuntimeError('无门店记录时不应恢复成功')
    except RepaymentError as error:
        if error.code != 'RECOVERY_EVIDENCE_MISSING': raise
    if void_pending(other_token, store.slug, 'claim', claim2.pk, '隔离测试连接中断且无记录')['status'] != 'VOIDED':
        raise RuntimeError('无门店记录时未能作废')
    try:
        register(token, store.slug, payload2)
        raise RuntimeError('作废后的旧请求不应执行')
    except RepaymentError as error:
        if error.code != 'REPAYMENT_VOIDED': raise
    if register(token, store.slug, {**payload2, 'request_id': str(uuid.uuid4())})['status'] != 'RECORDED':
        raise RuntimeError('作废后新请求未能登记')

    # A committed tenant allocation must survive lost directory completion.
    _, payload3 = make_claim(actor, f'shadow-{nonce}-allocation-disconnect')
    registration = register(token, store.slug, payload3)
    claim3 = PlatformPassRepaymentClaim.objects.get(pk=registration['claim_id'])
    act(token, store.slug, claim3.pk, 'VERIFY', {'request_id': str(uuid.uuid4())})
    request_id = str(uuid.uuid4())
    data = {'request_id': request_id, 'refund_id': refund.pk, 'amount': 200}
    def allocation_hook(pause):
        def wrapped(**kwargs):
            if kwargs.get('action') == 'pass_repayment.allocate_completed': pause()
            return original_audit(**kwargs)
        return patch.object(module.PlatformAuditEvent.objects, 'create', side_effect=wrapped)
    disconnect_worker('default', allocation_hook, lambda: act(token, store.slug, claim3.pk, 'ALLOCATE', data))
    operation = PlatformPassRepaymentOperation.objects.get(request_id=request_id)
    if operation.tenant_action_id is not None: raise RuntimeError('断开后目录不应伪称核销完成')
    with store_context(store):
        if PassRefundRepaymentAllocation.objects.filter(request_key=f'platform:{operation.pk.hex}').count() != 1:
            raise RuntimeError('门店已提交的核销记录缺失或重复')
    if recover(other_token, store.slug, 'operation', operation.pk)['status'] != 'COMPLETED':
        raise RuntimeError('另一名财务未能恢复已提交核销')
    if act(token, store.slug, claim3.pk, 'ALLOCATE', data)['status'] != 'COMPLETED':
        raise RuntimeError('原核销请求恢复后未能幂等重试')
    with store_context(store):
        if PassRefundRepaymentAllocation.objects.filter(request_key=f'platform:{operation.pk.hex}').count() != 1:
            raise RuntimeError('核销重试造成重复分配')


try:
    for model in directory_models:
        if table_exists('default', model): raise RuntimeError('本次测试表名已存在，拒绝操作')
    for model in tenant_stubs + tenant_models:
        if table_exists('store_b', model): raise RuntimeError('本次测试表名已存在，拒绝操作')
    with connections['default'].schema_editor() as editor:
        for model in directory_models:
            created.append(('default', model))
            editor.create_model(model)
    # Dependency stubs have only isolated primary keys; all repayment, refund,
    # uniqueness and locking behavior under test uses the real Django models.
    with connections['store_b'].cursor() as cursor:
        for model in tenant_stubs:
            created.append(('store_b', model))
            cursor.execute(f'CREATE TABLE {connections["store_b"].ops.quote_name(model._meta.db_table)} '
                           '(id bigint NOT NULL AUTO_INCREMENT PRIMARY KEY) ENGINE=InnoDB')
            cursor.execute(f'INSERT INTO {connections["store_b"].ops.quote_name(model._meta.db_table)} (id) VALUES (1), (2)')
    with connections['store_b'].schema_editor() as editor:
        for model in tenant_models:
            created.append(('store_b', model))
            editor.create_model(model)
    actors = []
    for index in range(2):
        actor = PlatformAccount(username=f'shadow-finance-{nonce}-{index}', phone='19900000000', is_phone_verified=True)
        actor.set_password(uuid.uuid4().hex); actor.save()
        PlatformRoleGrant.objects.create(account=actor, role='FINANCE', scope_slug='xinan')
        actors.append(actor)
    token = AccessToken(issue_tokens(actors[0], 'SMS', actors[0].phone)[0])
    other_token = AccessToken(issue_tokens(actors[1], 'SMS', actors[1].phone)[0])
    claim1, payload1 = make_claim(actors[0], f'shadow-{nonce}-first')
    run_race(claim1, payload1, token, other_token, 'registration')
    claim2, payload2 = make_claim(actors[0], f'shadow-{nonce}-second')
    run_race(claim2, payload2, token, other_token, 'void')
    claim1.refresh_from_db()
    run_verify_race(claim1, token, other_token, 'verification')
    _, payload3 = make_claim(actors[0], f'shadow-{nonce}-third')
    result3 = register(token, store.slug, payload3)
    claim3 = PlatformPassRepaymentClaim.objects.get(pk=result3['claim_id'])
    run_verify_race(claim3, token, other_token, 'void')
    with store_context(store):
        now = timezone.now()
        refunds = [ProviderPassRefund.objects.create(purchase_id=index, account_id=index,
            amount=300, funded_amount=0, shortfall_amount=300, provider_transaction_id=index,
            original_starts_at=now, original_expires_at=now, schedule_changes=[]) for index in (1, 2)]
    for index, winner in enumerate(('allocation', 'void')):
        _, payload = make_claim(actors[0], f'shadow-{nonce}-allocation-{index}')
        registered = register(token, store.slug, payload)
        claim = PlatformPassRepaymentClaim.objects.get(pk=registered['claim_id'])
        act(token, store.slug, claim.pk, 'VERIFY', {'request_id': str(uuid.uuid4())})
        run_allocate_race(claim, refunds[index], token, other_token, winner)
    run_disconnect_checks(actors[0], token, other_token, refunds[1])
    print(json.dumps({'mysql': 'registered_isolated', 'registration_first': 'passed',
                      'void_first': 'passed', 'verification_first': 'passed',
                      'void_before_verification': 'passed', 'allocation_first': 'passed',
                      'void_before_allocation': 'passed', 'directory_disconnect': 'passed',
                      'tenant_disconnect_before_commit': 'passed', 'allocation_directory_disconnect': 'passed',
                      'existing_business_rows_changed': False}))
finally:
    # Drop only names generated in this process, after verifying the target DB.
    if any(thread.is_alive() for thread in workers):
        raise RuntimeError(f'测试线程仍在运行，保留前缀 xa_tmp_pr_{nonce}_ 的本次测试表以免影响事务')
    for alias, model in reversed(created):
        if not model._meta.db_table.startswith(f'xa_tmp_pr_{nonce}_'):
            raise RuntimeError('测试表名不属于本次运行，拒绝清理')
        if table_exists(alias, model):
            with connections[alias].schema_editor() as editor:
                editor.delete_model(model)
    if any(table_exists(alias, model) for alias, model in created):
        raise RuntimeError('本次隔离测试表未全部清理')
    for model, (table, constraints) in original.items():
        model._meta.db_table, model._meta.constraints = table, constraints
