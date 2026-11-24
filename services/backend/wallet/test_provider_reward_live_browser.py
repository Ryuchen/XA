"""A provider's wallet follows real staff reward and penalty writes."""
import os
import shutil

from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient, APITransactionTestCase

from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.tokens import issue_account_tokens
from orders.tests.factories import make_console_user, make_provider, make_service, make_service_category, make_user, make_wallet
from wallet.models import DisposeRecord, Transaction


@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False)
class ProviderRewardLiveBrowserTest(APITransactionTestCase):
    def test_open_wallet_updates_reward_penalty_and_transactions(self):
        self.run_wallet_flow(gift=False)

    def test_open_wallet_updates_when_boss_sends_and_staff_refunds_gift(self):
        self.run_wallet_flow(gift=True)

    def run_wallet_flow(self, *, gift):
        node, database, root = shutil.which('node'), os.environ.get('XA_ASGI_NETWORK_TEST_DB'), os.environ.get('XA_PROVIDER_CHAT_H5_ROOT')
        if not node or not database or connection.settings_dict['NAME'] != database or not root:
            self.skipTest('Requires an isolated file database, ASGI and provider H5 build')
        provider, staff = make_provider(), make_console_user(['escort:dispose', 'order:view', 'order:refund'])
        account, staff_account = [get_or_create_account_for_legacy_user(user) for user in (provider, staff)]
        provider.escort_profile.account = account
        provider.escort_profile.save(update_fields=['account'])
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        wallet = make_wallet(provider, balance=0)
        wallet.account = account
        wallet.save(update_fields=['account'])
        gift_input = None
        if gift:
            boss = make_user()
            boss_account = get_or_create_account_for_legacy_user(boss)
            boss_wallet = make_wallet(boss, balance=50000)
            boss_wallet.account = boss_account
            boss_wallet.save(update_fields=['account'])
            service = make_service(name='隔离实时礼物', price=10000, commission_rate=20,
                                   service_category=make_service_category(is_gift=True))
            gift_input = {'bossToken': issue_account_tokens(boss_account)[0], 'serviceId': service.pk}
        staff_client = APIClient(HTTP_HOST='admin.esports.club', HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        if gift:
            boss_client = APIClient(HTTP_HOST='sl.esports.club', HTTP_AUTHORIZATION='Bearer ' + gift_input['bossToken'])
            sent = boss_client.post('/api/orders/gifts/send/', {
                'gift_service_id': gift_input['serviceId'], 'provider_id': account.pk, 'quantity': 1,
                'expected_amount': 10000, 'client_request_id': 'live-wallet-gift'}, format='json')
            self.assertEqual(sent.data['code'], 0, sent.data)
            gift_id = sent.data['data']['id']
            detail = staff_client.get(f'/api/admin/orders/{gift_id}/')
            self.assertEqual(detail.data['code'], 0, detail.data)
            self.assertEqual(detail.data['data']['amount'], 10000)
            plan = staff_client.get(f'/api/admin/orders/{gift_id}/refund-preview/')
            self.assertEqual(plan.data['code'], 0, plan.data)
            refunded = staff_client.post(f'/api/admin/orders/{gift_id}/refund/', {
                'reason': '隔离赠礼退款', 'plan_token': plan.data['data']['plan_token'],
                'client_request_id': 'live-wallet-gift-refund'}, format='json')
            self.assertEqual(refunded.data['code'], 0, refunded.data)
        else:
            for dispose_type, amount, reason in (('REWARD', 3000, '实时服务奖励'), ('PENALTY', 1000, '实时服务罚款')):
                disposed = staff_client.post(f'/api/admin/escorts/{provider.escort_profile.pk}/dispose/', {
                    'dispose_type': dispose_type, 'amount': amount, 'reason': reason,
                    'client_request_id': f'live-{dispose_type.lower()}'}, format='json')
                self.assertEqual(disposed.data['code'], 0, disposed.data)
        wallet.refresh_from_db()
        if gift:
            boss_wallet.refresh_from_db()
            self.assertEqual((wallet.balance, boss_wallet.balance), (0, 50000))
            self.assertEqual(list(Transaction.objects.filter(wallet=wallet).order_by('amount').values_list('amount', flat=True)), [-8000, 8000])
        else:
            self.assertEqual(wallet.balance, 2000)
            self.assertEqual(DisposeRecord.objects.filter(account=account).count(), 2)
            self.assertEqual(list(Transaction.objects.filter(wallet=wallet).order_by('amount').values_list('amount', flat=True)), [-1000, 3000])