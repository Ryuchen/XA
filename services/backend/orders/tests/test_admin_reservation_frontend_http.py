"""Run the admin reservation page controller against actual isolated APIs."""
import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False)
class AdminReservationFrontendHTTPTest(LiveServerTestCase):
    def test_staff_page_creates_and_converts_for_the_selected_boss(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        boss, provider = make_user(), make_provider()
        staff = make_console_user(['order:view', 'order:dispatch'])
        viewer = make_console_user(['order:view'])
        canceller = make_console_user(['order:view', 'order:cancel'])
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        staff_account = get_or_create_account_for_legacy_user(staff)
        viewer_account = get_or_create_account_for_legacy_user(viewer)
        cancel_account = get_or_create_account_for_legacy_user(canceller)
        for user, account in ((boss, boss_account), (provider, provider_account), (staff, staff_account),
                              (viewer, viewer_account), (canceller, cancel_account)):
            link_legacy_relations(user, account)
        for user, account in ((staff, staff_account), (viewer, viewer_account), (canceller, cancel_account)):
            user.admin_membership.account = account
            user.admin_membership.save(update_fields=['account'])
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(boss, balance=50000)
        wallet.account = boss_account
        wallet.save(update_fields=['account'])
        service = make_service(price=10000)
        start = (timezone.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run([node, str(repo / 'scripts/admin-reservation-live.cjs')],
            input=json.dumps({'base': self.live_server_url, 'staffToken': issue_account_tokens(staff_account)[0],
                'providerToken': issue_account_tokens(provider_account)[0], 'bossToken': issue_account_tokens(boss_account)[0], 'staffId': staff_account.pk,
                'viewerToken': issue_account_tokens(viewer_account)[0], 'cancelToken': issue_account_tokens(cancel_account)[0],
                'bossId': boss_account.pk, 'providerId': provider_account.pk, 'serviceId': service.pk,
                'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat(),
                'secondStart': (start + timedelta(hours=2)).isoformat(),
                'secondEnd': (start + timedelta(hours=3)).isoformat(),
                'thirdStart': (start + timedelta(hours=4)).isoformat(),
                'thirdEnd': (start + timedelta(hours=5)).isoformat(),
                'fourthStart': (start + timedelta(hours=6)).isoformat(),
                'fourthEnd': (start + timedelta(hours=7)).isoformat()}),
            text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=repo,
            env={**os.environ, 'TZ': 'America/New_York'})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        order = Order.objects.get(customer_account=boss_account)
        self.assertEqual((order.status, order.provider_account_id, order.amount), ('GRABBED', provider_account.pk, 10000))
        self.assertEqual(Wallet.objects.get(user=boss).balance, 40000)
