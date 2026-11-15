"""Optional real Redis, worker and production beat schedule on a disposable DB."""
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

import redis
from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.models import Order, OrderProvider, OrderRoomCompletionRequest, OrderRoomConfirmation, OrderStatusLog
from orders.state_machine import transition
from orders.tests.factories import make_console_user, make_order, make_provider, make_service, make_user
from wallet.models import Transaction


@skipUnless(os.environ.get('XA_ROOM_BEAT_TEST') == '1', 'Requires isolated Redis and real worker/beat opt-in')
class RoomBeatNetworkTest(TransactionTestCase):
    def test_real_beat_honors_late_objection_then_resumes_auto_confirmation(self):
        database = os.environ.get('XA_ASGI_NETWORK_TEST_DB')
        if not database or connection.settings_dict['NAME'] != database:
            self.fail('Expected the explicitly isolated file-backed SQLite database')
        boss, player = make_user(), make_provider(deposit_mode='EARNINGS')
        boss_account, player_account = [get_or_create_account_for_legacy_user(u) for u in (boss, player)]
        admin = make_console_user(['order:view', 'support:edit'])
        staff_account = get_or_create_account_for_legacy_user(admin)
        admin.admin_membership.account = staff_account; admin.admin_membership.save(update_fields=['account'])
        service = make_service(room_enabled=True)
        waits = []
        for _ in range(2):
            order = make_order(boss, service=service, customer_account=boss_account,
                               payment_status='PAID', settlement_mode='REPORT_APPROVAL')
            def assign(current):
                current.provider, current.provider_account = player, player_account
                OrderProvider.objects.create(order=current, provider=player, provider_account=player_account)
            transition(order.pk, Order.Status.GRABBED, side_effect=assign, update_fields=['provider', 'provider_account'])
            transition(order.pk, Order.Status.IN_SERVICE)
            request = OrderRoomCompletionRequest.objects.create(room=order.room, submitted_by=player_account,
                request_id='request', description='已完成', participants=[player_account.pk], status='VERIFIED',
                reviewed_by=staff_account, reviewed_at=timezone.now(), review_note='已核实', review_request_id='review')
            wait = OrderRoomConfirmation.objects.create(request=request, config_minutes=30, config_version=1,
                started_at=timezone.now()-timedelta(minutes=31), deadline=timezone.now()-timedelta(minutes=1))
            waits.append((order.pk, wait.pk))
        boss_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(boss_account)[0])
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer '+issue_account_tokens(staff_account)[0])
        objection = boss_client.post(f'/api/orders/orders/{waits[1][0]}/room/objections/',
            {'reason': '超时后仍有异议', 'client_request_id': 'late'}, format='json')
        self.assertEqual(objection.status_code, 200, objection.data)

        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        password, token = secrets.token_hex(16), secrets.token_hex(10)
        container_name = 'xa-room-beat-' + token
        docker = ['wsl.exe', '-d', 'XA-Ubuntu-24.04', '-u', 'root', '--', 'docker']
        broker = f'redis://:{password}@127.0.0.1:{port}/0'
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.room_beat_test_settings',
               'XA_RESERVATION_TEST_BROKER': broker, 'KOOK_ENABLED': 'false', 'WECOM_DISPATCH_ENABLED': 'false',
               'WECHAT_APPID': '', 'WECHAT_SECRET': ''}
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        processes = []
        container_started = False
        redis_client = redis.Redis(host='127.0.0.1', port=port, password=password, socket_timeout=.2)
        with TemporaryDirectory(prefix='xa-room-beat-') as scratch:
            try:
                subprocess.run(docker + ['run', '-d', '--rm', '--pull', 'never', '--name', container_name,
                    '-p', f'127.0.0.1:{port}:6379', 'redis:alpine', 'redis-server', '--save', '',
                    '--appendonly', 'no', '--requirepass', password], check=True, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, creationflags=flags)
                container_started = True
                for _ in range(150):
                    try:
                        if redis_client.ping():
                            break
                    except redis.RedisError:
                        time.sleep(.1)
                else:
                    self.fail('isolated Redis did not start')
                commands = [
                    [sys.executable, '-m', 'celery', '-A', 'config', 'worker', '-P', 'solo', '-l', 'WARNING'],
                    [sys.executable, '-m', 'celery', '-A', 'config', 'beat', '-l', 'WARNING',
                     '--schedule', str(Path(scratch)/'beat')],
                ]
                for command in commands:
                    processes.append(subprocess.Popen(command, cwd=settings.BASE_DIR, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags))
                def await_status(pk, expected):
                    deadline = time.monotonic()+70
                    while time.monotonic() < deadline:
                        if any(process.poll() is not None for process in processes):
                            self.fail('isolated worker or beat exited early')
                        if OrderRoomConfirmation.objects.get(pk=pk).status == expected:
                            return
                        time.sleep(.25)
                    self.fail(f'beat did not move room {pk} to {expected}')
                await_status(waits[0][1], 'AUTO')
                self.assertEqual(OrderRoomConfirmation.objects.get(pk=waits[1][1]).status, 'PAUSED')
                resolved = staff_client.post(f"/api/admin/room-objections/{objection.data['data']['id']}/resolve/",
                    {'resolution': '核实仍可结单', 'client_request_id': 'resolve', 'confirmation_ready': True}, format='json')
                self.assertEqual(resolved.status_code, 200, resolved.data)
                await_status(waits[1][1], 'AUTO')
                self.assertEqual(OrderStatusLog.objects.filter(order_id__in=[x[0] for x in waits], to_status='COMPLETED').count(), 2)
                self.assertFalse(Transaction.objects.filter(order_id__in=[x[0] for x in waits], tx_type='INCOME').exists())
                from site_messages.models import Message
                self.assertEqual(Message.objects.filter(recipient_account=boss_account, title='房间订单已自动完成').count(), 2)
                self.assertEqual(Message.objects.filter(recipient_account=player_account, title='房间订单已完成').count(), 2)
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=10)
                if container_started:
                    try:
                        redis_client.shutdown(nosave=True)
                    except redis.RedisError:
                        pass
                redis_client.close()
                if container_started:
                    subprocess.run(docker + ['stop', container_name], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=flags)
