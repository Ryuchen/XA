"""Boss page and real isolated API agree about a points cancellation."""
import json
import os
from datetime import timedelta
from pathlib import Path
import subprocess
from unittest import skipUnless

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from club_accounts.tokens import issue_account_tokens
from engagement.models import MemberProfile, PointLedger
from orders.models import Order, OrderPointsRefund, OrderRefund
from orders.tests import test_points_service_refund as refund_fixture
from wallet.models import Transaction, Wallet
from wallet.test_deposit_realtime_workflow import single_asgi


@skipUnless(os.environ.get('XA_SERVICE_POINTS_CANCEL_BROWSER') == '1', 'Requires isolated ASGI and boss frontend')
@override_settings(SERVICE_ORDER_POINTS_RECEIPT_ENABLED=True, POINTS_USAGE_SWITCHES_ENABLED=True,
                   SERVICE_ORDER_POINTS_CANCEL_TESTING=True, LEGACY_FORCE_AUTH_COMPAT=False)
class PointsServiceCancelBrowserTest(LiveServerTestCase):
    def test_owner_sees_and_recovers_full_original_assets(self):
        refund_fixture.ServicePointsRefundTests.setUp(self)
        Order.objects.filter(pk=self.order.pk).update(auto_cancel_at=timezone.now() + timedelta(minutes=10))
        with single_asgi() as (api, _):
            root = Path(settings.BASE_DIR).parent.parent
            result = subprocess.run(['node', str(root / 'scripts/points-service-cancel-browser.cjs')], cwd=root,
                input=json.dumps({'api':api, 'order':self.order.pk,
                    'boss':{'id':self.account.pk, 'token':issue_account_tokens(self.account)[0]}}),
                text=True, encoding='utf-8', capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('CANCELLED', 'REFUNDED'))
        self.assertEqual(Wallet.objects.get(account=self.account).balance, 2000)
        self.assertEqual(MemberProfile.objects.get(account=self.account).points_balance, 15000)
        self.assertEqual(OrderRefund.objects.filter(order=self.order).count(), 1)
        self.assertEqual(OrderPointsRefund.objects.filter(refund__order=self.order).count(), 1)
        self.assertEqual(Transaction.objects.filter(order=self.order, tx_type='REFUND').count(), 1)
        self.assertEqual(PointLedger.objects.filter(order=self.order, kind='ORDER_POINTS_RETURN').count(), 1)
