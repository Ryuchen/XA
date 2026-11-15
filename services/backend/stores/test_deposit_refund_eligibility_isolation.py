"""Same IDs in two stores do not mix deposit eligibility."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from console.models import AdminMembership, AdminRole
from orders.models import Order, ServiceItem
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from users.models import EscortProfile
from wallet.models import Wallet


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, LOGIN_OTP_REQUIRED=False)
class DepositRefundEligibilityIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_identical_provider_and_order_ids_stay_in_their_store(self):
        stores = (resolve_store(), xinan_store())
        tokens = []
        for store in stores:
            with store_context(store):
                User = get_user_model()
                provider = User.objects.create_user(pk=710001, username='deposit-isolation-player', role='PROVIDER')
                staff = User.objects.create_user(pk=710002, username='deposit-isolation-staff', role='OPERATOR')
                boss = User.objects.create_user(pk=710003, username='deposit-isolation-boss', role='CUSTOMER')
                account = ClubAccount.objects.create_account(pk=710001, username='deposit-isolation-player', account_type='PROVIDER')
                admin = ClubAccount.objects.create_account(pk=710002, username='deposit-isolation-staff', account_type='STAFF')
                profile = EscortProfile.objects.create(pk=710004, user=provider, account=account, display_name='隔离陪玩', deposit_paid=3000)
                Wallet.objects.filter(user=provider).update(account=account)
                role = AdminRole.objects.create(pk=710005, name='隔离客服', code='deposit-isolation',
                                                permissions=['escort:view', 'escort:deposit_refund'])
                membership = AdminMembership.objects.create(user=staff, account=admin); membership.roles.add(role)
                service = ServiceItem.objects.create(pk=710006, name='隔离服务', price=1000)
                closed_at = timezone.now()-timedelta(days=8)
                order = Order.objects.create(pk=710007, customer=boss, provider=provider, provider_account=account,
                    service=service, amount=1000 if store.slug == 'xinan' else 0,
                    status='IN_SERVICE' if store.slug == 'xinan' else 'CANCELLED',
                    payment_status='PAID' if store.slug == 'xinan' else 'UNPAID',
                    cancelled_at=None if store.slug == 'xinan' else closed_at)
                if store.slug != 'xinan':
                    Order.objects.filter(pk=order.pk).update(created_at=closed_at-timedelta(hours=1))
                tokens.append(issue_account_tokens(admin)[0])
        for index, token in enumerate(tokens):
            client = APIClient(HTTP_HOST='admin.esports.club')
            client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
            data = client.get('/api/admin/escorts/710004/deposit-refund/').data['data']
            self.assertEqual(data['can_refund'], index == 0)
            self.assertEqual(data['active_order_count'], index)
            self.assertEqual(data['wait_status'], ('READY', 'BLOCKED')[index])
            attempted = client.get('/api/admin/escorts/710004/deposit-refund/', {'store':'shenglang'})
            if attempted.status_code == 200:
                self.assertEqual(attempted.data['data'], data)
            else:
                self.assertEqual(attempted.status_code, 400)
