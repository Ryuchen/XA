from django.test import override_settings
from rest_framework.test import APIClient

from club_accounts.tokens import issue_account_tokens
from console.test_dispatch_capacity import _CapacityTestBase
from orders.models import Order, OrderProvider
from orders.tests.factories import make_console_user, make_order
from club_accounts.services import get_or_create_account_for_legacy_user
from wallet.models import Transaction


@override_settings(ORIGINAL_ATTRIBUTION_ENABLED=False, SEGMENT_STANDARD_TRANSFER_ENABLED=False,
                   LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class TransferCommissionGuardTest(_CapacityTestBase):
    def setUp(self):
        super().setUp()
        self.old, self.old_account, _ = self._new_escort('原陪玩')
        self.new, self.new_account, _ = self._new_escort('接替陪玩')
        staff = make_console_user(['order:view', 'order:dispatch'])
        account = get_or_create_account_for_legacy_user(staff)
        staff.admin_membership.account = account
        staff.admin_membership.save(update_fields=['account'])
        self.client = APIClient(HTTP_HOST='admin.esports.club',
                                HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])

    def check_blocked(self, mode):
        order = make_order(self.boss, service=self.service, provider=self.old,
            customer_account=self.boss_account, provider_account=self.old_account,
            status=Order.Status.IN_SERVICE, payment_status=Order.PaymentStatus.PAID,
            settlement_mode=mode, amount=10000, commission_rate=20)
        OrderProvider.objects.create(order=order, provider=self.old, provider_account=self.old_account,
            settlement_base=10000, provider_income=8000, commission_rate=20)
        response = self.client.post(f'/api/admin/orders/{order.pk}/transfer/', {'transfers': [{
            'old_provider_id': self.old_account.pk, 'new_provider_id': self.new_account.pk,
            'split_type': 'PERCENT', 'split_value': 50, 'penalty_amount': 0}]}, format='json')
        self.assertNotEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.assertEqual(order.provider_account_id, self.old_account.pk)
        self.assertFalse(Transaction.objects.filter(order=order, tx_type='INCOME').exists())

    def test_missing_standard_snapshot_blocks_transfer(self):
        self.check_blocked(Order.SettlementMode.REPORT_APPROVAL)

    def test_completion_mode_never_pays_out_on_transfer(self):
        self.check_blocked(Order.SettlementMode.COMPLETION)
