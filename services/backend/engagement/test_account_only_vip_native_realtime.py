"""Open native member pages follow real order maturation over WebSocket."""
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberTag, VipTier
from orders.models import Order
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from users.test_native_checkin_order_realtime import isolated_redis_asgi
from wallet.models import ProviderReport


@skipUnless(os.environ.get('XA_VIP_NATIVE_REALTIME') == '1', 'Requires isolated Redis and ASGI')
@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class AccountOnlyVipNativeRealtimeTest(LiveServerTestCase):
    def test_native_empty_tier_configuration(self):
        boss = ClubAccount.objects.create_account(username='native-vip-empty', account_type='BOSS')
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / 'scripts/native-membership-empty-real.cjs')], cwd=repo,
            input=json.dumps({'base': self.live_server_url, 'token': issue_account_tokens(boss)[0], 'account': boss.pk}),
            text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_open_pages_follow_confirmed_order_without_losing_edits(self):
        media = TemporaryDirectory(prefix='xa-vip-live-'); self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = ClubAccount.objects.create_account(username='native-vip-confirm', account_type='BOSS')
        staff = make_console_user(['wallet:recharge', 'order:view', 'report:view', 'report:audit', 'engagement:view', 'engagement:edit'])
        admin = get_or_create_account_for_legacy_user(staff); staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(); player = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = player; provider.escort_profile.save(update_fields=['account'])
        service = make_service(price=10000, commission_rate=20); provider.escort_profile.service_items.add(service)
        tier = make_boss_type(name='实时成长会员', discount_rate=90)
        vip = VipTier.objects.create(level=6, boss_type=tier, recharge_threshold=10000)
        MemberTag.objects.create(account=boss, event_key='native-vip-tag', title='体验标签')
        def client(account):
            result = APIClient(); result.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); return result
        def ok(response):
            self.assertEqual(response.status_code, 200, response.data); self.assertEqual(response.data['code'], 0, response.data); return response.data.get('data')
        owner, operator, worker = client(boss), client(admin), client(player)
        ok(operator.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 20000, 'gift_amount': 0,
            'trade_no': 'native-vip-receipt', 'client_request_id': 'native-vip-recharge', 'proof_image': screenshot()}, format='multipart'))
        order = ok(owner.post('/api/orders/create/', {'service_id': service.pk, 'provider_id': player.pk,
            'client_request_id': 'native-vip-order'}, format='json'))
        for action, field in [('start', 'entry_image'), ('complete', 'completion_image')]:
            ok(worker.post(f"/api/orders/orders/{order['id']}/{action}/", {field: screenshot()}, format='multipart'))
        report = ProviderReport.objects.get(order_id=order['id'])
        ok(worker.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
        ok(worker.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
        ok(operator.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
        old = timezone.now() - timedelta(hours=73)
        Order.objects.filter(pk=order['id']).update(completed_at=old)
        ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
        repo = Path(settings.BASE_DIR).parent.parent
        with isolated_redis_asgi(include_env=True) as (base, ws_base, env):
            env.update(CELERY_BROKER_URL='memory://', CELERY_RESULT_BACKEND='cache+memory://')
            script = 'native-vip-membership-ide.cjs' if os.environ.get('XA_WECHAT_VIP_REFRESH') == '1' else 'native-vip-confirmation-realtime.cjs'
            result = subprocess.run(['node', str(repo / 'scripts' / script)], cwd=repo, env=env,
                input=json.dumps({'base': base, 'wsBase': ws_base, 'token': issue_account_tokens(boss)[0],
                    'account': boss.pk, 'orderId': order['id'], 'serviceId': service.pk, 'python': sys.executable, 'backend': str(settings.BASE_DIR),
                    'staffToken': issue_account_tokens(admin)[0], 'tierId': vip.pk,
                    'screenshot': str(repo / '.tools/vip-membership-pending-iphone12-20261103.png')}),
                text=True, encoding='utf-8', capture_output=os.environ.get('XA_WECHAT_PHYSICAL_TAP') != '1', timeout=80)
            self.assertEqual(result.returncode, 0, (result.stdout or '') + (result.stderr or ''))
            if os.environ.get('XA_WECHAT_VIP_REFRESH') == '1' and result.stdout:
                print(result.stdout)
