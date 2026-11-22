"""Check local recording availability and its original upload identity."""
import hashlib
import json
import os

from django.http import Http404

from common.private_media import _local_file

MAX_BYTES = 10 * 1024 * 1024
CONTENT_TYPES = {'audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg'}


def recording_fingerprint(sha256, title, content_type):
    payload = {'sha256': sha256, 'title': title, 'content_type': content_type}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def recording_matches(recording, handle, *, verify_content):
    if (recording.content_type not in CONTENT_TYPES or not 0 < recording.size <= MAX_BYTES
            or os.fstat(handle.fileno()).st_size != recording.size):
        return False
    # Pre-idempotency recordings have no saved upload fingerprint.
    if not verify_content or not recording.request_fingerprint:
        return True
    digest = hashlib.sha256()
    remaining = recording.size
    while remaining:
        chunk = handle.read(min(remaining, 64 * 1024))
        if not chunk:
            return False
        digest.update(chunk)
        remaining -= len(chunk)
    if handle.read(1):
        return False
    return recording_fingerprint(digest.hexdigest(), recording.title, recording.content_type) == recording.request_fingerprint


def recording_available(recording, *, verify_content=False):
    try:
        with _local_file(recording.audio).open('rb') as handle:
            return recording_matches(recording, handle, verify_content=verify_content)
    except (Http404, OSError, ValueError, NotImplementedError):
        return False
