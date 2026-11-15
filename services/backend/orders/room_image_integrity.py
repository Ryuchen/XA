"""Verify immutable shared room images without trusting their file names."""
import hashlib
import re

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def image_matches(handle, expected):
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        return False
    digest, total = hashlib.sha256(), 0
    for chunk in iter(lambda: handle.read(64 * 1024), b''):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            return False
        digest.update(chunk)
    return total > 0 and digest.hexdigest() == expected
