"""Exercise the real scheduled retry against an isolated Redis and database."""
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import skipUnless

import redis
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.benefits import grant_periodic
from coupons.models import UserCoupon
from coupons.test_coupon_availability_workflow import CouponAvailabilityWorkflowTest
from engagement.models import VipTier
from orders.models import Order, OrderPlatformSubsidyShare
from orders.tests.factories import make_boss_type, make_console_user, make_provider, make_service
from orders.tests.test_release_flow import screenshot
from wallet.models import Transaction, Wallet, get_platform_wallet


@skipUnless(os.environ.get('XA_SUBSIDY_BEAT_TEST') == '1', 'Requires isolated Redis and file database')
@override_settings(VIP_SAFE_BOX_SUBSIDY_ENABLED=True, ORDER_SETTLEMENT_MODE='COMPLETION',
                   REQUIRE_ORDER_EVIDENCE_IMAGES=True, LEGACY_FORCE_AUTH_COMPAT=False,
                   KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class SubsidyBeatNetworkTest(APITransactionTestCase):
    setUp = CouponAvailabilityWorkflowTest.setUp
    client_for = staticmethod(CouponAvailabilityWorkflowTest.client_for)
    ok = CouponAvailabilityWorkflowTest.ok

    def test_scheduled_worker_retries_after_platform_topup(self):
        self._scheduled_retry(terminated=False)

    def test_scheduled_worker_pays_negotiated_termination_after_topup(self):
        self._scheduled_retry(terminated=True)

    @override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
    def test_approved_report_waits_for_funds_then_scheduled_worker_settles(self):
        self._scheduled_retry(terminated=False, report_approval=True)

    def _scheduled_retry(self, *, terminated, report_approval=False):
        artifacts = TemporaryDirectory(prefix='xa-subsidy-beat-')
        self.addCleanup(artifacts.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=artifacts.name))
        self.assertEqual(connection.settings_dict['NAME'], os.environ.get('XA_ASGI_NETWORK_TEST_DB'))
        service = make_service(name='定时补贴安全箱', price=10000, benefit_kind='SAFEBOX_33',
                               platform='PC', commission_rate=20)
        tier = make_boss_type(name='定时补贴会员', discount_rate=90)
        VipTier.objects.create(boss_type=tier, level=6, recharge_threshold=0, safe_box_service_ids=[service.pk])
        self.account.boss_type = tier
        self.account.save(update_fields=['boss_type'])
        grant_periodic(self.account)
        voucher = UserCoupon.objects.get(account=self.account, coupon__scope='SAFE_BOX')
        staff = make_console_user(['order:view', 'order:dispatch', 'order:refund', 'report:view', 'report:audit', 'wallet:adjust'])
        staff_account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='定时补贴陪玩')
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        provider.escort_profile.service_items.add(service)
        order = self.ok(self.boss.post('/api/orders/create/', {
            'service_id': service.pk, 'user_coupon_id': voucher.pk,
            'dispatch_route': 'STAFF', 'client_request_id': 'vip-beat-retry',
        }, format='json'))
        self.ok(self.client_for(staff_account).post(f"/api/admin/orders/{order['id']}/assign/",
            {'provider_id': account.pk}, format='json'))
        player = self.client_for(account)
        self.ok(player.post(f"/api/orders/orders/{order['id']}/start/", {'entry_image': screenshot()}, format='multipart'))
        platform = get_platform_wallet()
        platform.balance = 7999
        platform.save(update_fields=['balance'])
        if terminated:
            operator = self.client_for(staff_account)
            base = f"/api/admin/orders/{order['id']}/"
            share = self.ok(operator.get(base + 'subsidy-negotiation-options/'))['shares'][0]
            allocations = [{'share_id': share['share_id'], 'amount': 8000}]
            plan = self.ok(operator.post(base + 'subsidy-negotiation-preview/', {'allocations': allocations}, format='json'))
            self.ok(operator.post(base + 'subsidy-negotiation-confirm/', {'allocations': allocations,
                'plan_token': plan['plan_token'], 'client_request_id': 'vip-beat-termination',
                'reason': '双方协商终止', 'reviewed': True}, format='json'))
        else:
            self.ok(player.post(f"/api/orders/orders/{order['id']}/complete/", {'completion_image': screenshot()}, format='multipart'))
        if report_approval:
            from engagement.confirmation import process_order, refresh_report_clock
            from wallet.models import ProviderReport
            report = ProviderReport.objects.get(order_id=order['id'], provider_account=account)
            self.ok(player.post(f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, format='multipart'))
            self.ok(player.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json'))
            self.ok(self.client_for(staff_account).post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json'))
            old = timezone.now() - timedelta(hours=73)
            Order.objects.filter(pk=order['id']).update(completed_at=old)
            ProviderReport.objects.filter(pk=report.pk).update(submitted_at=old)
            refresh_report_clock(Order.objects.get(pk=order['id']))
            self.assertFalse(process_order(order['id']))
        current = Order.objects.get(pk=order['id'])
        self.assertEqual(current.status, 'CANCELLED' if terminated else 'COMPLETED')
        self.assertIsNone(current.settled_at)
        if terminated:
            self.assertFalse(UserCoupon.objects.filter(account=self.account, coupon__scope='SAFE_BOX', status='UNUSED').exists())

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        password, token = secrets.token_hex(16), secrets.token_hex(10)
        name = 'xa-subsidy-beat-' + token
        docker = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--', 'docker']
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.subsidy_beat_test_settings',
               'XA_RESERVATION_TEST_BROKER': f'redis://:{password}@127.0.0.1:{port}/0'}
        # Celery reads these environment variables ahead of Django settings.
        # Do not let the parent test process's in-memory transport override the
        # password-protected Redis shared by the two child processes.
        env.pop('CELERY_BROKER_URL', None)
        env.pop('CELERY_RESULT_BACKEND', None)
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        processes = []
        logs = []
        broker = redis.Redis(host='127.0.0.1', port=port, password=password, socket_timeout=.2)
        try:
            subprocess.run(docker + ['run', '-d', '--rm', '--name', name, '-p', f'127.0.0.1:{port}:6379',
                'redis:alpine', 'redis-server', '--save', '', '--appendonly', 'no', '--requirepass', password],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            for _ in range(150):
                try:
                    if broker.ping():
                        break
                except redis.RedisError:
                    time.sleep(.1)
            else:
                self.fail('isolated Redis did not start')
            for index, command in enumerate(([sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'INFO'],
                            [sys.executable, '-m', 'celery', '-A', 'config', 'beat', '-l', 'INFO',
                             '--schedule', str(Path(artifacts.name) / ('schedule-' + token))])):
                log = open(Path(artifacts.name) / f'celery-{index}.log', 'w+', encoding='utf-8')
                logs.append(log)
                processes.append(subprocess.Popen(command, cwd=settings.BASE_DIR, env=env,
                    stdout=log, stderr=subprocess.STDOUT, creationflags=flags))
            if report_approval:
                self.ok(self.client_for(staff_account).post('/api/admin/wallets/platform-topup/', {
                    'amount': 2001, 'reference': 'report-beat-offline-001',
                    'client_request_id': 'report-beat-offline-001'}, format='json'))
            else:
                platform.balance = 10000
                platform.save(update_fields=['balance'])
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    self.fail('isolated worker or beat exited early')
                current.refresh_from_db()
                if current.settled_at:
                    break
                time.sleep(.25)
            else:
                for log in logs:
                    log.flush()
                    log.seek(0)
                self.fail('scheduled retry did not settle the order\n' + '\n'.join(log.read() for log in logs))
            platform.refresh_from_db()
            self.assertEqual(platform.balance, 2000)
            self.assertEqual(Wallet.objects.get(account=account).balance, 8000)
            self.assertIsNotNone(OrderPlatformSubsidyShare.objects.get(subsidy__order=current).paid_at)
            self.assertEqual(Transaction.objects.filter(order=current, tx_type='SUBSIDY_EXPENSE').count(), 1)
            if report_approval:
                self.assertIsNotNone(current.consumption_confirmed_at)
            if terminated:
                voucher.refresh_from_db()
                self.assertEqual(voucher.status, 'USED')
                self.assertEqual(current.payment_status, 'REFUNDED')
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not UserCoupon.objects.filter(
                    account=self.account, coupon__scope='SAFE_BOX', status='UNUSED'
                ).exists():
                    time.sleep(0.1)
                self.assertEqual(UserCoupon.objects.filter(
                    account=self.account, coupon__scope='SAFE_BOX', status='UNUSED'
                ).count(), 1)
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            for log in logs:
                log.close()
            try:
                broker.shutdown(nosave=True)
            except redis.RedisError:
                pass
            broker.close()
            subprocess.run(docker + ['stop', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, creationflags=flags)


del CouponAvailabilityWorkflowTest
