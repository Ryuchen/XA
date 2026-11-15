from datetime import date
from io import StringIO

from django.core.management import call_command, CommandError
from django.test import TestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from coupons.benefits import issue
from coupons.management.commands.audit_vip_monthly_evidence import audit_monthly
from coupons.models import Coupon, UserCoupon, VipMonthlyCouponGrant
from engagement.models import VipTier
from orders.tests.factories import make_boss_type, make_user
from wallet.models import Transaction, Wallet


class MonthlyCouponAuditTest(TestCase):
    def test_audit_reports_evidence_without_fabricating_a_zero_grant(self):
        accounts = [get_or_create_account_for_legacy_user(make_user()) for _ in range(2)]
        tier = VipTier.objects.create(level=2, boss_type=make_boss_type(), recharge_threshold=100,
                                      monthly_coupon_count=2, monthly_discount=90)
        for account in accounts:
            account.boss_type = tier.boss_type
            account.save(update_fields=['boss_type'])
        issue(accounts[0], f'vip-month:{accounts[0].pk}:1:2026-11:0', name='历史券', rate=90)
        issue(accounts[0], f'vip-month:{accounts[1].pk}:1:2026-11:8', name='错误归属券', rate=90)
        before = tuple(model.objects.count() for model in (Coupon, UserCoupon, VipMonthlyCouponGrant, Wallet, Transaction))
        result = audit_monthly('default', month=date(2026, 11, 1))
        rows = {row['account_id']:row for row in result['records']}
        self.assertEqual((result['with_coupons'], result['without_coupons']), (1, 1))
        self.assertEqual(rows[accounts[0].pk]['recorded_coupon_count'], 1)
        self.assertEqual(rows[accounts[1].pk]['evidence'], 'NO_COUPONS_CANNOT_PROVE_ZERO_GRANT')
        self.assertEqual(result['issues']['owner_or_cycle_mismatch'], 1)
        self.assertEqual(before, tuple(model.objects.count() for model in (Coupon, UserCoupon, VipMonthlyCouponGrant, Wallet, Transaction)))
        output = StringIO()
        call_command('audit_vip_monthly_evidence', database='default', month='2026-11', stdout=output)
        self.assertIn('NO_COUPONS_CANNOT_PROVE_ZERO_GRANT', output.getvalue())
        with self.assertRaises(CommandError):
            call_command('audit_vip_monthly_evidence', database='default', month='2026-13', stdout=StringIO())
