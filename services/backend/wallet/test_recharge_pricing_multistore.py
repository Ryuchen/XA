"""Recharge tiers and their quotes remain isolated by store database."""
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user
from club_accounts.models import ClubAccount
from club_accounts.tokens import issue_account_tokens
from engagement.models import RechargePackage
from orders.tests.factories import make_console_user
from stores.context import resolve_store, store_context
from stores.tests_hosts import xinan_store
from wallet.models import RechargeGiftApproval, RechargeRecord, Transaction, Wallet


@override_settings(ALLOWED_HOSTS=['*'], LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=False)
class RechargePricingIsolationTest(TestCase):
    databases = {'default', 'store_b'}

    def test_package_configuration_exposes_vip_payment_rule_in_each_store(self):
        stores = [(resolve_store(), 'sl.esports.club', 10000), (xinan_store(), 'xa.esports.club', 20000)]
        client = APIClient()
        for store, host, gift in stores:
            with store_context(store):
                staff = make_console_user(['engagement:view', 'engagement:edit', 'wallet:recharge'])
                account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = account
                staff.admin_membership.save(update_fields=['account'])
                token = issue_account_tokens(account)[0]
            auth = {'HTTP_HOST': host, 'HTTP_AUTHORIZATION': 'Bearer ' + token}
            url = '/api/admin/recharge-packages/'
            obsolete = client.post(url, {'amount': 300000, 'gift_amount': gift, 'points_rate': 150}, format='json', **auth)
            self.assertEqual(obsolete.status_code, 400, obsolete.data)
            created = client.post(url, {'amount': 300000, 'gift_amount': gift}, format='json', **auth)
            self.assertEqual(created.status_code, 201, created.data)
            data = created.data['data']
            self.assertEqual(data['points_rule'], 'VIP_AT_PAYMENT')
            self.assertNotIn('points_rate', data)
            rejected = client.patch(f'{url}{data["id"]}/', {'points_rate': 200}, format='json', **auth)
            self.assertEqual(rejected.status_code, 400, rejected.data)
            quote = client.get('/api/admin/wallets/recharge-quote/', {'amount': 700000}, **auth)
            self.assertEqual((quote.data['data']['gift_amount'], quote.data['data']['remaining'],
                              quote.data['data']['points_rule']), (gift * 2, 100000, 'VIP_AT_PAYMENT'))
            visible = client.get('/api/admin/wallets/recharge-packages/', **auth).data['data']
            self.assertTrue(all('points_rate' not in row and row['points_rule'] == 'VIP_AT_PAYMENT' for row in visible))

    def test_same_principal_uses_only_its_own_store_tiers(self):
        stores = [(resolve_store(), 'sl.esports.club', 10000), (xinan_store(), 'xa.esports.club', 25000)]
        credentials = []
        for store, host, gift in stores:
            with store_context(store):
                RechargePackage.objects.all().delete()
                RechargePackage.objects.create(amount=300000, gift_amount=gift, active=True)
                staff = make_console_user(['wallet:recharge'])
                account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = account
                staff.admin_membership.save(update_fields=['account'])
                credentials.append({'HTTP_HOST': host, 'HTTP_AUTHORIZATION': 'Bearer ' + issue_account_tokens(account)[0]})
        client = APIClient()
        for index, (_, _, gift) in enumerate(stores):
            result = client.get('/api/admin/wallets/recharge-quote/', {'amount': 600000}, **credentials[index])
            self.assertEqual(result.status_code, 200, result.data)
            self.assertEqual(result.data['data']['gift_amount'], gift * 2)
        mismatch = client.get('/api/admin/wallets/recharge-quote/', {'amount': 600000},
                              HTTP_HOST='xa.esports.club', HTTP_AUTHORIZATION=credentials[0]['HTTP_AUTHORIZATION'])
        self.assertNotEqual(mismatch.status_code, 200)

    def test_approval_with_same_record_ids_cannot_cross_stores(self):
        stores = [(resolve_store(), 'sl.esports.club', 10000), (xinan_store(), 'xa.esports.club', 25000)]
        credentials, managers, ids = [], [], []
        client = APIClient()
        for store, host, gift in stores:
            with store_context(store):
                RechargePackage.objects.all().delete()
                RechargePackage.objects.create(amount=300000, gift_amount=gift, active=True)
                staff = make_console_user(['wallet:recharge', 'recharge:view'])
                account = get_or_create_account_for_legacy_user(staff)
                staff.admin_membership.account = account; staff.admin_membership.save(update_fields=['account'])
                manager = make_console_user(['recharge:view', 'recharge:gift_approve'])
                manager_account = get_or_create_account_for_legacy_user(manager)
                manager.admin_membership.account = manager_account; manager.admin_membership.save(update_fields=['account'])
                managers.append({'HTTP_HOST': host, 'HTTP_AUTHORIZATION': 'Bearer ' + issue_account_tokens(manager_account)[0]})
                boss = ClubAccount.objects.create_account(username='isolated-pricing-boss', account_type='BOSS')
                token = issue_account_tokens(account)[0]
                credentials.append({'HTTP_HOST': host, 'HTTP_AUTHORIZATION': 'Bearer ' + token})
                result = client.post('/api/admin/wallets/recharge/', {'account_id': boss.pk, 'amount': 300000,
                    'gift_amount': gift + 500, 'trade_no': 'same-isolated-receipt',
                    'client_request_id': 'same-isolated-request'}, format='json', **credentials[-1])
                self.assertEqual(result.status_code, 200, result.data)
                ids.append((RechargeRecord.objects.get().pk, boss.pk))
        self.assertEqual(ids[0][0], ids[1][0])
        cross = client.post(f'/api/admin/recharge-records/{ids[1][0]}/gift-decision/',
            {'decision': 'APPROVED'}, format='json', HTTP_HOST='xa.esports.club',
            HTTP_AUTHORIZATION=managers[0]['HTTP_AUTHORIZATION'])
        self.assertNotEqual(cross.status_code, 200)
        for i, (store, _, gift) in enumerate(stores):
            record_id, boss_id = ids[i]
            with store_context(store):
                self.assertEqual(Wallet.objects.get(account_id=boss_id).balance, 300000)
            endpoint = f'/api/admin/recharge-records/{record_id}/gift-decision/'
            denied = client.post(endpoint, {'decision': 'APPROVED'}, format='json', **credentials[i])
            self.assertEqual(denied.status_code, 403)
            decision = 'APPROVED' if i == 0 else 'REJECTED'
            reason = '本店核实'
            result = client.post(endpoint, {'decision': decision, 'reason': reason}, format='json', **managers[i])
            self.assertEqual(result.status_code, 200, result.data)
            replay = client.post(endpoint, {'decision': decision, 'reason': reason}, format='json', **managers[i])
            self.assertEqual(replay.status_code, 200, replay.data)
            self.assertEqual(replay.data['data']['gift_approval'], result.data['data']['gift_approval'])
            conflict = client.post(endpoint, {'decision': 'REJECTED' if i == 0 else 'APPROVED',
                                              'reason': reason}, format='json', **managers[i])
            self.assertEqual(conflict.status_code, 409, conflict.data)
            with store_context(store):
                wallet = Wallet.objects.get(account_id=boss_id)
                expected = gift + 500 if i == 0 else gift
                self.assertEqual((wallet.balance, wallet.total_recharge, wallet.total_gift),
                                 (300000 + expected, 300000, expected))
                approval = RechargeGiftApproval.objects.get(recharge_id=record_id)
                self.assertEqual((approval.status, approval.decision_reason), (decision, reason))
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='TOPUP', status='SUCCESS').count(), 1)
                self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='GIFT', status='SUCCESS').count(), 1)
            if i == 0:
                with store_context(stores[1][0]):
                    self.assertEqual(RechargeGiftApproval.objects.get(recharge_id=ids[1][0]).status, 'PENDING')
