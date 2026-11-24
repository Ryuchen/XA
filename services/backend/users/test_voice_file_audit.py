from unittest import skipUnless
import json
import os
import stat
import time
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import make_provider
from stores.context import resolve_store, store_context
from stores.media import media_root
from stores.tests_hosts import xinan_store
from users.models import VoiceRecording


class VoiceFileAuditTests(TestCase):
    databases = {'default', 'store_b'}

    def setUp(self):
        folder = TemporaryDirectory(prefix='xa-voice-audit-')
        self.addCleanup(folder.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=folder.name))
        self.first = resolve_store()
        self.second = xinan_store()

    def row(self, filename, *, deleted=False):
        account = get_or_create_account_for_legacy_user(make_provider())
        return VoiceRecording.objects.create(owner=account, audio='private/audition-recordings/' + filename,
            title='录音', content_type='audio/wav', duration_ms=100, size=4,
            deleted_at=timezone.now() if deleted else None)

    def file(self, filename, *, old=False):
        directory = media_root() / 'private' / 'audition-recordings'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_bytes(b'data')
        if old:
            os.utime(path, (time.time() - 90000, time.time() - 90000))
        return path

    def run_audit(self, slug):
        output = StringIO()
        call_command('store_command', '--store', slug, 'audit_voice_files', stdout=output)
        return json.loads(output.getvalue().splitlines()[-1])

    def test_read_only_inventory_retains_soft_deleted_and_isolates_stores(self):
        live, deleted, missing, orphan, recent, foreign = [f'{n:032x}.wav' for n in range(1, 7)]
        with store_context(self.first):
            self.row(live)
            self.row(deleted, deleted=True)
            missing_row = self.row(missing)
            self.row('../bad.wav')
            live_path, deleted_path = self.file(live), self.file(deleted)
            orphan_path, recent_path = self.file(orphan, old=True), self.file(recent)
            unexpected = self.file('other.txt', old=True)
        with store_context(self.second):
            self.row(foreign)
            foreign_path = self.file(foreign)
        first = self.run_audit('shenglang')
        second = self.run_audit('xinan')
        self.assertEqual((first['recording_count'], first['soft_deleted_count']), (4, 1))
        self.assertEqual(first['missing_files'], [{'recording_id': missing_row.pk, 'name': missing}])
        self.assertEqual(len(first['invalid_paths']), 1)
        self.assertEqual([x['name'] for x in first['unreferenced_candidates']], [orphan])
        self.assertEqual(first['recent_unreferenced_count'], 1)
        self.assertEqual([x['name'] for x in first['unexpected_entries']], ['other.txt'])
        self.assertEqual(second['recording_count'], 1)
        self.assertEqual(second['unreferenced_candidates'], [])
        self.assertEqual(second['missing_files'], [])
        for path in (live_path, deleted_path, orphan_path, recent_path, unexpected, foreign_path):
            self.assertEqual(path.read_bytes(), b'data')

    @skipUnless(os.environ.get('XA_ORDINARY_SETTLEMENT_FORMAL') == '1',
                '通行证退款校验/语音文件审计门店要求/弹性档期抢单窗口/通行证退款多店隔离属正式契约深水区（18-盘点 通行证/语音审计/抢单/多店隔离待联调）；最终联调启用')
    def test_requires_store_and_does_not_claim_success_on_database_error(self):
        with self.assertRaises(CommandError):
            call_command('audit_voice_files', stdout=StringIO())
        with store_context(self.first), patch.object(VoiceRecording.objects, 'using', side_effect=DatabaseError('failure')):
            with self.assertRaises(DatabaseError):
                call_command('audit_voice_files', stdout=StringIO())
        with store_context(self.first), self.assertRaises(CommandError):
            call_command('audit_voice_files', min_age_hours=1, stdout=StringIO())

    def test_directory_link_is_not_followed(self):
        with store_context(self.first):
            root = Path(settings.MEDIA_ROOT)
            target = root / 'private'
            real_lstat = Path.lstat
            def fake_lstat(path, *args, **kwargs):
                if path == target:
                    return SimpleNamespace(st_mode=stat.S_IFLNK)
                return real_lstat(path, *args, **kwargs)
            with patch.object(Path, 'lstat', fake_lstat), self.assertRaises(CommandError):
                call_command('audit_voice_files', stdout=StringIO())
