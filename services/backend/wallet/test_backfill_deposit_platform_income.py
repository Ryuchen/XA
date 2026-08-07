"""``backfill_deposit_platform_income`` 只读盘点命令的单元测试。

这条命令是历史押金平台侧补记的**前置核对工具**，它自己不写库。测试要守住
三件事：

1. 三个关键数字（DEPOSIT 流水合计 / deposit_paid 合计 / 差额）算得对，
   且差额为零与不为零时给出的结论不同 —— 结论错了会误导执行人闭眼补数；
2. 口径隔离：平台侧 ``DEPOSIT_INCOME`` 绝不能混进「陪玩已缴押金」的标尺，
   否则补一笔数标尺就跟着涨一笔，永远追不上自己；
3. ``--commit`` 必须硬拒，且拒之前先把报告打出来。
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from orders.tests.factories import make_provider, make_user, make_wallet
from users.models import EscortProfile
from wallet.management.commands.backfill_deposit_platform_income import (
    _display_width,
)
from wallet.models import Transaction, Wallet, get_platform_wallet

COMMAND = 'backfill_deposit_platform_income'


class BackfillDepositPlatformIncomeTest(TestCase):
    """只读盘点命令：数字正确性、口径隔离、--commit 硬拒。"""

    def _run(self, *args):
        """执行命令并返回 stdout 文本。

        显式 ``no_color=True``：``BaseCommand`` 默认按 ``sys.stdout.isatty()``
        决定是否上色，在终端里跑测试会把 ANSI 转义码混进断言目标。
        """
        out = StringIO()
        call_command(COMMAND, *args, stdout=out, no_color=True)
        return out.getvalue()

    # -- 夹具 -----------------------------------------------------------

    def _pay_deposit(self, provider, amount, *, sync_profile=True):
        """模拟一次陪玩侧押金出账。

        Args:
            provider: 陪玩 ``User``。
            amount: 缴纳金额（内部账务单位，正数）。
            sync_profile: True 表示同步累加 ``deposit_paid``（正常路径）；
                False 表示只落流水不动档案，用于构造账实不符。
        """
        wallet = Wallet.objects.get(user=provider)
        balance_before = wallet.balance
        wallet.balance = max(balance_before - amount, 0)
        wallet.save(update_fields=['balance'])
        Transaction.objects.create(
            wallet=wallet,
            amount=-amount,
            tx_type=Transaction.TxType.DEPOSIT,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=Transaction.Status.SUCCESS,
            remark='缴纳押金',
        )
        if sync_profile:
            profile = EscortProfile.objects.get(user=provider)
            profile.deposit_paid += amount
            profile.save(update_fields=['deposit_paid'])

    def _credit_platform(self, amount):
        """模拟一笔平台侧押金入账（修复上线后产生的正常流水）。"""
        wallet = get_platform_wallet()
        balance_before = wallet.balance
        wallet.balance += amount
        wallet.save(update_fields=['balance'])
        Transaction.objects.create(
            wallet=wallet,
            amount=amount,
            tx_type=Transaction.TxType.DEPOSIT_INCOME,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=Transaction.Status.SUCCESS,
            remark='收取押金',
        )
        return wallet

    # -- 汇总数字 -------------------------------------------------------

    def test_dry_run_reports_matched_totals(self):
        """流水与 deposit_paid 完全一致时：差额为 0，结论是「对得上」。"""
        provider = make_provider(deposit_required=50000, display_name='对得上的小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)

        out = self._run()

        self.assertIn('=== 押金账务盘点（只读，未写入任何数据）===', out)
        self.assertIn('[1] 陪玩侧 DEPOSIT 流水：1 笔，出账合计 30000（3000 兴安币）', out)
        self.assertIn('[2] EscortProfile.deposit_paid 合计：30000（3000 兴安币），其中非零 1 名陪玩', out)
        self.assertIn('[3] 差额 [1]-[2]：0（0 兴安币）', out)
        self.assertIn('对得上', out)
        self.assertNotIn('对不上', out)
        # 平台侧一分没记，整笔 30000 都待回补
        self.assertIn('[4] 平台侧 DEPOSIT_INCOME 流水：0 笔，入账合计 0（0 兴安币）', out)
        self.assertIn('[5] 待回补金额 [1]-[4]：30000（3000 兴安币）', out)
        self.assertIn('明细：逐个陪玩核对全部一致，无差额条目。', out)
        self.assertIn('0 名陪玩账实不符', out)

    def test_dry_run_flags_mismatch_and_lists_detail(self):
        """存在绕过 DepositView 的 deposit_paid 时：判「对不上」并点名到人。"""
        clean = make_provider(deposit_required=50000, display_name='走正门的小张')
        make_wallet(clean, balance=80000)
        self._pay_deposit(clean, 30000)

        # 后台表单直改出来的假押金：有 deposit_paid，没有任何流水
        forged = make_provider(deposit_required=50000, display_name='被直改的老王')
        make_wallet(forged, balance=80000)
        profile = EscortProfile.objects.get(user=forged)
        profile.deposit_paid = 50000
        profile.save(update_fields=['deposit_paid'])

        out = self._run()

        self.assertIn('[1] 陪玩侧 DEPOSIT 流水：1 笔，出账合计 30000（3000 兴安币）', out)
        self.assertIn('[2] EscortProfile.deposit_paid 合计：80000（8000 兴安币），其中非零 2 名陪玩', out)
        self.assertIn('[3] 差额 [1]-[2]：-50000（-5000 兴安币）', out)
        self.assertIn('对不上', out)
        self.assertIn('回补基准需人工判定', out)
        self.assertNotIn('对得上', out)

        # 明细只列对不上的那一个，走正门的不该出现在差额清单里
        self.assertIn('差额明细（共 1 名对不上', out)
        self.assertIn('被直改的老王', out)
        self.assertIn('-50000', out)
        self.assertIn('1 名陪玩账实不符', out)
        detail = out.split('差额明细')[1]
        self.assertNotIn('走正门的小张', detail)

    def test_deposit_income_not_counted_as_provider_deposit(self):
        """口径隔离：平台侧入账只进 [4]，绝不能污染 [1] 这把标尺。"""
        provider = make_provider(deposit_required=50000, display_name='小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)
        self._credit_platform(30000)

        out = self._run()

        # [1] 仍然只有陪玩那 1 笔；若把 DEPOSIT_INCOME 混进来会变成 2 笔 / 合计 0
        self.assertIn('[1] 陪玩侧 DEPOSIT 流水：1 笔，出账合计 30000（3000 兴安币）', out)
        self.assertIn('[3] 差额 [1]-[2]：0（0 兴安币）', out)
        self.assertIn('[4] 平台侧 DEPOSIT_INCOME 流水：1 笔，入账合计 30000（3000 兴安币）', out)
        # 平台侧补齐后待回补收敛到 0，这就是命令自称的幂等判据
        self.assertIn('[5] 待回补金额 [1]-[4]：0（0 兴安币）', out)

    def test_partially_backfilled_reports_remaining_gap(self):
        """平台侧补了一半：[5] 只报剩下的缺口，不重复计已补部分。"""
        provider = make_provider(deposit_required=50000, display_name='小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)
        self._pay_deposit(provider, 20000)
        self._credit_platform(30000)

        out = self._run()

        self.assertIn('[1] 陪玩侧 DEPOSIT 流水：2 笔，出账合计 50000（5000 兴安币）', out)
        self.assertIn('[4] 平台侧 DEPOSIT_INCOME 流水：1 笔，入账合计 30000（3000 兴安币）', out)
        self.assertIn('[5] 待回补金额 [1]-[4]：20000（2000 兴安币）', out)

    def test_orphan_wallet_is_called_out(self):
        """有 DEPOSIT 流水却挂不到陪玩档案的钱包必须单独点名，别被静默漏掉。"""
        stranger = make_user()  # 普通用户，没有 EscortProfile
        make_wallet(stranger, balance=80000)
        wallet = Wallet.objects.get(user=stranger)
        Transaction.objects.create(
            wallet=wallet,
            amount=-12000,
            tx_type=Transaction.TxType.DEPOSIT,
            balance_before=80000,
            balance_after=68000,
            status=Transaction.Status.SUCCESS,
            remark='历史遗留押金',
        )

        out = self._run()

        self.assertIn('1 个钱包有 DEPOSIT 流水但挂不到陪玩档案', out)
        self.assertIn('涉及 12000（1200 兴安币）', out)
        self.assertIn(f'wallet_id: [{wallet.pk}]', out)

    # -- 明细开关 -------------------------------------------------------

    def test_top_zero_hides_detail(self):
        """--top 0：只要汇总，不打明细（库大时刷屏没意义）。"""
        provider = make_provider(deposit_required=50000, display_name='被直改的老王')
        make_wallet(provider, balance=80000)
        profile = EscortProfile.objects.get(user=provider)
        profile.deposit_paid = 50000
        profile.save(update_fields=['deposit_paid'])

        out = self._run('--top', '0')

        self.assertIn('[3] 差额 [1]-[2]：-50000（-5000 兴安币）', out)
        self.assertNotIn('差额明细', out)
        self.assertNotIn('被直改的老王', out)

    def test_top_limits_rows_and_sorts_by_abs_diff(self):
        """--top N：按差额**绝对值**倒序截断，先看金额最大的那批。

        故意让两个方向的差额同时存在，否则「按绝对值倒序」和「按带符号值
        正序」会给出一样的顺序，排序口径写反了也测不出来：

        * ``流水多的老李``：有 90000 流水、``deposit_paid`` 被清零 → 差额 +90000
        * ``被直改的老王``：无流水、``deposit_paid`` 50000        → 差额 -50000
        """
        big = make_provider(deposit_required=100000, display_name='流水多的老李')
        make_wallet(big, balance=100000)
        self._pay_deposit(big, 90000, sync_profile=False)

        small = make_provider(deposit_required=100000, display_name='被直改的老王')
        make_wallet(small, balance=100000)
        profile = EscortProfile.objects.get(user=small)
        profile.deposit_paid = 50000
        profile.save(update_fields=['deposit_paid'])

        out = self._run('--top', '1')
        self.assertIn('差额明细（共 2 名对不上，按差额绝对值倒序取前 1）', out)
        detail = out.split('差额明细')[1]
        self.assertIn('流水多的老李', detail)
        self.assertNotIn('被直改的老王', detail)

        # 不截断时两条都在，且顺序仍是绝对值大的在前
        full = self._run('--top', '5').split('差额明细')[1]
        self.assertLess(full.index('流水多的老李'), full.index('被直改的老王'))

    def test_detail_table_columns_stay_aligned(self):
        """明细表按**显示宽度**对齐，长短不一的中文昵称不会把列挤歪。

        中文字符在等宽终端占两列，用 ``f'{x:<16}'`` 这种按字符数补齐的写法，
        表格会整体错位、人工核对时基本没法扫。这条用例钉住「表头与每一行的
        显示宽度完全相等」，防止有人改回按字符数补齐。
        """
        long_name = '名字特别长到必须被截断的陪玩老李同学'
        for name, paid in (
            ('王', 1000),
            (long_name, 2000),
            ('AB混排Cd', 3000),
        ):
            provider = make_provider(deposit_required=100000, display_name=name)
            make_wallet(provider, balance=100000)
            profile = EscortProfile.objects.get(user=provider)
            profile.deposit_paid = paid
            profile.save(update_fields=['deposit_paid'])

        out = self._run()
        lines = out.splitlines()
        header_idx = next(i for i, line in enumerate(lines) if '档案ID' in line)
        table = lines[header_idx:header_idx + 4]  # 表头 + 3 行明细
        self.assertEqual(len(table), 4)

        widths = {_display_width(line) for line in table}
        self.assertEqual(
            len(widths), 1,
            f'明细表列宽不一致：{[(line, _display_width(line)) for line in table]}',
        )

        # 超长昵称按显示宽度截到 20 列 = 10 个汉字（不是 20 个字符），不挤歪整行
        self.assertIn('名字特别长到必须被截', out)
        self.assertNotIn('名字特别长到必须被截断', out)
        self.assertNotIn(long_name, out)

    # -- 写入闸门 -------------------------------------------------------

    def test_commit_is_rejected(self):
        """--commit 硬拒：回补基准没人工确认之前，不许这条命令碰钱。"""
        provider = make_provider(deposit_required=50000, display_name='小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)

        with self.assertRaises(CommandError) as ctx:
            self._run('--commit')

        self.assertIn('--commit 尚未启用', str(ctx.exception))
        self.assertIn('必须先由人工确认', str(ctx.exception))

    def test_commit_prints_report_before_refusing(self):
        """拒绝之前先把报告打出来：执行人不用再补跑一次 dry-run 才知道现状。"""
        provider = make_provider(deposit_required=50000, display_name='小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)

        out = StringIO()
        with self.assertRaises(CommandError):
            call_command(COMMAND, '--commit', stdout=out, no_color=True)

        printed = out.getvalue()
        self.assertIn('[3] 差额 [1]-[2]：0（0 兴安币）', printed)
        self.assertIn('[5] 待回补金额 [1]-[4]：30000（3000 兴安币）', printed)
        # 报告打完就退出，不该出现只读盘点的收尾行（那是没有 --commit 才走的）
        self.assertNotIn('盘点完成（只读）', printed)

    def test_dry_run_writes_nothing(self):
        """只读盘点跑完，库里一个字节都不该变。"""
        provider = make_provider(deposit_required=50000, display_name='小张')
        make_wallet(provider, balance=80000)
        self._pay_deposit(provider, 30000)
        platform_wallet = get_platform_wallet()

        snapshot = {
            'tx_count': Transaction.objects.count(),
            'provider_balance': Wallet.objects.get(user=provider).balance,
            'deposit_paid': EscortProfile.objects.get(user=provider).deposit_paid,
            'platform_balance': platform_wallet.balance,
        }

        self._run()
        self._run('--dry-run')

        platform_wallet.refresh_from_db()
        self.assertEqual(Transaction.objects.count(), snapshot['tx_count'])
        self.assertEqual(
            Wallet.objects.get(user=provider).balance, snapshot['provider_balance']
        )
        self.assertEqual(
            EscortProfile.objects.get(user=provider).deposit_paid,
            snapshot['deposit_paid'],
        )
        self.assertEqual(platform_wallet.balance, snapshot['platform_balance'])
        self.assertFalse(
            Transaction.objects.filter(
                tx_type=Transaction.TxType.DEPOSIT_INCOME
            ).exists()
        )
