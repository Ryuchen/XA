"""Boss gift page uses the real per-store principal balance from an isolated ASGI."""
import json
import os
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from engagement.models import PointFundingLot, PointLedger
from engagement.services import config
from orders.models import Order, ServiceCategory
from orders.tests.factories import make_user, make_provider, make_service
from wallet.services import get_wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_GIFT_PRINCIPAL_BROWSER') == '1', 'Requires isolated ASGI and boss Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class GiftPrincipalBrowserTest(LiveServerTestCase):
    def test_boss_sees_principal_and_only_eligible_order_is_charged(self):
        operation = config(); operation.base_points_rate = 150
        operation.save(update_fields=['base_points_rate'])
        provider = make_provider(is_verified=True)
        recipient = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = recipient; provider.escort_profile.save(update_fields=['account'])
        category = ServiceCategory.objects.create(name='隔离本金礼物分类', is_gift=True)
        service = make_service(name='隔离本金礼物', price=2100, service_category=category)
        identities = []
        for index, principal in enumerate((1000, 3000)):
            user = make_user(username=f'synthetic-gift-principal-{index}')
            account = get_or_create_account_for_legacy_user(user)
            wallet = get_wallet(account=account, user=user)
            wallet.balance = principal + 5000; wallet.save(update_fields=['balance'])
            PointFundingLot.objects.create(account=account, source_key=f'gift-only-{index}', kind='GIFT', remaining=5000)
            PointFundingLot.objects.create(account=account, source_key=f'prepaid-only-{index}', kind='PREPAID', remaining=principal)
            identities.append({'token': issue_account_tokens(account)[0], 'id': account.pk})
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            process = subprocess.run(['node', str(root / 'scripts/gift-principal-browser.cjs')], cwd=root,
                input=json.dumps({'api': api, 'recipient': recipient.pk, 'service': service.pk,
                                  'insufficient': identities[0], 'sufficient': identities[1]}),
                text=True, encoding='utf-8', capture_output=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertFalse(Order.objects.filter(customer_account_id=identities[0]['id'], is_gift_snapshot=True).exists())
        self.assertEqual(Order.objects.filter(customer_account_id=identities[1]['id'], is_gift_snapshot=True).count(), 1)
        order = Order.objects.get(customer_account_id=identities[1]['id'], is_gift_snapshot=True)
        self.assertEqual(order.gift_points_snapshot['points_rate'], 150)
        self.assertEqual(PointLedger.objects.get(order=order, kind='CONSUMPTION').amount, 3150)
        self.assertEqual(PointFundingLot.objects.get(source_key='gift-only-1').remaining, 5000)
        self.assertEqual(PointFundingLot.objects.get(source_key='prepaid-only-1').remaining, 900)
