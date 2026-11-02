"""Show a real, upgraded VIP claim in the 375px native miniapp."""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from engagement.models import RewardClaim
from engagement.test_vip_claim_queue_realtime import VipClaimQueueRealtimeTest
from orders.tests import test_release_flow as flow


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL', REQUIRE_ORDER_EVIDENCE_IMAGES=True,
    REQUIRE_RECHARGE_PROOF=True, LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
    CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class VipTermsNativeTest(LiveServerTestCase):
    host, port = '127.0.0.1', 19210
    login = flow.ReleaseFlowTest.login
    ok = flow.ReleaseFlowTest.ok
    recharge = flow.ReleaseFlowTest.recharge
    new_order = flow.ReleaseFlowTest.new_order
    finish = flow.ReleaseFlowTest.finish
    settle_after_window = flow.ReleaseFlowTest.settle_after_window

    def setUp(self):
        self.client = APIClient()
        flow.ReleaseFlowTest.setUp(self)

    def test_real_upgrade_terms_in_native_detail(self):
        VipClaimQueueRealtimeTest.test_upgrade_updates_claim_terms_and_requires_operator_recheck(self)
        if os.environ.get('XA_NATIVE_VIP_TERMS') != '1':
            return
        claim = RewardClaim.objects.filter(account=self.accounts[self.boss.pk], name_snapshot='会员冠名').order_by('id').first()
        repo = Path(settings.BASE_DIR).parent.parent
        result = subprocess.run(['node', str(repo / '.tools/native-recharge-checkout-375-20261102/native-vip-terms-375.cjs')],
            cwd=repo, input=json.dumps({'id': claim.pk, 'session': {'token': issue_account_tokens(self.accounts[self.boss.pk])[0],
                'user': {'id': str(self.accounts[self.boss.pk].pk), 'role': 'customer'}}}),
            text=True, encoding='utf-8', capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
