"""Read-only, store-local evidence for private audition recording files."""
import json
import os
import re
import stat
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from stores.context import current_database, current_store
from stores.media import StoreFileSystemStorage, media_root
from users.models import VoiceRecording


PREFIX = 'private/audition-recordings/'
FILENAME = re.compile(r'[0-9a-f]{32}\.(?:wav|mp3|m4a|ogg|opus)', re.ASCII)


def _recording_directory():
    """Do not traverse links, including links inside a tenant namespace."""
    base = Path(settings.MEDIA_ROOT).resolve()
    directory = media_root() / 'private' / 'audition-recordings'
    try:
        parts = directory.relative_to(base).parts
    except ValueError as exc:
        raise CommandError('录音目录不在配置的媒体根目录内') from exc
    current = base
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CommandError('无法检查录音目录') from exc
        if not stat.S_ISDIR(mode):
            raise CommandError('录音目录包含链接或非目录路径，已停止盘点')
    return current


def audit_voice_files(*, min_age_hours=24):
    store = current_store()
    if store is None:
        raise CommandError('必须通过 store_command --store 指定门店')
    if not isinstance(default_storage, StoreFileSystemStorage):
        raise CommandError('当前存储不是门店本地文件系统，不能执行此盘点')
    if min_age_hours < 24:
        raise CommandError('待核对文件最短观察时间为 24 小时')
    now = timezone.now()
    cutoff = (now - timedelta(hours=min_age_hours)).timestamp()
    database = current_database()
    # Includes soft-deleted rows: submitted auditions may still reference them.
    refs = list(VoiceRecording.objects.using(database).values_list('id', 'audio', 'deleted_at'))
    valid_refs, invalid_refs = {}, []
    for pk, name, deleted_at in refs:
        filename = name[len(PREFIX):] if name.startswith(PREFIX) else ''
        if not FILENAME.fullmatch(filename):
            invalid_refs.append({'recording_id': pk, 'reason': 'invalid_recording_path'})
            continue
        valid_refs.setdefault(filename, []).append(pk)

    directory = _recording_directory()
    regular, unsafe = {}, []
    if directory is not None:
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or not FILENAME.fullmatch(entry.name):
                        unsafe.append({'name': entry.name, 'reason': 'unexpected_entry'})
                    else:
                        regular[entry.name] = info
        except OSError as exc:
            raise CommandError('无法完整扫描录音目录，盘点结果无效') from exc

    missing = [{'recording_id': pk, 'name': name} for name, ids in valid_refs.items()
               if name not in regular for pk in ids]
    unreferenced = [{'name': name, 'size': info.st_size,
                     'modified_at': datetime.fromtimestamp(info.st_mtime, tz=datetime_timezone.utc).isoformat()}
                    for name, info in regular.items() if name not in valid_refs and info.st_mtime <= cutoff]
    recent = sum(name not in valid_refs and info.st_mtime > cutoff for name, info in regular.items())
    duplicate = [{'name': name, 'recording_ids': ids} for name, ids in valid_refs.items() if len(ids) > 1]
    return {'store': store.slug, 'database_alias': database, 'observed_at': now.isoformat(),
            'min_age_hours': min_age_hours, 'directory_present': directory is not None,
            'recording_count': len(refs), 'soft_deleted_count': sum(deleted is not None for _, _, deleted in refs),
            'missing_files': sorted(missing, key=lambda row: row['recording_id']),
            'invalid_paths': sorted(invalid_refs, key=lambda row: row['recording_id']),
            'duplicate_references': sorted(duplicate, key=lambda row: row['name']),
            'unreferenced_candidates': sorted(unreferenced, key=lambda row: row['name']),
            'recent_unreferenced_count': recent, 'unexpected_entries': sorted(unsafe, key=lambda row: row['name']),
            'warning': '只读观察，不证明文件可删除；上传或事务可能仍在进行，须人工核对。'}


class Command(BaseCommand):
    help = '只读盘点指定门店私有试音文件，不能删除文件'

    def add_arguments(self, parser):
        parser.add_argument('--min-age-hours', type=int, default=24)

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(audit_voice_files(min_age_hours=options['min_age_hours']), ensure_ascii=False))
