"""Provider-owned private recording library; order binding is a separate action."""
import hashlib
import math
import re
from pathlib import Path

from django.utils import timezone
from mutagen import File as read_audio
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE
from rest_framework.response import Response
from rest_framework.views import APIView

from club_accounts.models import ClubAccount
from club_accounts.permissions import IsClubAccountAuthenticated
from common.private_media import private_media_url
from stores.db import transaction
from .voice_models import VoiceRecording
from .voice_availability import MAX_BYTES, recording_available, recording_fingerprint


FORMATS = {
    '.wav': (WAVE, 'audio/wav'),
    '.mp3': (MP3, 'audio/mpeg'),
    '.m4a': (MP4, 'audio/mp4'),
    '.ogg': ((OggOpus, OggVorbis), 'audio/ogg'),
    '.opus': (OggOpus, 'audio/ogg'),
}


def recording_data(recording, request):
    available = recording_available(recording)
    return {'id': recording.pk, 'title': recording.title, 'duration_ms': recording.duration_ms,
            'size': recording.size, 'created_at': recording.created_at,
            'available': available,
            'audio_url': private_media_url(request, recording.audio) if available else ''}


def valid_request_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-z0-9_-]{8,64}', value, flags=re.ASCII))


def _locked_provider(request):
    """Keep a recording operation in the request's original store membership."""
    snapshot = request.account
    from stores.context import current_database
    if (snapshot._state.db != current_database() or type(snapshot.business_cycle) is not int
            or snapshot.business_cycle <= 0):
        return None
    account = ClubAccount.objects.select_for_update().filter(pk=snapshot.pk,
        account_type=ClubAccount.AccountType.PROVIDER, is_active=True, can_login=True).first()
    if account and (not account.license_membership_active or account.business_cycle != snapshot.business_cycle):
        return None
    return account


def _membership_changed():
    return Response({'code': 409, 'msg': '陪玩身份已变化，请重新登录后管理录音'}, status=409)


class VoiceRecordingListView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    @transaction.atomic
    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查看本人录音库'}, status=403)
        if not _locked_provider(request):
            return _membership_changed()
        raw_page = request.query_params.get('page', '1')
        if len(raw_page) > 6 or not raw_page.isascii() or not raw_page.isdigit() or not 1 <= int(raw_page) <= 100000:
            return Response({'code': 400, 'msg': '页码无效'}, status=400)
        page = int(raw_page)
        rows = list(VoiceRecording.objects.filter(owner=request.account, deleted_at__isnull=True)[(page-1)*50:page*50+1])
        return Response({'code': 0, 'data': {'list': [recording_data(row, request) for row in rows[:50]],
                                            'page': page, 'has_more': len(rows) > 50}})

    def post(self, request):
        return upload_recording(request)


def upload_recording(request):
    if request.account.account_type != ClubAccount.AccountType.PROVIDER:
        return Response({'code': 403, 'msg': '仅陪玩可上传录音'}, status=403)
    upload = request.FILES.get('audio')
    request_id = request.data.get('client_request_id')
    if not valid_request_id(request_id):
        return Response({'code': 400, 'msg': '上传请求编号无效，请重新选择录音'}, status=400)
    title = request.data.get('title', '')
    if not isinstance(title, str) or len(title.strip()) > 80:
        return Response({'code': 400, 'msg': '录音名称不能超过 80 字'}, status=400)
    if not upload or not 0 < upload.size <= MAX_BYTES:
        return Response({'code': 400, 'msg': '请选择不超过 10MB 的音频'}, status=400)
    suffix = Path(upload.name).suffix.lower()
    audio_format = FORMATS.get(suffix)
    if not audio_format:
        return Response({'code': 400, 'msg': '请选择 MP3、M4A、WAV 或 OGG 音频'}, status=400)
    try:
        parsed = read_audio(upload)
        duration = float(parsed.info.length) if parsed is not None and isinstance(parsed, audio_format[0]) else 0
        if not math.isfinite(duration) or not 0 < duration <= 24 * 60 * 60:
            raise ValueError('invalid duration')
    except Exception:
        return Response({'code': 400, 'msg': '音频无法读取，请检查文件格式'}, status=400)
    finally:
        upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    final_title = title.strip() or Path(upload.name).stem[:80] or '未命名录音'
    fingerprint = recording_fingerprint(digest.hexdigest(), final_title, audio_format[1])
    with transaction.atomic():
        # Serialize competing retries even before a recording exists; use a
        # current read under MySQL's repeatable-read isolation.
        owner = _locked_provider(request)
        if not owner:
            return _membership_changed()
        existing = VoiceRecording.objects.select_for_update().filter(owner=owner, client_request_id=request_id).first()
        if existing:
            if existing.request_fingerprint != fingerprint:
                return Response({'code': 409, 'errorCode': 'VOICE_REQUEST_CONFLICT',
                    'msg': '原请求使用了不同录音，请核对后重试'}, status=409)
            if existing.deleted_at:
                return Response({'code': 409, 'errorCode': 'VOICE_RECORDING_REMOVED',
                    'msg': '该请求的录音已移除'}, status=409)
            return Response({'code': 0, 'data': recording_data(existing, request)})
        row = VoiceRecording(owner=owner, audio=upload, title=final_title,
                             content_type=audio_format[1], duration_ms=max(1, math.ceil(duration * 1000)),
                             size=upload.size, client_request_id=request_id, request_fingerprint=fingerprint)
        # Do not unlink the file on an ambiguous commit failure: the DB may
        # already have committed. Unreferenced files need audited cleanup.
        row.save()
    return Response({'code': 0, 'data': recording_data(row, request)})


class VoiceRecordingResolveView(APIView):
    permission_classes = [IsClubAccountAuthenticated]

    @transaction.atomic
    def get(self, request):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可查询本人上传'}, status=403)
        if not _locked_provider(request):
            return _membership_changed()
        request_id = request.query_params.get('client_request_id')
        if not valid_request_id(request_id):
            return Response({'code': 400, 'msg': '上传请求编号无效'}, status=400)
        row = VoiceRecording.objects.filter(owner=request.account, client_request_id=request_id).first()
        if row is None:
            # An in-flight transaction can still commit later; this is not a
            # license to retry with a new request id.
            return Response({'code': 0, 'data': {'status': 'NOT_FOUND'}})
        if row.deleted_at:
            return Response({'code': 0, 'data': {'status': 'REMOVED'}})
        return Response({'code': 0, 'data': {'status': 'FOUND', 'recording': recording_data(row, request)}})


class VoiceRecordingDeleteView(APIView):
    permission_classes = [IsClubAccountAuthenticated]
    throttle_scope = 'order_write'

    def delete(self, request, recording_id):
        if request.account.account_type != ClubAccount.AccountType.PROVIDER:
            return Response({'code': 403, 'msg': '仅陪玩可管理录音'}, status=403)
        with transaction.atomic():
            if not _locked_provider(request):
                return _membership_changed()
            row = VoiceRecording.objects.select_for_update().filter(pk=recording_id, owner=request.account).first()
            if row is None:
                return Response({'code': 404, 'msg': '录音不存在'}, status=404)
            if not row.deleted_at:
                row.deleted_at = timezone.now()
                row.save(update_fields=['deleted_at'])
        return Response({'code': 0, 'msg': '已从录音库移除'})
