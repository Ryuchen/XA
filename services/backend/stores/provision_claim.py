"""Durable proof for retrying only a database created for this store."""
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from django.core.management.base import CommandError
from django.db import DatabaseError, transaction

from .database_identity import assert_distinct_store_database, database_identity, lock_store_registration
from .models import Store, StoreProvisioningClaim

MARKER = 'xa_store_provision_identity'


def fingerprint(alias):
    identity = database_identity(alias)
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def reserve_claim(store):
    """Commit the nonce before touching the target; no customer DB is inspected."""
    with transaction.atomic(using='default'):
        lock_store_registration()
        current = Store.objects.select_for_update().get(pk=store.pk)
        if current.initialized_at is not None or current.status != Store.Status.PROVISIONING:
            raise CommandError('仅待初始化门店可申请新数据库')
        try:
            assert_distinct_store_database(current.database_alias, exclude_store_id=current.pk)
            target = fingerprint(current.database_alias)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        claim, _ = StoreProvisioningClaim.objects.get_or_create(store=current,
            defaults={'database_fingerprint': target})
        if claim.database_fingerprint != target:
            raise CommandError('目标数据库配置已变化，不能继续初始化；请人工核对')
        return claim


def reserve_admin_intent(store, username):
    """Persist the intended first admin before any tenant-side account write."""
    username = (username or '').strip()
    with transaction.atomic(using='default'):
        lock_store_registration()
        current = Store.objects.select_for_update().get(pk=store.pk)
        claim = StoreProvisioningClaim.objects.select_for_update().filter(store=current).first()
        if claim is None:
            raise CommandError('缺少数据库归属记录，不能继续初始化')
        try:
            target = fingerprint(current.database_alias)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if claim.database_fingerprint != target:
            raise CommandError('目标数据库配置已变化，不能继续初始化；请人工核对')
        if username and claim.admin_username and username != claim.admin_username:
            raise CommandError('恢复初始化须使用原先登记的管理员账号')
        if username and not claim.admin_username:
            claim.admin_username = username
            claim.save(update_fields=['admin_username', 'updated_at'])
        return claim


def expected_marker(claim):
    return (1, claim.token.hex, claim.store_id, claim.store.slug)


def verify_sqlite_marker(path, claim):
    """Read-only open: never create or modify an existing, unverified file."""
    try:
        uri = Path(path).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            kind = db.execute('SELECT type, sql FROM sqlite_master WHERE name = ?', (MARKER,)).fetchone()
            if not kind or kind[0] != 'table' or (kind[1] or '').lstrip().upper().startswith('CREATE VIRTUAL TABLE'):
                raise CommandError('目标数据库已存在但归属无法核实，不会自动接管')
            rows = db.execute(f'SELECT id, token, store_id, slug FROM {MARKER}').fetchall()
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise CommandError('目标数据库已存在但归属无法核实，不会自动接管') from exc
    if rows != [expected_marker(claim)]:
        raise CommandError('目标数据库归属不匹配，不会自动接管')


def create_or_verify_sqlite(path, claim):
    """Exclusive creation prevents claiming a file created by another actor."""
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        verify_sqlite_marker(path, claim)
        return
    except OSError as exc:
        raise CommandError('无法创建门店数据库，请核对目标路径') from exc
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(f'CREATE TABLE {MARKER} (id INTEGER PRIMARY KEY, token TEXT NOT NULL, store_id INTEGER NOT NULL, slug TEXT NOT NULL)')
            db.execute(f'INSERT INTO {MARKER} (id, token, store_id, slug) VALUES (?, ?, ?, ?)', expected_marker(claim))
    except (sqlite3.Error, OSError) as exc:
        # Do not remove an ambiguous file: it may require human inspection.
        raise CommandError('数据库已创建但归属标记未完成，请人工核对') from exc
    verify_sqlite_marker(path, claim)


def _mysql_error_code(exc):
    for item in (exc, exc.__cause__):
        if item is not None and item.args and type(item.args[0]) is int:
            return item.args[0]
    return None


def verify_mysql_marker(cursor, name, claim):
    try:
        cursor.execute('SELECT TABLE_TYPE FROM information_schema.tables WHERE table_schema = %s AND table_name = %s',
                       [name, MARKER])
        table = cursor.fetchone()
        if not table or table[0] != 'BASE TABLE':
            raise CommandError('目标数据库已存在但归属无法核实，不会自动接管')
        cursor.execute(f'SELECT id, token, store_id, slug FROM `{name}`.`{MARKER}`')
        rows = cursor.fetchall()
    except DatabaseError as exc:
        raise CommandError('目标数据库已存在但归属无法核实，不会自动接管') from exc
    if list(rows) != [expected_marker(claim)]:
        raise CommandError('目标数据库归属不匹配，不会自动接管')


def create_or_verify_mysql(server, cursor, name, claim):
    try:
        cursor.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    except DatabaseError as exc:
        if _mysql_error_code(exc) != 1007:
            raise CommandError('创建数据库失败；若目标库已存在，请先人工核对归属，不会自动接管') from exc
        verify_mysql_marker(cursor, name, claim)
        return
    try:
        cursor.execute(f'CREATE TABLE `{name}`.`{MARKER}` '
            '(id SMALLINT PRIMARY KEY, token CHAR(32) NOT NULL, store_id BIGINT NOT NULL, slug VARCHAR(48) NOT NULL)')
        cursor.execute(f'INSERT INTO `{name}`.`{MARKER}` (id, token, store_id, slug) VALUES (%s, %s, %s, %s)',
                       expected_marker(claim))
        server.commit()
        verify_mysql_marker(cursor, name, claim)
    except DatabaseError as exc:
        raise CommandError('数据库已创建但归属标记未完成，请人工核对') from exc


def verify_owned_database(alias, claim):
    """Recheck the target immediately before migration; never create here."""
    from django.db import connections
    from .database_identity import identity_from_config
    config = connections[alias].settings_dict
    engine, name = config['ENGINE'], str(config.get('NAME') or '')
    try:
        actual_identity = identity_from_config(alias, config)
        configured_identity = database_identity(alias)
    except ValueError as exc:
        raise CommandError(str(exc)) from exc
    if actual_identity != configured_identity or claim.database_fingerprint != fingerprint(alias):
        raise CommandError('目标数据库配置已变化，不能继续初始化；请人工核对')
    if 'sqlite' in engine:
        verify_sqlite_marker(name, claim)
        return
    if 'mysql' not in engine:
        raise CommandError('该数据库类型暂不支持安全恢复')
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute('SELECT DATABASE()')
            actual = cursor.fetchone()[0]
            if not isinstance(actual, str) or actual.casefold() != name.casefold():
                raise CommandError('当前连接与登记的目标数据库不一致')
            verify_mysql_marker(cursor, name, claim)
    except DatabaseError as exc:
        raise CommandError('目标数据库连接不可用，无法核实归属') from exc
