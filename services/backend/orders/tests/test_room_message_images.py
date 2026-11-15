from io import BytesIO
from datetime import timedelta
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.conf import settings
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, OrderRoomMessage, OrderRoomMessageImage, OrderRoomImageUploadIntent
from orders.state_machine import transition
from orders.tests.factories import make_order, make_provider, make_service, make_user
from orders.tests.test_order_rooms import OrderRoomTests
from stores.context import resolve_store, store_context
from stores.db import transaction
from stores.tests_hosts import xinan_store


def upload(color='red'):
    content = BytesIO()
    Image.new('RGB', (4, 4), color).save(content, 'PNG')
    return SimpleUploadedFile('picture.png', content.getvalue(), content_type='image/png')


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomMessageImageTests(APITransactionTestCase):
    def setUp(self):
        OrderRoomTests.setUp(self)
        self.order = OrderRoomTests.order(self)
        OrderRoomTests.assign(self, self.order, [(self.provider, self.provider_account)])
        self.url = f'/api/orders/orders/{self.order.pk}/room/messages/image/'
        self.list_url = f'/api/orders/orders/{self.order.pk}/room/messages/'

    def send(self, client, key, color='red'):
        return client.post(self.url, {'client_request_id': key, 'image': upload(color)}, format='multipart')

    def test_private_tickets_recheck_transfer_and_idempotency(self):
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            boss = self.send(self.boss_client, 'boss-photo')
            own = self.send(self.provider_client, 'own-photo', 'blue')
            self.assertEqual((boss.status_code, own.status_code), (200, 200))
            self.assertEqual(self.send(self.boss_client, 'boss-photo').data['data']['id'], boss.data['data']['id'])
            self.assertEqual(OrderRoomImageUploadIntent.objects.filter(status='ATTACHED').count(), 2)
            self.assertFalse(OrderRoomImageUploadIntent.objects.filter(status='PENDING').exists())
            self.assertEqual(self.send(self.boss_client, 'boss-photo', 'blue').status_code, 409)
            self.assertEqual(self.boss_client.post(self.list_url, {'content': '[图片]',
                'client_request_id': 'boss-photo'}, format='json').status_code, 409)
            rows = self.provider_client.get(self.list_url).data['data']['list']
            urls = {row['id']: urlsplit(row['image_url']).path for row in rows}
            boss_ticket, own_ticket = urls[boss.data['data']['id']], urls[own.data['data']['id']]
            for ticket in (boss_ticket, own_ticket):
                response = self.provider_client.get(ticket)
                self.assertEqual(response.status_code, 200)
                response.close()
            assignment = self.order.providers.get(provider_account=self.provider_account)
            assignment.released_at = timezone.now(); assignment.save(update_fields=['released_at'])
            OrderProvider.objects.create(order=self.order, provider=self.other, provider_account=self.other_account)
            Order.objects.filter(pk=self.order.pk).update(provider=self.other, provider_account=self.other_account)
            self.assertEqual(self.provider_client.get(boss_ticket).status_code, 404)
            response = self.provider_client.get(own_ticket)
            self.assertEqual(response.status_code, 200)
            response.close()
            own_rows = self.provider_client.get(self.list_url).data['data']['list']
            self.assertEqual([item['id'] for item in own_rows], [own.data['data']['id']])
            self.assertEqual(self.send(self.provider_client, 'new-photo').status_code, 403)
            self.assertEqual(self.send(self.provider_client, 'own-photo', 'blue').status_code, 200)
            self.assertEqual(self.other_client.get(self.list_url).data['data']['total'], 2)
            name = OrderRoomMessageImage.objects.get(message_id=boss.data['data']['id']).image.name
            self.assertEqual(self.boss_client.get('/media/'+name).status_code, 404)

    def test_invalid_upload_and_failed_db_write_is_recovered_safely(self):
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            bad = SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')
            self.assertEqual(self.boss_client.post(self.url, {'image': bad, 'client_request_id': 'bad'},
                                                   format='multipart').status_code, 400)
            large = SimpleUploadedFile('large.png', b'x' * (5 * 1024 * 1024 + 1), content_type='image/png')
            self.assertEqual(self.boss_client.post(self.url, {'image': large, 'client_request_id': 'large'},
                                                   format='multipart').status_code, 400)
            self.assertEqual(self.send(self.other_client, 'not-member').status_code, 404)
            with patch('orders.room_message_images.OrderRoomMessageImage.save', side_effect=RuntimeError('save failed')):
                with self.assertRaises(RuntimeError):
                    self.send(self.boss_client, 'fail')
            self.assertFalse(OrderRoomMessage.objects.exists())
            intent = OrderRoomImageUploadIntent.objects.get()
            self.assertEqual(intent.status, 'PENDING')
            self.assertEqual(len([p for p in Path(media).rglob('*') if p.is_file()]), 1)
            OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now()-timedelta(hours=2))
            from orders.room_image_recovery import recover_stale_uploads
            self.assertEqual(recover_stale_uploads(), {'cleaned': 1, 'review': 0, 'failed': 0})
            intent.refresh_from_db()
            self.assertEqual(intent.status, 'CLEANED')
            self.assertEqual([p for p in Path(media).rglob('*') if p.is_file()], [])

    def test_requires_durable_outside_transaction_and_preserves_intended_file(self):
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            with transaction.atomic():
                self.assertEqual(self.send(self.boss_client, 'nested').status_code, 503)
            self.assertFalse(OrderRoomImageUploadIntent.objects.exists())
            response = self.send(self.boss_client, 'committed')
            self.assertEqual(response.status_code, 200, response.data)
            intent = OrderRoomImageUploadIntent.objects.get()
            self.assertEqual(intent.status, 'ATTACHED')
            self.assertEqual((intent.room.order_id, intent.request_id), (self.order.pk, 'committed'))
            self.assertTrue(default_storage.exists(intent.path))
            OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now()-timedelta(hours=2))
            from orders.room_image_recovery import recover_stale_uploads
            self.assertEqual(recover_stale_uploads(), {'cleaned': 0, 'review': 0, 'failed': 0})
            self.assertTrue(default_storage.exists(intent.path))
            # Even inconsistent pending evidence must never delete a referenced image.
            OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(
                status='PENDING', attachment=None, resolved_at=None)
            self.assertEqual(recover_stale_uploads(), {'cleaned': 0, 'review': 1, 'failed': 0})
            self.assertTrue(default_storage.exists(intent.path))

    def test_uncertain_write_can_retry_without_losing_successful_file(self):
        from orders.room_image_recovery import recover_stale_uploads
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            with patch('orders.room_message_images.OrderRoomMessageImage.save', side_effect=OSError('write uncertain')):
                response = self.send(self.boss_client, 'retry-picture')
            self.assertEqual((response.status_code, response.data['errorCode']), (503, 'IMAGE_RESULT_UNCERTAIN'))
            retry = self.send(self.boss_client, 'retry-picture')
            self.assertEqual(retry.status_code, 200, retry.data)
            pending = OrderRoomImageUploadIntent.objects.get(status='PENDING')
            attached = OrderRoomImageUploadIntent.objects.get(status='ATTACHED')
            OrderRoomImageUploadIntent.objects.filter(pk=pending.pk).update(created_at=timezone.now()-timedelta(hours=2))
            self.assertEqual(recover_stale_uploads(), {'cleaned': 1, 'review': 0, 'failed': 0})
            self.assertFalse(default_storage.exists(pending.path))
            self.assertTrue(default_storage.exists(attached.path))
            self.assertEqual(OrderRoomMessage.objects.count(), 1)

    def test_recovery_rejects_unproven_paths_and_retries_storage_failures(self):
        from orders.room_image_recovery import recover_stale_uploads
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            content = b'synthetic-image-bytes'
            digest = hashlib.sha256(content).hexdigest()
            names = ['order_rooms/messages/' + char * 32 + '.png' for char in ('a', 'b', 'c', 'd')]
            for name in names:
                self.assertEqual(default_storage.save(name, ContentFile(content)), name)
            old = timezone.now()-timedelta(hours=2)
            clean = OrderRoomImageUploadIntent.objects.create(path=names[0], content_sha256=digest, author=self.boss_account)
            mismatch = OrderRoomImageUploadIntent.objects.create(path=names[1], content_sha256='0'*64, author=self.boss_account)
            retry = OrderRoomImageUploadIntent.objects.create(path=names[2], content_sha256=digest, author=self.boss_account)
            fresh = OrderRoomImageUploadIntent.objects.create(path=names[3], content_sha256=digest, author=self.boss_account)
            invalid = OrderRoomImageUploadIntent.objects.create(path='../outside.png', content_sha256=digest, author=self.boss_account)
            OrderRoomImageUploadIntent.objects.filter(pk__in=[clean.pk, mismatch.pk, retry.pk, invalid.pk]).update(created_at=old)
            original_delete = default_storage.delete
            def fail_one(name):
                if name == names[2]:
                    raise OSError('storage unavailable')
                return original_delete(name)
            with patch.object(default_storage, 'delete', side_effect=fail_one):
                self.assertEqual(recover_stale_uploads(), {'cleaned': 1, 'review': 2, 'failed': 1})
            for row in (clean, mismatch, retry, fresh, invalid):
                row.refresh_from_db()
            self.assertEqual([row.status for row in (clean, mismatch, retry, fresh, invalid)], ['CLEANED', 'REVIEW', 'PENDING', 'PENDING', 'REVIEW'])
            self.assertFalse(default_storage.exists(names[0]))
            self.assertTrue(all(default_storage.exists(name) for name in names[1:]))
            self.assertEqual(recover_stale_uploads(), {'cleaned': 1, 'review': 0, 'failed': 0})
            self.assertFalse(default_storage.exists(names[2]))

    def test_recovery_rejects_storage_path_outside_store_root(self):
        from orders.room_image_recovery import recover_stale_uploads
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            name = 'order_rooms/messages/' + 'e'*32 + '.png'
            intent = OrderRoomImageUploadIntent.objects.create(path=name, content_sha256='0'*64, author=self.boss_account)
            OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now()-timedelta(hours=2))
            outside = str(Path(media).parent / 'not-a-room-upload.png')
            with patch.object(default_storage, 'path', return_value=outside), \
                 patch.object(default_storage, 'delete', side_effect=AssertionError('must not delete outside root')):
                self.assertEqual(recover_stale_uploads(), {'cleaned': 0, 'review': 1, 'failed': 0})

    def test_changed_image_is_not_served_or_claimed_as_successful_retry(self):
        with TemporaryDirectory(prefix='xa-room-images-') as media, override_settings(MEDIA_ROOT=media):
            response = self.send(self.boss_client, 'tampered')
            self.assertEqual(response.status_code, 200, response.data)
            ticket = urlsplit(response.data['data']['image_url']).path
            attachment = OrderRoomMessageImage.objects.get(message_id=response.data['data']['id'])
            with open(attachment.image.path, 'wb') as handle:
                handle.write(upload('blue').read())
            self.assertEqual(self.boss_client.get(ticket).status_code, 404)
            retry = self.send(self.boss_client, 'tampered')
            self.assertEqual((retry.status_code, retry.data['errorCode']), (409, 'IMAGE_REVIEW'))
            self.assertEqual(OrderRoomMessage.objects.count(), 1)


@skipUnless('store_b' in settings.DATABASES, 'requires isolated two-store databases')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, ALLOWED_HOSTS=['*'], ORIGINAL_ATTRIBUTION_ENABLED=False)
class RoomMessageImagesStoresTest(APITransactionTestCase):
    databases = {'default', 'store_b'} if 'store_b' in settings.DATABASES else {'default'}

    def test_same_identifiers_do_not_share_attachment_or_ticket(self):
        with TemporaryDirectory(prefix='xa-room-images-stores-') as media, override_settings(MEDIA_ROOT=media):
            rows = []
            for store, host in ((resolve_store(), 'sl.esports.club'), (xinan_store(), 'xa.esports.club')):
                with store_context(store):
                    boss, player = make_user(), make_provider(deposit_mode='EARNINGS')
                    account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
                    order = make_order(boss, service=make_service(room_enabled=True), customer_account=account,
                                       payment_status='PAID')
                    def assign(row):
                        row.provider, row.provider_account = player, player_account
                        OrderProvider.objects.create(order=row, provider=player, provider_account=player_account)
                    transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
                    client = APIClient(HTTP_HOST=host, HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(account)[0])
                    rows.append((store, order.pk, client))
            self.assertEqual(rows[0][1], rows[1][1])
            path = f'/api/orders/orders/{rows[1][1]}/room/messages/image/'
            response = rows[1][2].post(path, {'image': upload(), 'client_request_id': 'picture'}, format='multipart')
            self.assertEqual(response.status_code, 200, response.data)
            parts = urlsplit(response.data['data']['image_url'])
            ticket = parts.path + ('?' + parts.query if parts.query else '')
            image_response = rows[1][2].get(ticket)
            self.assertEqual(image_response.status_code, 200)
            image_response.close()
            self.assertNotEqual(rows[0][2].get(ticket).status_code, 200)
            self.assertEqual(rows[0][2].get(f'/api/orders/orders/{rows[0][1]}/room/messages/').data['data']['total'], 0)

    def test_recovery_only_removes_its_own_store_orphan(self):
        from orders.tasks import recover_room_image_uploads
        from stores.tasks import PERIODIC_STORE_TASKS
        self.assertIn('orders.tasks.recover_room_image_uploads', PERIODIC_STORE_TASKS)
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['recover-room-image-uploads']['task'],
                         'orders.tasks.recover_room_image_uploads')
        with TemporaryDirectory(prefix='xa-room-image-recovery-stores-') as media, override_settings(MEDIA_ROOT=media):
            stores = (resolve_store(), xinan_store())
            name = 'order_rooms/messages/' + 'f'*32 + '.png'
            data = b'private-test-file'
            for store in stores:
                with store_context(store):
                    account = get_or_create_account_for_legacy_user(make_user())
                    self.assertEqual(default_storage.save(name, ContentFile(data)), name)
                    intent = OrderRoomImageUploadIntent.objects.create(path=name, author=account,
                        content_sha256=hashlib.sha256(data).hexdigest())
                    OrderRoomImageUploadIntent.objects.filter(pk=intent.pk).update(created_at=timezone.now()-timedelta(hours=2))
            with store_context(stores[1]):
                self.assertEqual(recover_room_image_uploads.run(), {'cleaned': 1, 'review': 0, 'failed': 0})
                self.assertFalse(default_storage.exists(name))
            with store_context(stores[0]):
                self.assertTrue(default_storage.exists(name))
                self.assertEqual(OrderRoomImageUploadIntent.objects.get().status, 'PENDING')
