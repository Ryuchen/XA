"""两套 ID 空间错充回归测试。

背景：``UserViewSet`` 列表返回的是 ``ClubAccount.id``，而 recharge / messages.push /
recharge-records / dispose-records 内部存、筛的是 legacy ``CustomUser.id``。两套 ID 空间
都是小整数自增，bare integer 无法区分，单个 ``user_id`` 字段既当 ClubAccount.id 又当
legacy id 就会"错充到同号的另一位用户"。

修复：前端用独立字段 ``account_id``（ClubAccount.id）透传，legacy ``CustomUser.id`` 仍走
``user_id`` 字段兼容。两者分字段传入，单向映射，从根本上消除错充。
"""

from django.contrib.auth import get_user_model
from django.test import override_settings

from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import (
    make_provider,
    make_console_user,
    make_superuser,
    make_user,
    make_wallet,
)
from users.models import EscortProfile
from wallet.models import DisposeRecord, RechargeRecord, Wallet

User = get_user_model()


def _ensure_account(legacy_user):
    """为 legacy 用户建好 ClubAccount + LegacyAccountMap（映射无条件创建）。"""
    return get_or_create_account_for_legacy_user(legacy_user)


class RechargeIdSpaceTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        make_user()  # 错位序号，避免 ClubAccount.id 与 legacy id 巧合相等
        self.boss = make_user()
        make_wallet(self.boss, balance=0)
        self.account = _ensure_account(self.boss)  # 目标 boss 的 ClubAccount
        self.boss2 = make_user()
        make_wallet(self.boss2, balance=0)

    def _recharge(self, payload):
        self.client.force_authenticate(self.admin)
        return self.client.post('/api/admin/wallets/recharge/', payload)

    def test_recharge_accepts_account_id(self):
        """前端传 ClubAccount.id（account_id）也能正确入账到对应 legacy 用户钱包。"""
        res = self._recharge({'account_id': self.account.id, 'amount': 10000})
        self.assertEqual(res.data['code'], 0)
        wallet = Wallet.objects.get(user=self.boss)
        self.assertEqual(wallet.balance, 10000)
        # 记录落的是 legacy 用户 id，不是 ClubAccount.id
        record = RechargeRecord.objects.get()
        self.assertEqual(record.user_id, self.boss.id)

    def test_recharge_account_id_does_not_mischarge_other_user(self):
        """传 ClubAccount.id 绝不能落到无关用户头上（错充防护）。"""
        self._recharge({'account_id': self.account.id, 'amount': 10000})
        self.assertFalse(RechargeRecord.objects.filter(user=self.boss2).exists())
        self.assertEqual(Wallet.objects.get(user=self.boss2).balance, 0)

    def test_recharge_backward_compat_legacy_id(self):
        """旧后台 / 内部工具仍传 legacy id（user_id）时行为不变。"""
        res = self._recharge({'user_id': self.boss.id, 'amount': 10000})
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 10000)

    def test_recharge_records_list_filter_by_account_id(self):
        """充值记录列表按 account_id（ClubAccount.id）筛选也要命中内部 legacy 存储。"""
        self._recharge({'account_id': self.account.id, 'amount': 10000})
        self.client.force_authenticate(self.admin)
        res = self.client.get(
            '/api/admin/recharge-records/', {'account_id': self.account.id}
        )
        self.assertEqual(res.data['code'], 0)
        rows = res.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user'], self.boss.id)

    def test_recharge_records_follow_business_account_identity_and_keep_legacy_fallback(self):
        response = self._recharge({'account_id': self.account.id, 'amount': 10000})
        self.assertEqual(response.data['code'], 0, response.data)
        wallet = Wallet.objects.get(user=self.boss)
        record = RechargeRecord.objects.get(user=self.boss)
        self.assertEqual((record.account_id, wallet.account_id), (self.account.id, self.account.id))
        self.boss.username = 'legacy-recharge-owner'
        self.boss.nickname = '历史客户'
        self.boss.phone = '13600001234'
        self.boss.save(update_fields=['username', 'nickname', 'phone'])
        self.account.username = 'current-recharge-owner'
        self.account.nickname = '现行客户'
        self.account.phone = '13700005678'
        self.account.save(update_fields=['username', 'nickname', 'phone'])
        self.client.force_authenticate(self.admin)
        url = '/api/admin/recharge-records/'
        response = self.client.get(url, {'wallet_id': wallet.id, 'account_id': self.account.id,
                                         'nickname': '现行', 'phone': '1370000'})
        rows = response.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['username'], rows[0]['nickname'], rows[0]['phone']),
                         ('current-recharge-owner', '现行客户', '13700005678'))
        for filters in ({'nickname': '历史客户'}, {'phone': '1360000'}, {'account_id': 'invalid'}):
            self.assertFalse(self.client.get(url, filters).data['data']['list'])
        # Simulate an old record created before account linkage; its legacy
        # identity remains discoverable from the historical wallet.
        record.account = None
        record.save(update_fields=['account'])
        response = self.client.get(url, {'wallet_id': wallet.id, 'account_id': self.account.id,
                                         'nickname': '历史客户', 'phone': '1360000'})
        rows = response.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['username'], 'legacy-recharge-owner')

    def test_unmapped_account_is_not_charged_to_same_numbered_legacy_user(self):
        collision = max(User.objects.order_by('-pk').values_list('pk', flat=True).first() or 0,
                        ClubAccount.objects.order_by('-pk').values_list('pk', flat=True).first() or 0) + 100
        unrelated = make_user(pk=collision)
        wallet = make_wallet(unrelated, balance=0)
        unmapped = ClubAccount.objects.create(pk=collision, username='unmapped-recharge-business',
                                              account_type=ClubAccount.AccountType.BOSS)
        result = self._recharge({'account_id': unmapped.pk, 'amount': 10000})
        self.assertEqual(result.data['code'], 404, result.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 0)
        self.assertFalse(RechargeRecord.objects.filter(user=unrelated).exists())
        # An older unlinked record owned by the coincidental legacy user must
        # not appear under the unrelated business account either.
        RechargeRecord.objects.create(user=unrelated, amount=100)
        rows = self.client.get('/api/admin/recharge-records/', {'account_id': unmapped.pk}).data['data']['list']
        self.assertEqual(rows, [])

    def test_explicit_wallet_link_allows_recharge_without_legacy_mapping(self):
        customer = make_user()
        account = ClubAccount.objects.create(username='wallet-linked-recharge-owner',
                                             account_type=ClubAccount.AccountType.BOSS)
        wallet = make_wallet(customer, balance=0)
        wallet.account = account; wallet.save(update_fields=['account'])
        response = self._recharge({'account_id': account.pk, 'amount': 10000})
        self.assertEqual(response.data['code'], 0, response.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 10000)
        self.assertEqual(RechargeRecord.objects.get(user=customer).account_id, account.pk)

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=False, REQUIRE_RECHARGE_PROOF=True)
    def test_historical_recharge_is_not_reassigned_by_stale_mapping(self):
        from tempfile import TemporaryDirectory
        from club_accounts.models import LegacyAccountMap
        from club_accounts.tokens import issue_account_tokens
        from orders.tests.test_release_flow import screenshot
        staff = make_console_user(['wallet:recharge', 'recharge:view'])
        staff_account = _ensure_account(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        with TemporaryDirectory(prefix='xa-recharge-identity-') as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post('/api/admin/wallets/recharge/', {'account_id': self.account.pk, 'amount': 10000,
                'trade_no': 'identity-recharge-proof', 'client_request_id': 'identity-recharge-proof',
                'proof_image': screenshot()}, format='multipart')
        self.assertEqual(response.data['code'], 0, response.data)
        record = RechargeRecord.objects.get(user=self.boss)
        record.account = None
        record.save(update_fields=['account'])
        other = ClubAccount.objects.create(username='remapped-recharge-owner',
                                           account_type=ClubAccount.AccountType.BOSS)
        LegacyAccountMap.objects.filter(legacy_user_id=self.boss.pk).update(account=other)
        LegacyAccountMap.objects.create(account=self.account, legacy_user_id=self.boss2.pk, legacy_role='CUSTOMER')
        url = '/api/admin/recharge-records/'
        self.assertEqual([row['id'] for row in self.client.get(url, {'account_id': self.account.pk}).data['data']['list']], [record.pk])
        self.assertEqual(self.client.get(url, {'account_id': other.pk}).data['data']['list'], [])


    def test_recharge_and_ledger_show_actual_operator_account(self):
        response = self._recharge({'account_id': self.account.id, 'amount': 10000})
        self.assertEqual(response.data['code'], 0, response.data)
        record = RechargeRecord.objects.get(user=self.boss)
        self.assertIsNotNone(record.operator_account_id)
        self.admin.nickname = '历史经办人'; self.admin.save(update_fields=['nickname'])
        operator = record.operator_account
        operator.nickname = '现行经办人'; operator.save(update_fields=['nickname'])
        self.client.force_authenticate(self.admin)
        rows = self.client.get('/api/admin/recharge-records/', {'account_id': self.account.pk}).data['data']['list']
        self.assertEqual(rows[0]['operator_name'], '现行经办人')
        transactions = self.client.get('/api/admin/transactions/', {'wallet_id': record.user.wallet.pk}).data['data']['list']
        self.assertEqual(next(row for row in transactions if row['id'] == record.recharge_tx_id)['operator_name'], '现行经办人')
        record.operator_account = None; record.save(update_fields=['operator_account'])
        from console.serializers import AdminRechargeRecordSerializer
        self.assertEqual(AdminRechargeRecordSerializer(RechargeRecord.objects.get(pk=record.pk)).data['operator_name'], '历史经办人')


class IdCollisionTest(APITestCase):
    """显式制造 ClubAccount.id 与某 legacy id 撞号的场景，验证两字段解析到不同（正确）的人。"""

    def setUp(self):
        self.admin = make_superuser()
        # 显式制造相同数字的不同身份；不依赖数据库回滚自增序列。
        collision_key = max(
            User.objects.order_by('-pk').values_list('pk', flat=True).first() or 0,
            ClubAccount.objects.order_by('-pk').values_list('pk', flat=True).first() or 0,
        ) + 100
        self.legacy4_user = make_user(pk=collision_key)
        make_wallet(self.legacy4_user, balance=0)
        self.boss = make_user()
        make_wallet(self.boss, balance=0)
        ClubAccount.objects.create(
            pk=collision_key, username=self.boss.username, password=self.boss.password,
            account_type=ClubAccount.AccountType.BOSS,
        )
        self.account = _ensure_account(self.boss)

    def test_account_id_and_user_id_resolve_to_different_users(self):
        account_id_num = self.account.id
        self.assertEqual(account_id_num, self.legacy4_user.id)
        self.assertNotEqual(self.legacy4_user.id, self.boss.id)

        # account_id → 解析为 boss，正确入账
        self._recharge({'account_id': account_id_num, 'amount': 10000})
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 10000)
        self.assertEqual(Wallet.objects.get(user=self.legacy4_user).balance, 0)

        # 同一数字当 user_id（legacy）传入 → 解析为另一位，绝不动 boss
        self._recharge({'user_id': account_id_num, 'amount': 5000})
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 10000)  # boss 不变
        self.assertEqual(Wallet.objects.get(user=self.legacy4_user).balance, 5000)  # 撞号用户入账

    def _recharge(self, payload):
        self.client.force_authenticate(self.admin)
        return self.client.post('/api/admin/wallets/recharge/', payload)


class MessagePushIdSpaceTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        make_user()  # 错位序号
        self.boss = make_user()
        self.account = _ensure_account(self.boss)
        self.boss2 = make_user()

    def _push(self, payload):
        self.client.force_authenticate(self.admin)
        return self.client.post('/api/admin/messages/push/', payload)

    def test_push_accepts_account_id(self):
        """推送接收人传 ClubAccount.id（recipient_account_ids）也要落到正确 legacy 用户。"""
        res = self._push({
            'title': '系统通知', 'preview': '您好', 'detail': '测试',
            'recipient_account_ids': [self.account.id],
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['count'], 1)
        from site_messages.models import Message
        msg = Message.objects.get()
        self.assertEqual(msg.recipient_id, self.boss.id)

    def test_push_account_id_does_not_mischarge_other_user(self):
        res = self._push({
            'title': 't', 'preview': 'p', 'detail': 'd',
            'recipient_account_ids': [self.account.id],
        })
        self.assertEqual(res.data['data']['count'], 1)
        from site_messages.models import Message
        self.assertFalse(Message.objects.filter(recipient_id=self.boss2.id).exists())

    def test_push_backward_compat_legacy_id(self):
        res = self._push({
            'title': 't', 'preview': 'p', 'detail': 'd',
            'recipient_ids': [self.boss.id],
        })
        self.assertEqual(res.data['code'], 0)
        self.assertEqual(res.data['data']['count'], 1)
        from site_messages.models import Message
        self.assertEqual(Message.objects.get().recipient_id, self.boss.id)


class DisposeRecordsIdSpaceTest(APITestCase):
    def setUp(self):
        self.admin = make_superuser()
        make_user()  # 错位序号
        self.provider = make_provider()
        make_wallet(self.provider, balance=10000)
        self.account = _ensure_account(self.provider)

    def test_dispose_records_list_filter_by_account_id(self):
        """奖惩记录列表按 account_id（ClubAccount.id）筛选也要命中。"""
        escort_id = EscortProfile.objects.get(user=self.provider).id
        self.client.force_authenticate(self.admin)
        res = self.client.post(
            f'/api/admin/escorts/{escort_id}/dispose/',
            {'dispose_type': 'REWARD', 'amount': 3000, 'reason': '优质服务'},
        )
        self.assertEqual(res.data['code'], 0)

        res = self.client.get(
            '/api/admin/dispose-records/', {'account_id': self.account.id}
        )
        self.assertEqual(res.data['code'], 0)
        rows = res.data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['user'], self.provider.id)
        self.assertEqual(
            DisposeRecord.objects.get().user_id, self.provider.id
        )

    def test_current_account_identity_and_historical_fallback(self):
        escort_id = EscortProfile.objects.get(user=self.provider).id
        self.client.force_authenticate(self.admin)
        response = self.client.post(f'/api/admin/escorts/{escort_id}/dispose/',
                                    {'dispose_type': 'REWARD', 'amount': 3000, 'reason': '隔离验收'})
        self.assertEqual(response.data['code'], 0, response.data)
        record = DisposeRecord.objects.get(user=self.provider)
        self.account.username = 'current-reward-provider'; self.account.nickname = '现行获奖陪玩'
        self.account.save(update_fields=['username', 'nickname'])
        self.provider.username = 'legacy-reward-provider'; self.provider.nickname = '历史获奖陪玩'
        self.provider.save(update_fields=['username', 'nickname'])
        url = '/api/admin/dispose-records/'
        rows = self.client.get(url, {'account_id': self.account.pk, 'keyword': '现行获奖'}).data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['username'], rows[0]['nickname']), ('current-reward-provider', '现行获奖陪玩'))
        self.assertFalse(self.client.get(url, {'keyword': 'legacy-reward-provider'}).data['data']['list'])
        record.account = None; record.save(update_fields=['account'])
        rows = self.client.get(url, {'account_id': self.account.pk, 'keyword': '历史获奖'}).data['data']['list']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['username'], 'legacy-reward-provider')

    def test_unmapped_same_number_account_does_not_find_legacy_rewards(self):
        collision = max(User.objects.order_by('-pk').values_list('pk', flat=True).first() or 0,
                        ClubAccount.objects.order_by('-pk').values_list('pk', flat=True).first() or 0) + 100
        legacy = make_provider(pk=collision)
        account = ClubAccount.objects.create(pk=collision, username='unmapped-reward-owner',
                                             account_type=ClubAccount.AccountType.PROVIDER)
        DisposeRecord.objects.create(user=legacy, dispose_type='REWARD', amount=100, reason='历史导入')
        self.client.force_authenticate(self.admin)
        rows = self.client.get('/api/admin/dispose-records/', {'account_id': account.pk}).data['data']['list']
        self.assertEqual(rows, [])

    @override_settings(LEGACY_FORCE_AUTH_COMPAT=False)
    def test_historical_reward_follows_wallet_after_legacy_mapping_changes(self):
        from club_accounts.models import LegacyAccountMap
        from club_accounts.tokens import issue_account_tokens
        staff = make_console_user(['escort:dispose', 'dispose:view'])
        staff_account = _ensure_account(staff)
        staff.admin_membership.account = staff_account
        staff.admin_membership.save(update_fields=['account'])
        profile = self.provider.escort_profile
        profile.account = self.account
        profile.save(update_fields=['account'])
        wallet = Wallet.objects.get(user=self.provider)
        wallet.account = self.account
        wallet.save(update_fields=['account'])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(staff_account)[0])
        response = self.client.post(f'/api/admin/escorts/{profile.pk}/dispose/', {
            'dispose_type': 'REWARD', 'amount': 3000, 'reason': '隔离奖惩记录',
            'client_request_id': 'historical-reward-identity',
        }, format='json')
        self.assertEqual(response.data['code'], 0, response.data)
        record = DisposeRecord.objects.get(user=self.provider)
        record.account = None
        record.save(update_fields=['account'])
        other = ClubAccount.objects.create(username='remapped-reward-owner', account_type=ClubAccount.AccountType.PROVIDER)
        LegacyAccountMap.objects.filter(legacy_user_id=self.provider.pk).update(account=other)
        another_provider = make_provider()
        LegacyAccountMap.objects.create(account=self.account, legacy_user_id=another_provider.pk, legacy_role='PROVIDER')
        url = '/api/admin/dispose-records/'
        self.assertEqual([row['id'] for row in self.client.get(url, {'account_id': self.account.pk}).data['data']['list']], [record.pk])
        self.assertEqual(self.client.get(url, {'account_id': other.pk}).data['data']['list'], [])
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(self.account)[0])
        benefits = self.client.get('/api/wallet/provider-benefits/').data['data']
        self.assertEqual([row['id'] for row in benefits['dispose_records']], [record.pk])
        self.assertEqual(benefits['summary']['total_reward'], 3000)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + issue_account_tokens(other)[0])
        self.assertEqual(self.client.get('/api/wallet/provider-benefits/').data['data']['dispose_records'], [])
