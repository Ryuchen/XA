import json
import base64
import io
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from PIL import Image

from announcements.models import Announcement
from audition.models import AuditionLink
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_user, make_service, make_provider, make_console_user
from site_messages.models import Message
from wallet.services import get_wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, WECHAT_MOCK_LOGIN=False, ORDER_TRAFFIC_ENABLED=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   WECHAT_MINIAPP_APPID='test-appid', WECHAT_MINIAPP_SECRET='test-secret')
class NativeFrontendHTTPTest(LiveServerTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))

    def test_native_page_controllers_use_real_backend_contracts(self):
        self.run_native_flow(False)

    def test_native_self_order_direct_grab_uses_real_backend_contracts(self):
        self.run_native_flow(True)

    def run_native_flow(self, auto_grab):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime is required for native frontend HTTP verification')
        user = make_user(openid='native-http-wechat', is_openid_bound=True)
        account = get_or_create_account_for_legacy_user(user)
        wallet = get_wallet(account=account, user=user)
        wallet.balance = 10000
        wallet.save(update_fields=['balance'])
        service = make_service(price=1250, commission_rate=20)
        provider = make_provider(pass_tier='BLACK', pass_expires_at=timezone.now() + timedelta(days=1))
        provider_account = get_or_create_account_for_legacy_user(provider)
        profile = provider.escort_profile
        profile.account = provider_account
        profile.save(update_fields=['account'])
        admin = make_console_user(['report:audit', 'order:view'])
        admin_account = get_or_create_account_for_legacy_user(admin)
        membership = admin.admin_membership
        membership.account = admin_account
        membership.save(update_fields=['account'])
        provider_token, _ = issue_account_tokens(provider_account)
        admin_token, _ = issue_account_tokens(admin_account)
        evidence = io.BytesIO()
        Image.new('RGB', (4, 4), 'white').save(evidence, format='PNG')
        announcement = Announcement.objects.create(title='联动公告', content='本次活动的真实接口内容')
        message = Message.objects.create(recipient=user, recipient_account=account, title='服务消息',
                                         action_url=f'/pages/productDetail/index?id={service.pk}')
        audition_user = make_user()
        audition_account = get_or_create_account_for_legacy_user(audition_user)
        audition = AuditionLink.objects.create(title='原生试音活动', boss_user=audition_user, boss_account=audition_account)
        token, refresh = issue_account_tokens(account)
        repo = Path(settings.BASE_DIR).parent.parent
        payload = {
            'base': self.live_server_url,
            'autoGrab': auto_grab,
            # Django shares a single SQLite in-memory connection across live
            # server threads. Serialize transport in that test-only setting;
            # MySQL runs and the production wx layer retain parallel requests.
            'serializeRequests': connection.vendor == 'sqlite' and connection.is_in_memory_db(),
            'serviceId': service.pk,
            'providerToken': provider_token, 'adminToken': admin_token,
            'evidence': base64.b64encode(evidence.getvalue()).decode(),
            'announcementId': announcement.pk,
            'messageId': message.pk,
            'auditionToken': audition.boss_token,
            'auditionAccountId': str(audition_account.pk),
            'session': {'token': token, 'refresh': refresh,
                        'user': {'id': str(account.id), 'role': 'customer', 'nickname': '联动验收'}},
        }
        with patch('orders.views._schedule_auto_cancel'), patch('orders.views.enqueue_kook_dispatch'), \
                patch('orders.views.enqueue_wecom_dispatch'), patch('orders.views.notify_order_update'), \
                patch('users.views._wechat_code2session', return_value='native-http-wechat'):
            result = subprocess.run(
                [node, str(repo / 'scripts' / 'native-live-driver.cjs')],
                input=json.dumps(payload), text=True, capture_output=True,
                encoding='utf-8', timeout=90, cwd=repo,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('real Django HTTP', result.stdout)
        from engagement.confirmation import process_order
        from orders.models import Order
        from wallet.models import Wallet
        with patch('django.utils.timezone.now', return_value=timezone.now()+timedelta(hours=73)):
            for pk in Order.objects.filter(status='COMPLETED',payment_status='PAID').values_list('pk',flat=True):
                process_order(pk)
        self.assertEqual(Wallet.objects.get(user=provider).balance,1000)
