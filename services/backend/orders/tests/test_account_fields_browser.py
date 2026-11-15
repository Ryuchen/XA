"""Real admin settings and boss checkout against a private test ASGI."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_ACCOUNT_FIELDS_BROWSER') == '1', 'Requires isolated ASGI and local Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class AccountFieldsBrowserTest(LiveServerTestCase):
    def test_admin_sets_service_rule_and_boss_places_order(self):
        boss = make_user(); boss_account = get_or_create_account_for_legacy_user(boss)
        wallet = get_wallet(user=boss, account=boss_account); wallet.balance = 3000; wallet.save(update_fields=['balance'])
        provider = make_provider(deposit_mode='CASH', deposit_paid=40000)
        provider_account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = provider_account; provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='隔离资料配置服务', price=1000)
        perms = ['dictionary:view', 'dictionary:edit', 'order:view', 'order:dispatch']
        staff = make_console_user(perms); account = get_or_create_account_for_legacy_user(staff)
        link_legacy_relations(staff, account)
        staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/order-account-fields-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'service': service.pk, 'provider': provider_account.pk,
                    'staff': {'id': account.pk, 'token': issue_account_tokens(account)[0], 'permissions': perms},
                    'boss': {'id': boss_account.pk, 'token': issue_account_tokens(boss_account)[0]}}),
                text=True, encoding='utf-8', capture_output=True, timeout=90)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        order = Order.objects.get(customer_account=boss_account)
        self.assertEqual((order.game_region, order.game_nickname, order.game_uid), ('', '', 'browser-uid'))
