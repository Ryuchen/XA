"""The native homepage rechecks an actual target changed by staff."""
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import LiveServerTestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from coupons.models import Coupon
from orders.models import Gameplay, Order
from orders.tests.factories import make_console_user, make_service, make_user, make_wallet
from users.models import BossType
from wallet.models import Transaction, Wallet


@skipUnless(os.environ.get('XA_BANNER_LIVE') == '1', 'Requires isolated live server and Node')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, CELERY_TASK_ALWAYS_EAGER=False)
class NativeBannerLiveTest(LiveServerTestCase):
    def test_owner_qualification_changes_open_home_without_affecting_other_owner(self):
        node = shutil.which('node')
        self.assertTrue(node)
        boss, other, staff = make_user(), make_user(), make_console_user(['banner:view', 'banner:edit', 'service:view', 'service:edit', 'coupon:view', 'coupon:edit'])
        make_wallet(boss, balance=500000)
        boss_account = get_or_create_account_for_legacy_user(boss)
        other_account = get_or_create_account_for_legacy_user(other)
        link_legacy_relations(boss, boss_account)
        link_legacy_relations(other, other_account)
        boss_account.boss_type = BossType.objects.create(name='隔离会员八折', discount_rate=80)
        boss_account.save(update_fields=['boss_type'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss_token = issue_account_tokens(boss_account)[0]
        other_token = issue_account_tokens(other_account)[0]
        staff_token = issue_account_tokens(staff_account)[0]
        gameplay = Gameplay.objects.create(name='新客体验', code='banner-value-experience', theme='experience')
        service = make_service(experience_kind='NEW_VALUE', platform='PC', security_level='ANY', gameplay=gameplay, catalog_family='新客套餐')
        other_service = make_service(experience_kind='NEW_FIRST', platform='PC', security_level='ANY', gameplay=gameplay, catalog_family='新客套餐')
        regular_gameplay = Gameplay.objects.create(name='普通套餐', code='banner-regular', theme='fun')
        regular_service = make_service(platform='PC', security_level='ANY', gameplay=regular_gameplay, catalog_family='普通套餐')
        regular_other = make_service(platform='PC', security_level='ANY', gameplay=regular_gameplay, catalog_family='另一普通套餐')
        regular_coupon = Coupon.objects.create(name='指定套餐优惠券', discount_type='DIRECT', amount=1000,
            service_ids=[regular_service.pk])
        general_coupon = Coupon.objects.create(name='通用优惠券', discount_type='DIRECT', amount=500)
        rate_coupon = Coupon.objects.create(name='九折券', discount_type='RATE', discount_rate=90,
            amount=1, service_ids=[regular_service.pk])
        coupon = Coupon.objects.create(name='轮播新客资格', discount_type='QUALIFICATION', amount=0,
            service_ids=[service.pk], experience_kind='NEW_VALUE', platform='PC')
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        image = io.BytesIO()
        Image.new('RGB', (750, 320), '#224466').save(image, format='PNG')
        with tempfile.TemporaryDirectory(prefix='xa-banner-qualification-') as media, override_settings(MEDIA_ROOT=media):
            created = client.post('/api/admin/banners/', {'title': '新客资格轮播', 'is_active': 'true',
                'link_type': 'PRODUCT', 'link_value': str(service.pk),
                'image': SimpleUploadedFile('banner.png', image.getvalue(), content_type='image/png')}, format='multipart')
            self.assertEqual(created.data['code'], 0, created.data)
            banner_id = created.data['data']['id']
            self.assertTrue(created.data['data']['link_available'])
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/native-banner-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token, 'bossId': boss_account.pk,
                    'bannerId': banner_id, 'serviceId': service.pk, 'otherServiceId': other_service.pk, 'claimCouponId': coupon.pk,
                    'regularServiceId': regular_service.pk, 'regularOtherId': regular_other.pk, 'regularCouponId': regular_coupon.pk,
                    'generalCouponId': general_coupon.pk, 'rateCouponId': rate_coupon.pk, 'staffToken': staff_token}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(Order.objects.filter(customer_account=boss_account).count(), 3)
            self.assertEqual(Wallet.objects.get(user=boss).balance, 490000)
            self.assertEqual(Transaction.objects.filter(wallet__user=boss, tx_type='PAY').count(), 3)
            self.assertEqual(Transaction.objects.filter(wallet__user=boss, tx_type='REFUND').count(), 2)
            for token, reason in [(boss_token, 'QUALIFICATION_USED'), (other_token, 'QUALIFICATION_REQUIRED')]:
                client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                banner = next(row for row in client.get('/api/banners/').data['data'] if row['id'] == banner_id)
                self.assertFalse(banner['link_available'])
                self.assertEqual(banner['link_unavailable_reason'], reason)
                detail = client.get(f'/api/orders/services/{service.pk}/')
                self.assertNotEqual(detail.data['code'], 0, detail.data)

    def test_staff_disables_service_while_owner_home_remains_open(self):
        node = shutil.which('node')
        self.assertTrue(node)
        boss, staff = make_user(), make_console_user(['banner:view', 'banner:edit', 'service:view', 'service:edit', 'engagement:view', 'engagement:edit'])
        boss_account = get_or_create_account_for_legacy_user(boss)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        service_staff = make_console_user(['service:view', 'service:edit'])
        service_account = get_or_create_account_for_legacy_user(service_staff)
        service_staff.admin_membership.account = service_account
        service_staff.admin_membership.save(update_fields=['account'])
        boss_token, staff_token = issue_account_tokens(boss_account)[0], issue_account_tokens(staff_account)[0]
        service = make_service()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        gameplay = client.post('/api/admin/gameplays/', {'name': '隔离轮播玩法', 'code': 'banner-live-gameplay'}, format='json')
        self.assertEqual(gameplay.data['code'], 0, gameplay.data)
        content = client.post('/api/admin/club-content/', {'code': 'banner-live-content', 'kind': 'RIGHTS',
            'title': '隔离轮播权益', 'content': '权益正文', 'is_active': True}, format='json')
        self.assertEqual(content.data['code'], 0, content.data)
        image = io.BytesIO()
        Image.new('RGB', (750, 320), '#224466').save(image, format='PNG')
        with tempfile.TemporaryDirectory(prefix='xa-banner-live-') as media, override_settings(MEDIA_ROOT=media):
            response = client.post('/api/admin/banners/', {'title': '隔离首页轮播', 'is_active': 'true',
                'link_type': 'PRODUCT', 'link_value': str(service.pk),
                'image': SimpleUploadedFile('banner.png', image.getvalue(), content_type='image/png')}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/native-banner-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token, 'bossId': boss_account.pk,
                    'staffToken': staff_token, 'bannerId': response.data['data']['id'], 'serviceId': service.pk,
                    'replacementContentId': content.data['data']['id']}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            role = staff.admin_membership.roles.first()
            role.permissions = ['banner:view', 'banner:edit']
            role.save(update_fields=['permissions'])
            result = subprocess.run([node, str(repo / 'scripts/admin-banner-status-live.cjs')],
                input=json.dumps({'api': self.live_server_url, 'token': staff_token,
                    'serviceToken': issue_account_tokens(service_account)[0], 'serviceId': service.pk,
                    'gameplayName': '隔离轮播玩法', 'contentName': '隔离轮播权益'}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + boss_token)
            rows = client.get('/api/banners/').data['data']
            updated = next(row for row in rows if row['id'] == response.data['data']['id'])
            self.assertEqual((updated['link_type'], updated['link_value'], updated['link_available']),
                             ('CONTENT', str(content.data['data']['id']), True))
            result = subprocess.run([node, str(repo / 'scripts/native-banner-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token, 'bossId': boss_account.pk,
                    'bannerId': updated['id'], 'expectedUrl': f"/pages/club-content/index?id={content.data['data']['id']}",
                    'observeOnly': True}), cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_staff_disables_content_while_owner_home_remains_open(self):
        node = shutil.which('node')
        self.assertTrue(node)
        boss, staff = make_user(), make_console_user(['banner:view', 'banner:edit', 'engagement:view', 'engagement:edit'])
        boss_account = get_or_create_account_for_legacy_user(boss)
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        boss_token, staff_token = issue_account_tokens(boss_account)[0], issue_account_tokens(staff_account)[0]
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + staff_token)
        content = client.post('/api/admin/club-content/', {'code': 'live-banner-rights', 'kind': 'RIGHTS',
            'title': '隔离权益', 'content': '权益正文', 'is_active': True}, format='json')
        self.assertEqual(content.data['code'], 0, content.data)
        content_id = content.data['data']['id']
        image = io.BytesIO(); Image.new('RGB', (750, 320), '#224466').save(image, format='PNG')
        with tempfile.TemporaryDirectory(prefix='xa-banner-live-') as media, override_settings(MEDIA_ROOT=media):
            response = client.post('/api/admin/banners/', {'title': '隔离权益轮播', 'is_active': 'true',
                'link_type': 'CONTENT', 'link_value': str(content_id),
                'image': SimpleUploadedFile('banner.png', image.getvalue(), content_type='image/png')}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            repo = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run([node, str(repo / 'scripts/native-banner-live.cjs')],
                input=json.dumps({'base': self.live_server_url, 'bossToken': boss_token, 'bossId': boss_account.pk,
                    'staffToken': staff_token, 'bannerId': response.data['data']['id'],
                    'expectedUrl': f'/pages/club-content/index?id={content_id}',
                    'disablePath': f'/api/admin/club-content/{content_id}/'}),
                cwd=repo, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
