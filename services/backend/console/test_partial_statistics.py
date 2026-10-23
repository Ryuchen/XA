"""Read-only partial-payment compatibility for dashboards and maturity scans."""
from datetime import timedelta
from django.test import override_settings
from rest_framework.test import APITestCase
from club_accounts.services import get_or_create_account_for_legacy_user
from engagement.models import ConsumptionConfirmation
from engagement.services import completed_consumption
from engagement.tasks import confirm_due_consumption
from orders.models import Order,OrderProvider,OrderRefund,ServiceCategory
from orders.tests import test_partial_refunds as fixtures
from orders.tests.factories import make_order,make_service
from users.companion_picker import companion_service_amounts
from users.companion_shortcuts import recent_provider_ids
from wallet.models import ProviderReport,Transaction
from wallet.services import get_wallet


@override_settings(ORDER_SETTLEMENT_MODE='REPORT_APPROVAL',LEGACY_FORCE_AUTH_COMPAT=False,REQUIRE_ORDER_EVIDENCE_IMAGES=False)
class PartialStatisticsTest(APITestCase):
    client_for=fixtures.PartialRefundTest.client_for
    settled=fixtures.PartialRefundTest.settled
    preview_amount=fixtures.PartialRefundTest.preview_amount
    pay_refund=fixtures.PartialRefundTest.pay_refund
    ok=fixtures.PartialRefundTest.ok

    def setUp(self):
        fixtures.PartialRefundTest.setUp(self)
        role=self.admin_user.admin_membership.roles.first()
        role.permissions=[*role.permissions,'dashboard:view','player_dashboard:view']
        role.save(update_fields=['permissions'])
        profile=self.provider.escort_profile
        profile.gender='MALE';profile.save(update_fields=['gender'])

    def test_partial_dashboard_counts_and_turnover_keep_net_paid_order(self):
        order=self.settled()
        self.ok(self.pay_refund(order,2500))
        basic=self.ok(self.admin.get('/api/admin/dashboard/stats'))
        player=self.ok(self.admin.get('/api/admin/dashboard/player'))
        self.assertEqual((basic['paid_order_total'],basic['paid_amount_total'],basic['order_amount_total']),(1,7500,7500))
        self.assertEqual((player['kpi']['total_amount'],player['kpi']['male_game']),(7500,7500))
        self.assertEqual((player['rank']['amount'][0]['value'],player['rank']['income'][0]['value'],player['rank']['count'][0]['value']),(7500,6000,1))
        secondary=player['secondary']
        self.ok(self.pay_refund(order))
        result=self.ok(self.admin.get('/api/admin/dashboard/player'))
        self.assertEqual((result['kpi']['total_amount'],result['rank']['income']),(0,[]))
        self.assertEqual(result['secondary']['total_deposit'],secondary['total_deposit'])

    def test_same_wallet_inviter_is_not_counted_as_provider_earnings(self):
        order=self.settled(inviter=self.provider)
        self.ok(self.pay_refund(order,2500))
        result=self.ok(self.admin.get('/api/admin/dashboard/player'))
        self.assertEqual(result['rank']['income'][0]['value'],5625)

    def test_nondivisible_team_income_uses_actual_original_and_reversal_ledger(self):
        other_profile=self.other.escort_profile
        other_profile.gender='FEMALE';other_profile.save(update_fields=['gender'])
        order=make_order(self.boss,provider=self.provider,customer_account=self.boss_account,
            provider_account=self.provider_account,status='COMPLETED',payment_status='PARTIALLY_REFUNDED',
            amount=8001,provider_income=6401,shop_income=1600,escort_mode='DOUBLE')
        OrderProvider.objects.create(order=order,provider=self.provider,provider_account=self.provider_account,settlement_base=3000,provider_income=2400)
        other_account=get_or_create_account_for_legacy_user(self.other)
        OrderProvider.objects.create(order=order,provider=self.other,provider_account=other_account,settlement_base=5001,provider_income=4001)
        other_wallet=get_wallet(user=self.other,account=other_account)
        for wallet,amount,recovered in ((self.provider_wallet,2400,300),(other_wallet,4001,500)):
            original=Transaction.objects.create(wallet=wallet,order=order,tx_type='INCOME',amount=amount,remark=f'订单收入：{order.order_no}')
            Transaction.objects.create(wallet=wallet,order=order,tx_type='INCOME',amount=-recovered,reversal_of=original)
        OrderRefund.objects.create(order=order,amount=1000)
        result=self.ok(self.admin.get('/api/admin/dashboard/player'))
        self.assertEqual(result['kpi']['total_amount'],7001)
        self.assertEqual(result['kpi']['male_game']+result['kpi']['female_game'],7001)
        self.assertEqual(sorted(row['value'] for row in result['rank']['income']),[2100,3501])
        self.assertEqual([r['value'] for r in result['rank']['count']],[1,1])

    def test_partial_recent_and_service_statistics_keep_net_selection_history(self):
        order=self.settled()
        self.ok(self.pay_refund(order,2500))
        amounts=companion_service_amounts([self.provider.escort_profile],include_recent=True)
        self.assertEqual((amounts['monthly'][self.provider_account.pk],amounts['recent'][self.provider_account.pk]),(7500,7500))
        self.assertIn(self.provider_account.pk,recent_provider_ids(self.boss_account))

    def test_periodic_worker_confirms_partial_net_after_original_maturity(self):
        order=self.settled()
        self.ok(self.pay_refund(order,2500))
        ProviderReport.objects.filter(order=order).update(submitted_at=self.now-timedelta(hours=74))
        self.assertEqual(confirm_due_consumption(),1)
        row=ConsumptionConfirmation.objects.get(order=order)
        self.assertEqual((row.amount,row.refunded_amount,completed_consumption(self.boss_account)),(10000,2500,7500))
        self.assertEqual(confirm_due_consumption(),0)
