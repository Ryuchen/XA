"""Exercise the real admin recharge and gift approval screens on isolated data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import RechargePackage
from orders.tests.factories import make_console_user
from wallet.models import RechargeRecord, Wallet
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_RECHARGE_PRICING_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class RechargePricingBrowserTest(LiveServerTestCase):
    def test_staff_recharge_and_manager_approval(self):
        staff = make_console_user(['wallet:view', 'wallet:recharge', 'recharge:view', 'recharge:gift_approve'])
        actor = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = actor; staff.admin_membership.save(update_fields=['account'])
        boss = ClubAccount.objects.create_account(username='synthetic-recharge-browser', account_type='BOSS')
        get_wallet(account=boss)
        RechargePackage.objects.create(amount=300000, gift_amount=10000, active=True)
        RechargePackage.objects.create(amount=600000, gift_amount=25000, active=True)
        with tempfile.TemporaryDirectory(prefix='xa-recharge-media-') as media:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
            env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.recharge_browser_settings', 'XA_RECHARGE_TEST_MEDIA': media}
            process = subprocess.Popen([sys.executable, '-m', 'daphne', '-b', '127.0.0.1', '-p', str(port), 'config.asgi:application'],
                cwd=settings.BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError('isolated ASGI exited')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.2): break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('isolated ASGI did not start')
                root = Path(settings.BASE_DIR).parent.parent
                result = subprocess.run(['node', str(root / 'scripts/recharge-pricing-browser.cjs')], cwd=root,
                    input=json.dumps({'api': f'http://127.0.0.1:{port}', 'token': issue_account_tokens(actor)[0],
                        'id': actor.pk, 'permissions': ['wallet:view', 'wallet:recharge', 'recharge:view', 'recharge:gift_approve']}),
                    text=True, encoding='utf-8', capture_output=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            finally:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
        record = RechargeRecord.objects.get(account=boss)
        self.assertEqual((record.amount, record.gift_amount, record.gift_approval.status), (900000, 40000, 'APPROVED'))
        self.assertEqual(Wallet.objects.get(account=boss).balance, 940000)
