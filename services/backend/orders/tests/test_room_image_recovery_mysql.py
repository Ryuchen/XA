"""Real MySQL must serialize an in-flight upload against orphan recovery."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest import skipUnless

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user
from common.mysql_test_support import mysql_locking_statements, require_disposable_mysql
from orders.models import OrderRoom, OrderRoomImageUploadIntent, OrderRoomMessage, OrderRoomMessageImage
from orders.room_image_recovery import recover_stale_uploads
from orders.tests.factories import make_order, make_user


@skipUnless(connection.vendor == 'mysql', 'Requires isolated MySQL row locks')
class RoomImageRecoveryMySQLTest(TransactionTestCase):
    def test_cleanup_waits_for_upload_commit_and_keeps_attached_file(self):
        require_disposable_mysql(connection)
        with TemporaryDirectory(prefix='xa-room-mysql-') as media, override_settings(MEDIA_ROOT=media):
            user = make_user()
            account = get_or_create_account_for_legacy_user(user)
            room = OrderRoom.objects.create(order=make_order(user, customer_account=account))
            name = 'order_rooms/messages/' + 'e'*32 + '.png'
            content = b'synthetic-test-file'
            self.assertEqual(default_storage.save(name, ContentFile(content)), name)
            intent = OrderRoomImageUploadIntent.objects.create(path=name, author=account,
                content_sha256=hashlib.sha256(content).hexdigest())
            OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now()-timedelta(hours=2))
            started, finished = Event(), Event()
            def recover():
                close_old_connections()
                try:
                    started.set()
                    return recover_stale_uploads()
                finally:
                    finished.set()
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with transaction.atomic():
                    locked = OrderRoomImageUploadIntent.objects.select_for_update().get(pk=intent.pk)
                    future = pool.submit(recover)
                    self.assertTrue(started.wait(5))
                    deadline = monotonic() + 5
                    waiting = 0
                    while monotonic() < deadline and not waiting and not finished.is_set():
                        waiting = mysql_locking_statements(connection, 'orders_orderroomimageuploadintent')
                        if not waiting:
                            sleep(0.03)
                    self.assertGreater(waiting, 0, 'cleanup did not reach the competing MySQL row lock')
                    message = OrderRoomMessage.objects.create(room=room, author=account, author_role='BOSS',
                                                               content='[图片]', request_id='mysql-race')
                    attachment = OrderRoomMessageImage.objects.create(message=message, image=name,
                                                                       content_sha256=locked.content_sha256)
                    locked.status, locked.attachment, locked.resolved_at = 'ATTACHED', attachment, timezone.now()
                    locked.save(update_fields=['status', 'attachment', 'resolved_at'])
                self.assertEqual(future.result(timeout=10), {'cleaned': 0, 'review': 0, 'failed': 0})
            self.assertTrue(default_storage.exists(name))
