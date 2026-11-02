"""Actual order pool remains usable while the provider controls its sound."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_provider, make_service, make_user, make_wallet
from users.models import EscortProfile
from wallet.models import SystemConfig, Wallet


@skipUnless(os.environ.get('XA_PROVIDER_SOUND_BROWSER') == '1', 'Requires isolated H5 build and database')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False, CELERY_TASK_ALWAYS_EAGER=False)
class ProviderOrderSoundBrowserTest(LiveServerTestCase):
    def test_mute_and_enable_with_actual_paid_orders(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider, other_provider = make_user(), make_provider(), make_provider()
        boss_account, provider_account = [get_or_create_account_for_legacy_user(user) for user in (boss, provider)]
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        EscortProfile.objects.filter(user=provider).update(account=provider_account)
        other_account = get_or_create_account_for_legacy_user(other_provider)
        link_legacy_relations(other_provider, other_account)
        EscortProfile.objects.filter(user=other_provider).update(account=other_account)
        make_wallet(boss, balance=50000)
        service = make_service(price=10000)
        SystemConfig.objects.update_or_create(key='provider_pass_delay_seconds_NONE', defaults={'value': '0'})
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/provider-order-sound-375.cjs')],
            input=json.dumps({'base': self.live_server_url, 'bossToken': issue_account_tokens(boss_account)[0],
                'providerToken': issue_account_tokens(provider_account)[0], 'providerId': str(provider_account.pk),
                'otherProviderToken': issue_account_tokens(other_account)[0],
                'serviceId': service.pk}), text=True, encoding='utf-8', capture_output=True, timeout=90, cwd=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Order.objects.filter(customer_account=boss_account, payment_status='PAID').count(), 2)
        self.assertEqual(Order.objects.get(customer_account=boss_account, reject_count=1).status, 'PENDING')
        self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
