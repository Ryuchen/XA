import os
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from rest_framework.test import APIClient

from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_order, make_provider, make_user
from wallet.services import get_wallet


@skipUnless(os.environ.get('XA_DEPOSIT_REFUND_GUARD_BROWSER') == '1', 'Requires isolated ASGI and admin Vite')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
class DepositRefundGuardBrowserTest(LiveServerTestCase):
    def test_staff_sees_active_order_and_cannot_submit_refund(self):
        permissions = ['escort:view', 'escort:deposit_refund']
        staff = make_console_user(permissions)
        admin = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, admin)
        staff.admin_membership.account = admin; staff.admin_membership.save(update_fields=['account'])
        provider = make_provider(display_name='隔离退押金陪玩', deposit_paid=3000)
        account = get_or_create_account_for_legacy_user(provider)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        wallet = get_wallet(account=account, user=provider); wallet.balance=1000; wallet.save(update_fields=['balance'])
        make_order(make_user(), provider=provider, provider_account=account, status='IN_SERVICE')
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(admin)[0])
        preview = staff_client.get(f'/api/admin/escorts/{provider.escort_profile.pk}/deposit-refund/')
        self.assertEqual(preview.data['code'], 0, preview.data)
        self.assertEqual(preview.data['data']['active_order_count'], 1)
        blocked = staff_client.post(f'/api/admin/escorts/{provider.escort_profile.pk}/deposit-refund/',
            {'amount': 3000, 'reason': '隔离退押金', 'client_request_id': 'deposit-guard-browser'}, format='json')
        self.assertEqual(blocked.data['code'], 409, blocked.data)
        wallet.refresh_from_db(); provider.escort_profile.refresh_from_db()
        self.assertEqual((wallet.balance, provider.escort_profile.deposit_paid), (1000, 3000))