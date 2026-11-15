"""Signed shared persistent hostname index; it is never an auth credential."""
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils.crypto import salted_hmac

SALT = 'xa.store-domain-index.v1'


def _directory():
    configured = getattr(settings, 'STORE_DOMAIN_INDEX_DIR', '')
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError('门店域名共享索引目录尚未配置')
    directory = Path(configured).resolve()
    if not directory.is_dir():
        raise ValueError('门店域名共享索引目录不可用')
    return directory


def _path(hostname):
    return _directory() / (hashlib.sha256(hostname.encode('ascii')).hexdigest() + '.json')


def _signature(payload, secret):
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return salted_hmac(SALT, raw, secret=secret, algorithm='sha256').hexdigest()


def _write_locked(hostname, *, slug, alias, store_id, revision, active):
    payload = {'schema': 1, 'hostname': hostname, 'slug': slug, 'database_alias': alias,
               'store_id': store_id, 'revision': revision, 'active': active}
    data = json.dumps({'payload': payload, 'signature': _signature(payload, settings.SECRET_KEY)},
                      sort_keys=True, separators=(',', ':')).encode('utf-8')
    target = _path(hostname)
    previous = read(hostname)
    if target.exists() and previous is None:
        raise ValueError('现有域名索引无法验证，请联系平台核对')
    if previous and (previous['slug'] != slug or previous['database_alias'] != alias
                     or previous['store_id'] != store_id or previous['revision'] > revision):
        raise ValueError('域名索引归属或版本不一致')
    fd, temp = tempfile.mkstemp(prefix='.domain-', dir=str(target.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
        if hasattr(os, 'O_DIRECTORY'):
            directory_fd = os.open(str(target.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write(hostname, *, slug, alias, store_id, revision, active):
    # Shared filesystem advisory lock serializes publication by all workers.
    lock_path = _path(hostname).with_suffix('.lock')
    with lock_path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if lock.read(1) == b'':
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _write_locked(hostname, slug=slug, alias=alias, store_id=store_id,
                          revision=revision, active=active)
        finally:
            if os.name == 'nt':
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def read(hostname):
    try:
        raw = _path(hostname).read_bytes()
        if len(raw) > 8192:
            return None
        wrapper = json.loads(raw)
        if not isinstance(wrapper, dict) or set(wrapper) != {'payload', 'signature'}:
            return None
        payload, signature = wrapper['payload'], wrapper['signature']
        if not isinstance(payload, dict) or set(payload) != {'schema', 'hostname', 'slug', 'database_alias',
                'store_id', 'revision', 'active'} or not isinstance(signature, str):
            return None
        if not any(hmac.compare_digest(signature, _signature(payload, secret))
                   for secret in [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', [])]):
            return None
        if (payload['schema'] != 1 or payload['hostname'] != hostname or type(payload['store_id']) is not int
                or payload['store_id'] <= 0 or type(payload['revision']) is not int or payload['revision'] < 1
                or type(payload['active']) is not bool):
            return None
        from .hosts import STORE_SLUG
        if not isinstance(payload['slug'], str) or not STORE_SLUG.fullmatch(payload['slug']):
            return None
        if not isinstance(payload['database_alias'], str) or not payload['database_alias']:
            return None
        return payload
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def active_hostnames():
    """Enumerate only signed active entries for offline worker bootstrap.

    Filenames and unsigned content never establish a store binding. The
    candidate hostname is re-read through the normal signature verifier.
    """
    try:
        directory = _directory()
        entries = list(directory.glob('*.json'))
    except (OSError, ValueError):
        return []
    hosts = set()
    for path in entries:
        try:
            raw = path.read_bytes()
            if len(raw) > 8192:
                continue
            wrapper = json.loads(raw)
            payload = wrapper.get('payload') if isinstance(wrapper, dict) else None
            hostname = payload.get('hostname') if isinstance(payload, dict) else None
            if not isinstance(hostname, str) or not hostname.isascii() or path != _path(hostname):
                continue
            verified = read(hostname)
            if verified and verified['active']:
                hosts.add(hostname)
        except (OSError, ValueError, TypeError, UnicodeError):
            continue
    return sorted(hosts)
