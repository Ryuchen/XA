"""两套 ID 空间错充回归测试。

背景：``UserViewSet`` 列表返回的是 ``ClubAccount.id``，而 recharge / messages.push /
recharge-records / dispose-records 内部存、筛的是 legacy ``CustomUser.id``。两套 ID 空间
都是小整数自增，bare integer 无法区分，单个 ``user_id`` 字段既当 ClubAccount.id 又当
legacy id 就会"错充到同号的另一位用户"。

修复：前端用独立字段 ``account_id``（ClubAccount.id）透传，legacy ``CustomUser.id`` 仍走
``user_id`` 字段兼容。两者分字段传入，单向映射，从根本上消除错充。
"""

from django.contrib.auth import get_user_model

from rest_framework.test import APITestCase

from club_accounts.models import ClubAccount
from club_accounts.services import get_or_create_account_for_legacy_user
from orders.tests.factories import (
    make_provider,
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


class IdCollisionTest(APITestCase):
    """显式制造 ClubAccount.id 与某 legacy id 撞号的场景，验证两字段解析到不同（正确）的人。"""

    def setUp(self):
        self.admin = make_superuser()
        # 先耗掉 ClubAccount 自增序号 1/2/3，使 boss 的 ClubAccount 落到 id=4，
        # 而 legacy 用户序号里也恰好有 id=4 的另一位——这就是"两套 ID 空间错充"的触发条件。
        for _ in range(3):
            u = make_user()
            make_wallet(u, balance=0)
            _ensure_account(u)
        self.legacy4_user = User.objects.get(id=4)  # 撞号对象：legacy id=4 的另一位用户
        make_wallet(self.legacy4_user, balance=0)
        self.boss = make_user()         # legacy id=5
        make_wallet(self.boss, balance=0)
        self.account = _ensure_account(self.boss)  # ClubAccount id=4 → 映射 legacy 5

    def test_account_id_and_user_id_resolve_to_different_users(self):
        account_id_num = self.account.id  # boss 的 ClubAccount.id（=4），与 legacy id=4 撞号
        self.assertEqual(account_id_num, 4)
        # ClubAccount.id=4 映射到的 legacy 用户是 boss（legacy 5），而非 legacy id=4 的那位
        self.assertNotEqual(self.legacy4_user.id, self.boss.id)

        # account_id=4 → 解析为 boss（legacy 5），正确入账
        self._recharge({'account_id': account_id_num, 'amount': 10000})
        self.assertEqual(Wallet.objects.get(user=self.boss).balance, 10000)
        self.assertEqual(Wallet.objects.get(user=self.legacy4_user).balance, 0)

        # 同一数字当 user_id（legacy）传入 → 解析为 legacy id=4 的那位，绝不动 boss
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
