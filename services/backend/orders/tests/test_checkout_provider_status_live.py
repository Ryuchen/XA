"""An open checkout follows a provider's real availability changes."""
import os
import tempfile
from datetime import timedelta
from unittest import skipUnless

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from club_accounts.services import get_or_create_account_for_legacy_user, link_legacy_relations
from club_accounts.tokens import issue_account_tokens
from orders.models import Order
from orders.tests.factories import make_console_user, make_provider, make_service, make_user, make_wallet
from orders.tests.test_release_flow import screenshot
from site_messages.models import Message
from wallet.models import ProviderReport, Transaction, Wallet
from engagement.models import ConsumptionConfirmation
from engagement.tasks import confirm_due_consumption


@skipUnless(os.environ.get('XA_CHECKOUT_PROVIDER_LIVE') == '1', 'Requires isolated HTTP and Node')
@override_settings(LEGACY_FORCE_AUTH_COMPAT=False, KOOK_ENABLED=False, WECOM_DISPATCH_ENABLED=False,
                   CELERY_TASK_ALWAYS_EAGER=False, REQUIRE_ORDER_EVIDENCE_IMAGES=True,
                   ORDER_SETTLEMENT_MODE='REPORT_APPROVAL')
class CheckoutProviderStatusLiveTest(LiveServerTestCase):
    def test_double_checkout_replaces_offline_companion_and_creates_one_order(self):
        media = tempfile.TemporaryDirectory(prefix='xa-double-replacement-evidence-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                     pass_expires_at=timezone.now() + timedelta(days=1)) for name in ('陪玩甲', '陪玩乙', '陪玩替补')]
        users = [boss, *providers]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(name='双陪替补验收', price=10000, billing_provider_count=2)
        for user, account in zip(providers, accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.save(update_fields=['account'])
            user.escort_profile.service_items.add(service)
        make_wallet(boss, balance=50000)
        offline_client = APIClient(); offline_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        offline = offline_client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json')
        self.assertEqual(offline.data['code'], 0, offline.data)
        boss_client = APIClient(); boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        blocked = boss_client.post('/api/orders/quote/', {'service_id': service.pk,
            'provider_ids': [accounts[1].pk, accounts[2].pk]}, format='json')
        self.assertEqual(blocked.data['code'], 400, blocked.data)
        created = boss_client.post('/api/orders/create/', {'service_id': service.pk,
            'provider_ids': [accounts[1].pk, accounts[3].pk], 'game_region': '隔离区', 'game_nickname': '隔离老板',
            'remark': '保留备注', 'client_request_id': 'double-replacement-checkout'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(customer_account=accounts[0])
        self.assertEqual(order.status, 'GRABBED')
        self.assertEqual(set(order.providers.filter(released_at__isnull=True).values_list('provider_account_id', flat=True)),
                         {accounts[1].pk, accounts[3].pk})
        self.assertEqual(Wallet.objects.get(user=boss).balance, 30000)
        for index, account in enumerate(accounts[1:]):
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            rows = client.get('/api/orders/orders/', {'status': 'grabbed', 'role': 'provider'}).data['data']
            self.assertEqual(order.pk in [row['id'] for row in rows], index in (0, 2))
            notices = Message.objects.filter(recipient_account=account, related_order_id=order.pk, title='老板指定你服务')
            self.assertEqual(notices.count(), 1 if index in (0, 2) else 0)
        staff = make_console_user(['order:view', 'report:view', 'report:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        detail = client.get(f'/api/admin/orders/{order.pk}/').data
        self.assertEqual(detail['code'], 0, detail)
        self.assertEqual((detail['data']['status'], detail['data']['amount']), ('GRABBED', 20000))
        provider_clients = []
        for account in (accounts[1], accounts[3]):
            provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0])
            provider_clients.append(provider_client)

        def progress(stage, submitted, status):
            for response in (boss_client.get(f'/api/orders/orders/{order.pk}/'), client.get(f'/api/admin/orders/{order.pk}/')):
                self.assertEqual(response.data['code'], 0, response.data)
                data = response.data['data']
                self.assertEqual(data['status'], status)
                self.assertEqual((data['team_progress']['stage'], data['team_progress']['submitted_count'],
                                  data['team_progress']['total_count']), (stage, submitted, 2))

        for index, provider_client in enumerate(provider_clients):
            response = provider_client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
            progress('ENTRY' if index == 0 else 'COMPLETION', 1 if index == 0 else 0,
                     'GRABBED' if index == 0 else 'IN_SERVICE')
        response = provider_clients[0].post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        progress('COMPLETION', 1, 'IN_SERVICE')
        response = provider_clients[1].post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, 'COMPLETED')
        self.assertIsNone(order.settled_at)
        reports = ProviderReport.objects.filter(order=order)
        self.assertEqual(reports.count(), 2)
        self.assertEqual(set(reports.values_list('status', flat=True)), {'DRAFT'})
        self.assertEqual(sum(reports.values_list('amount', flat=True)), 20000)
        for provider_client, account in zip(provider_clients, (accounts[1], accounts[3])):
            report = reports.get(provider_account=account)
            upload = provider_client.post(f'/api/wallet/reports/{report.pk}/images/',
                                          {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
            self.assertEqual(upload.data['code'], 0, upload.data)
            submitted = provider_client.post(f'/api/wallet/reports/{report.pk}/submit/', {}, format='json')
            self.assertEqual(submitted.data['code'], 0, submitted.data)
        self.assertEqual(set(reports.values_list('status', flat=True)), {'PENDING'})
        replaced_client = APIClient(); replaced_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[2])[0])
        self.assertFalse(any(row['order_id'] == order.pk for row in replaced_client.get('/api/wallet/reports/').data['data']))
        first = reports.get(provider_account=accounts[1])
        second = reports.get(provider_account=accounts[3])
        rejected = client.post(f'/api/admin/reports/{first.pk}/reject/', {'audit_remark': '请补充清晰战绩截图'}, format='json')
        self.assertEqual(rejected.data['code'], 0, rejected.data)
        approved = client.post(f'/api/admin/reports/{second.pk}/approve/', {}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)
        first.refresh_from_db(); second.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((first.status, second.status), ('REJECTED', 'APPROVED'))
        self.assertIsNone(order.consumption_ready_at)
        self.assertIsNone(order.settled_at)
        boss_view = boss_client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertEqual(boss_view['status'], 'COMPLETED')
        self.assertIsNone(boss_view['consumption_ready_at'])
        upload = provider_clients[0].post(f'/api/wallet/reports/{first.pk}/images/',
                                          {'kind': 'RESULT', 'image': screenshot()}, format='multipart')
        self.assertEqual(upload.data['code'], 0, upload.data)
        resubmitted = provider_clients[0].post(f'/api/wallet/reports/{first.pk}/submit/', {}, format='json')
        self.assertEqual(resubmitted.data['code'], 0, resubmitted.data)
        first.refresh_from_db(); second.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((first.status, second.status), ('PENDING', 'APPROVED'))
        self.assertIsNotNone(order.consumption_ready_at)
        self.assertIsNone(order.settled_at)
        approved = client.post(f'/api/admin/reports/{first.pk}/approve/', {}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)
        self.assertEqual(set(ProviderReport.objects.filter(order=order).values_list('status', flat=True)), {'APPROVED'})
        order.refresh_from_db()
        self.assertIsNone(order.settled_at, 'approval does not bypass the consumption confirmation period')
        for account in (accounts[1], accounts[3]):
            wallet = Wallet.objects.filter(account=account).first()
            if wallet is not None:
                self.assertEqual(wallet.balance, 0)
            self.assertFalse(Transaction.objects.filter(order=order, wallet__account=account,
                tx_type='INCOME', status='SUCCESS').exists())
        # Completion and report submission have separate 72-hour clocks.
        # Advance timestamps only in the isolated database, then execute the
        # actual scheduled scan and verify its retry is idempotent.
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now() - timedelta(hours=73))
        self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.settled_at)
        self.assertIsNone(order.consumption_confirmed_at)
        for account in (accounts[1], accounts[3]):
            share = order.providers.get(provider_account=account)
            self.assertGreater(share.provider_income, 0)
            self.assertEqual(Wallet.objects.get(account=account).balance, share.provider_income)
            self.assertEqual(Transaction.objects.filter(order=order, wallet__account=account,
                tx_type='INCOME', amount=share.provider_income, status='SUCCESS').count(), 1)
        verify_report_surfaces('已通过', settled=True)
        reports.update(submitted_at=timezone.now() - timedelta(hours=73))
        self.assertEqual(confirm_due_consumption.run(), 1)
        self.assertEqual(confirm_due_consumption.run(), 0)
        order.refresh_from_db()
        self.assertIsNotNone(order.consumption_confirmed_at)
        self.assertEqual(ConsumptionConfirmation.objects.filter(order=order).count(), 1)
        boss_view = boss_client.get(f'/api/orders/orders/{order.pk}/').data['data']
        self.assertIsNotNone(boss_view['consumption_confirmed_at'])
        for account in (accounts[1], accounts[3]):
            self.assertEqual(Transaction.objects.filter(order=order, wallet__account=account,
                tx_type='INCOME', status='SUCCESS').count(), 1)

    def test_double_income_freeze_and_release_visible_in_provider_wallets(self):
        from wallet.income import release_due_income
        from wallet.models import IncomeHold, SystemConfig
        media = tempfile.TemporaryDirectory(prefix='xa-double-income-evidence-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name))
        SystemConfig.objects.create(key='club_income_rules_enabled', value='1')
        boss = make_user()
        providers = [make_provider(display_name=name, deposit_mode='EARNINGS', pass_tier='BLACK',
                     pass_expires_at=timezone.now() + timedelta(days=1)) for name in ('收益陪玩甲', '收益陪玩乙')]
        users = [boss, *providers]
        accounts = [get_or_create_account_for_legacy_user(user) for user in users]
        for user, account in zip(users, accounts):
            link_legacy_relations(user, account)
        service = make_service(name='双陪收益冻结验收', price=10000, billing_provider_count=2)
        for user, account in zip(providers, accounts[1:]):
            user.escort_profile.account = account
            user.escort_profile.deposit_paid = 39000
            user.escort_profile.save(update_fields=['account', 'deposit_paid'])
            user.escort_profile.service_items.add(service)
        make_wallet(boss, balance=50000)
        boss_client = APIClient(); boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(accounts[0])[0])
        created = boss_client.post('/api/orders/create/', {'service_id': service.pk,
            'provider_ids': [account.pk for account in accounts[1:]], 'client_request_id': 'double-income-freeze'}, format='json')
        self.assertEqual(created.data['code'], 0, created.data)
        order = Order.objects.get(pk=created.data['data']['id'])
        clients = []
        for account in accounts[1:]:
            client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(account)[0]); clients.append(client)
            response = client.post(f'/api/orders/orders/{order.pk}/start/', {'entry_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
        for client in clients:
            response = client.post(f'/api/orders/orders/{order.pk}/complete/', {'completion_image': screenshot()}, format='multipart')
            self.assertEqual(response.data['code'], 0, response.data)
        staff = make_console_user(['dashboard:view', 'report:view', 'report:audit', 'transaction:view', 'wallet:view', 'order:view', 'withdraw:view', 'withdraw:audit'])
        staff_account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        reviewer = APIClient(); reviewer.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        for client, account in zip(clients, accounts[1:]):
            report = ProviderReport.objects.get(order=order, provider_account=account)
            for url, payload, fmt in ((f'/api/wallet/reports/{report.pk}/images/', {'kind': 'RESULT', 'image': screenshot()}, 'multipart'),
                                      (f'/api/wallet/reports/{report.pk}/submit/', {}, 'json')):
                response = client.post(url, payload, format=fmt); self.assertEqual(response.data['code'], 0, response.data)
            response = reviewer.post(f'/api/admin/reports/{report.pk}/approve/', {}, format='json')
            self.assertEqual(response.data['code'], 0, response.data)
        Order.objects.filter(pk=order.pk).update(completed_at=timezone.now() - timedelta(hours=73))
        confirm_due_consumption.run()
        holds = [IncomeHold.objects.get(income_transaction__order=order, wallet__account=account) for account in accounts[1:]]
        for hold in holds:
            hold.wallet.refresh_from_db()
            self.assertGreater(hold.deposit_amount, 0)
            self.assertGreater(hold.amount, 0)
            self.assertEqual((hold.wallet.balance, hold.wallet.frozen_amount), (0, hold.amount))
        # A migrated legacy user may have a different username from the
        # wallet's actual business account; display and search must agree.
        providers[0].username = 'isolated-legacy-income'
        providers[0].save(update_fields=['username'])
        accounts[1].username = 'isolated-current-income'
        accounts[1].save(update_fields=['username'])
        current_rows = reviewer.get('/api/admin/transactions/', {'keyword': accounts[1].username}).data['data']['list']
        legacy_rows = reviewer.get('/api/admin/transactions/', {'keyword': providers[0].username}).data['data']['list']
        self.assertTrue(any(row['order'] == order.pk and row['username'] == accounts[1].username for row in current_rows))
        self.assertFalse(any(row['order'] == order.pk for row in legacy_rows))
        release_time = max(hold.release_at for hold in holds) + timedelta(seconds=1)
        self.assertEqual(release_due_income(now=release_time), 2)
        self.assertEqual(release_due_income(now=release_time), 0)
        for hold in holds:
            hold.wallet.refresh_from_db()
            self.assertEqual((hold.wallet.balance, hold.wallet.frozen_amount), (hold.amount, 0))
            self.assertEqual(Transaction.objects.filter(order=order, wallet=hold.wallet,
                tx_type='EARNINGS_RELEASE', status='SUCCESS').count(), 1)
        # The first provider now withdraws earnings produced by this order.
        provider_client = clients[0]
        binding = provider_client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-account-20261102', 'payee_name': '隔离测试',
            'expected_id': 0, 'expected_version': 0}, format='json')
        self.assertEqual(binding.data['code'], 0, binding.data)
        payee = binding.data['data']
        amount = 6000
        request = provider_client.post('/api/wallet/withdraw/', {'amount': amount,
            'withdrawal_account_id': payee['id'], 'expected_payee_version': payee['version'],
            'client_request_id': 'isolated-double-income-withdraw'}, format='json')
        self.assertEqual(request.data['code'], 0, request.data)
        withdrawal = request.data['data']['request']
        detail = reviewer.get(f"/api/admin/withdrawals/{withdrawal['id']}/")
        self.assertEqual(detail.data['code'], 0, detail.data)
        self.assertEqual((detail.data['data']['payee_account'], detail.data['data']['payee_name']),
                         ('isolated-account-20261102', '隔离测试'))
        saved = provider_client.get('/api/wallet/withdraw-accounts/').data['data']
        updated = provider_client.put('/api/wallet/withdraw-accounts/WECHAT/', {
            'payee_account': 'isolated-updated-20261102', 'payee_name': '隔离新资料',
            'expected_id': saved[0]['id'], 'expected_version': saved[0]['version']}, format='json')
        self.assertEqual(updated.data['code'], 0, updated.data)
        saved = provider_client.get('/api/wallet/withdraw-accounts/').data['data']
        self.assertEqual((saved[0]['payee_account'], saved[0]['payee_name']),
                         ('isolated-updated-20261102', '隔离新资料'))
        hold = holds[0]; hold.wallet.refresh_from_db()
        self.assertEqual((hold.wallet.balance, hold.wallet.frozen_amount), (hold.amount - amount, amount))
        url = f"/api/admin/withdrawals/{withdrawal['id']}/"
        claimed = reviewer.post(url + 'claim/', {}, format='json')
        self.assertEqual(claimed.data['code'], 0, claimed.data)
        approved = reviewer.post(url + 'approve/', {'payout_reference': 'ISOLATED-INCOME-WITHDRAW',
            'expected_claimed_at': claimed.data['data']['claimed_at']}, format='json')
        self.assertEqual(approved.data['code'], 0, approved.data)
        approved = reviewer.get(url)
        self.assertEqual(approved.data['code'], 0, approved.data)
        self.assertEqual(approved.data['data']['status'], 'APPROVED')
        hold.wallet.refresh_from_db()
        self.assertEqual((hold.wallet.balance, hold.wallet.frozen_amount), (hold.amount - amount, 0))
        # Older wallets may not have been linked to a business account yet.
        hold.wallet.account = None
        hold.wallet.save(update_fields=['account'])
        legacy_rows = reviewer.get('/api/admin/transactions/', {'keyword': providers[0].username}).data['data']['list']
        self.assertTrue(any(row['order'] == order.pk and row['username'] == providers[0].username for row in legacy_rows))

    def test_customer_wallet_opens_recharge_history_at_mobile_width(self):
        media = tempfile.TemporaryDirectory(prefix='xa-recharge-identity-')
        self.addCleanup(media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=media.name, REQUIRE_RECHARGE_PROOF=True))
        boss = make_user()
        account = get_or_create_account_for_legacy_user(boss); link_legacy_relations(boss, account)
        wallet = make_wallet(boss, balance=0); wallet.account = account; wallet.save(update_fields=['account'])
        staff = make_console_user(['wallet:view', 'wallet:recharge', 'wallet:adjust', 'transaction:view', 'recharge:view'])
        staff_account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        result = client.post('/api/admin/wallets/recharge/', {'account_id': account.pk, 'amount': 10000,
            'gift_amount': 1000, 'trade_no': 'ISOLATED-RECHARGE-20261102',
            'client_request_id': 'isolated-recharge-history', 'proof_image': screenshot()}, format='multipart')
        self.assertEqual(result.data['code'], 0, result.data)
        account.username = 'current-recharge-identity'; account.nickname = '现行充值客户'
        account.save(update_fields=['username', 'nickname'])
        boss.username = 'legacy-recharge-identity'; boss.nickname = '历史充值客户'
        boss.save(update_fields=['username', 'nickname'])
        wallets = client.get('/api/admin/wallets/', {'keyword': account.username}).data['data']['list']
        row = next(item for item in wallets if item['id'] == wallet.pk)
        self.assertEqual((row['username'], row['nickname']), (account.username, account.nickname))
        records = client.get('/api/admin/recharge-records/', {'wallet_id': wallet.pk}).data['data']['list']
        record = next(item for item in records if item['trade_no'] == 'ISOLATED-RECHARGE-20261102')
        self.assertEqual((record['amount'], record['gift_amount'], record['username'], record['nickname']),
                         (10000, 1000, account.username, account.nickname))

    def test_reward_current_identity_matches_provider_wallet_at_mobile_width(self):
        provider = make_provider(display_name='隔离奖励陪玩')
        account = get_or_create_account_for_legacy_user(provider); link_legacy_relations(provider, account)
        provider.escort_profile.account = account; provider.escort_profile.save(update_fields=['account'])
        wallet = make_wallet(provider, balance=0); wallet.account = account; wallet.save(update_fields=['account'])
        staff = make_console_user(['escort:view', 'escort:dispose', 'dispose:view'])
        staff_account = get_or_create_account_for_legacy_user(staff); link_legacy_relations(staff, staff_account)
        staff.admin_membership.account = staff_account; staff.admin_membership.save(update_fields=['account'])
        staff.nickname = '历史客服'; staff.save(update_fields=['nickname'])
        staff_account.nickname = '现行客服'; staff_account.save(update_fields=['nickname'])
        client = APIClient(); client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        reason = '隔离验收服务奖励'
        penalty_reason = '隔离验收服务罚款'
        account.username = 'current-reward-identity'; account.nickname = '现行奖励陪玩'
        account.save(update_fields=['username', 'nickname'])
        provider.username = 'legacy-reward-identity'; provider.nickname = '历史奖励陪玩'
        provider.save(update_fields=['username', 'nickname'])
        escorts = client.get('/api/admin/escorts/', {'keyword': account.username}).data['data']['list']
        self.assertTrue(any(item['id'] == provider.escort_profile.pk and item['username'] == account.username for item in escorts))
        rewarded = client.post(f'/api/admin/escorts/{provider.escort_profile.pk}/dispose/',
            {'dispose_type': 'REWARD', 'amount': 3000, 'reason': reason}, format='json')
        self.assertEqual(rewarded.data['code'], 0, rewarded.data)
        penalized = client.post(f'/api/admin/escorts/{provider.escort_profile.pk}/dispose/',
            {'dispose_type': 'PENALTY', 'amount': 1000, 'reason': penalty_reason}, format='json')
        self.assertEqual(penalized.data['code'], 0, penalized.data)
        wallet.refresh_from_db(); self.assertEqual(wallet.balance, 2000)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='REWARD', amount=3000).count(), 1)
        self.assertEqual(Transaction.objects.filter(wallet=wallet, tx_type='PENALTY', amount=-1000).count(), 1)
        records = client.get('/api/admin/dispose-records/', {'account_id': account.pk}).data['data']['list']
        self.assertEqual({row['operator_name'] for row in records}, {'现行客服'})
        # Historical rows may lack the newer account column while retaining
        # their original, explicitly bound wallet and transaction.
        from wallet.models import DisposeRecord
        DisposeRecord.objects.filter(pk__in=[row['id'] for row in records]).update(account=None)
        legacy_records = client.get('/api/admin/dispose-records/', {'account_id': account.pk}).data['data']['list']
        self.assertEqual({row['id'] for row in legacy_records}, {row['id'] for row in records})

    def test_provider_toggles_status_while_boss_checkout_remains_open(self):
        boss, provider = make_user(), make_provider(display_name='隔离陪玩', deposit_mode='EARNINGS', pass_tier='BLACK',
            pass_expires_at=timezone.now() + timedelta(days=1))
        boss_account = get_or_create_account_for_legacy_user(boss)
        provider_account = get_or_create_account_for_legacy_user(provider)
        for user, account in ((boss, boss_account), (provider, provider_account)):
            link_legacy_relations(user, account)
        provider.escort_profile.account = provider_account
        provider.escort_profile.save(update_fields=['account'])
        service = make_service(name='陪玩状态验收', price=10000)
        provider.escort_profile.service_items.add(service)
        make_wallet(boss, balance=50000)
        boss_client = APIClient(); boss_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(boss_account)[0])
        provider_client = APIClient(); provider_client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(provider_account)[0])
        quote_payload = {'service_id': service.pk, 'provider_id': provider_account.pk}
        quoted = boss_client.post('/api/orders/quote/', quote_payload, format='json')
        self.assertEqual(quoted.data['code'], 0, quoted.data)
        offline = provider_client.post('/api/users/escorts/status/', {'status': 'OFFLINE'}, format='json')
        self.assertEqual(offline.data['code'], 0, offline.data)
        blocked = boss_client.post('/api/orders/quote/', quote_payload, format='json')
        self.assertEqual(blocked.data['code'], 400, blocked.data)
        available = provider_client.post('/api/users/escorts/status/', {'status': 'AVAILABLE'}, format='json')
        self.assertEqual(available.data['code'], 0, available.data)
        restored = boss_client.post('/api/orders/quote/', quote_payload, format='json')
        self.assertEqual(restored.data['code'], 0, restored.data)
