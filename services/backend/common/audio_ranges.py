"""Bounded single-range streaming for authorized private audio files."""
import os
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse


class BoundedAudio:
    def __init__(self, handle, start, length):
        self.handle = handle
        self.remaining = length
        handle.seek(start)

    def __iter__(self):
        return self

    def __next__(self):
        if self.remaining <= 0:
            raise StopIteration
        chunk = self.handle.read(min(self.remaining, 64 * 1024))
        if not chunk:
            raise StopIteration
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.handle.close()


def audio_response(request, handle, content_type):
    """Serve a validated file after the caller has checked its private grant."""
    size = os.fstat(handle.fileno()).st_size
    range_header = request.headers.get('Range', '').strip()
    # Range is a GET modifier. We do not have a stable public validator for
    # If-Range, so send the complete representation when it is present.
    if request.method == 'HEAD':
        handle.close()
        response = HttpResponse(content_type=content_type)
        response['Content-Length'] = str(size)
    elif (not range_header or request.headers.get('If-Range') or
          not range_header.lower().startswith('bytes=') or ',' in range_header):
        response = FileResponse(handle, content_type=content_type)
    else:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header, re.IGNORECASE | re.ASCII)
        parts = match.groups() if match else ('', '')
        valid = bool(match and any(parts) and all(len(part) <= 19 for part in parts) and size > 0)
        if valid:
            first, last = parts
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
                valid = start < size and start <= end
            else:
                length = int(last)
                valid = length > 0
                start, end = max(0, size - length), size - 1
        if not valid:
            handle.close()
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
        else:
            length = end - start + 1
            response = StreamingHttpResponse(BoundedAudio(handle, start, length), status=206, content_type=content_type)
            response['Content-Range'] = f'bytes {start}-{end}/{size}'
            response['Content-Length'] = str(length)
    response['Accept-Ranges'] = 'bytes'
    return response
