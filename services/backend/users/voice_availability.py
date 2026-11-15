"""Check local recording availability without changing the recording or file."""
import os

from django.http import Http404

from common.private_media import _local_file


def recording_available(recording):
    try:
        if recording.content_type not in ('audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg'):
            return False
        with _local_file(recording.audio).open('rb') as handle:
            return recording.size > 0 and os.fstat(handle.fileno()).st_size == recording.size
    except (Http404, OSError, ValueError, NotImplementedError):
        return False
