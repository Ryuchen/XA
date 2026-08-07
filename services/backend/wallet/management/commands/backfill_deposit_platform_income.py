"""押金账务盘点（只读）——为历史押金的平台侧补记做基准核对。

背景：``wallet.views.DepositView`` 此前只扣陪玩钱包、累加
``EscortProfile.deposit_paid``、记一笔陪玩侧 ``DEPOSIT`` 负数流水，
**从不给平台钱包记配对入账**。这笔钱从陪玩余额里消失后在账面上没有落点，
全站 ``sum(wallet.balance)`` 凭空变少。修复已随 ``DEPOSIT_INCOME`` 枚举上线，
但历史数据还欠着平台侧那一半。

本命令**当前只做只读盘点**，``--commit`` 分支硬拒。为什么不直接把回补写进来：
补数的基准取决于「陪玩侧 DEPOSIT 流水合计」与「``deposit_paid`` 合计」对不对
得上。

* 对得上：说明历史押金全部走 ``DepositView``，按流水合计补平台侧即可；
* 对不上：说明有绕过 ``DepositView`` 的写入（后台「编辑陪玩」表单直改
  ``deposit_paid`` 就是其中一条，已在 ``AdminEscortSerializer`` 堵掉）。
  这种情况下两个数字谁是真相要人工判，不能闭眼按其中一个反推另一个——
  按 ``deposit_paid`` 补会把「客服手改出来的假押金」变成平台真金白银的入账，
  按流水补则会留下一批账实不符的陪玩。

所以流程是：先跑本命令拿三个数 → 人工确认基准 → 再实现 ``--commit``。

用法::

    # 只读盘点（默认），打印汇总 + 按差额倒序的 TOP-N 明细
    python manage.py backfill_deposit_platform_income
    python manage.py backfill_deposit_platform_income --top 50

    # 回补（当前硬拒，等盘点数字确认后再实现）
    python manage.py backfill_deposit_platform_income --commit
"""

import unicodedata

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum

from users.models import EscortProfile
from wallet.models import Transaction, Wallet


def _yuan(amount):
    """内部账务单位 -> 兴安币展示值（1 兴安币 = 10 单位）。"""
    return f'{amount / 10:g}'


def _display_width(text):
    """字符串在等宽终端里占的列数：CJK 全角字符算 2 列，其余算 1 列。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        for ch in str(text)
    )


def _pad(text, width, align='<'):
    """按**显示宽度**补空格对齐。

    不能直接用 ``f'{x:<16}'``：那是按字符数补齐的，中文昵称在终端里一个字
    占两列，明细表的列会整体错位，人工核对时基本没法扫。
    """
    text = str(text)
    padding = ' ' * max(width - _display_width(text), 0)
    return padding + text if align == '>' else text + padding


def _clip(text, width):
    """按显示宽度截断，避免超长昵称把整行挤歪。"""
    out = []
    used = 0
    for ch in str(text):
        ch_width = 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
        if used + ch_width > width:
            break
        out.append(ch)
        used += ch_width
    return ''.join(out)


class Command(BaseCommand):
    help = '押金账务盘点：核对陪玩侧 DEPOSIT 流水与 deposit_paid 是否对得上'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            help='只读盘点，不写任何数据（默认行为）',
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            default=False,
            help='执行平台侧回补（当前未实现，会直接报错退出）',
        )
        parser.add_argument(
            '--top',
            type=int,
            default=20,
            help='按差额绝对值倒序打印的明细条数，默认 20；传 0 不打印明细',
        )

    # -- 数据采集 ---------------------------------------------------------

    def _wallet_index(self):
        """返回 ``(by_account_id, by_user_id)`` 两张 ``-> wallet_id`` 索引。

        钱包同时挂 ``account`` 与 ``user`` 两个维度，历史数据里并非总是齐全，
        只按一边查会漏掉一批陪玩（``wallet.services.get_wallet`` 也是两边都试）。
        """
        by_account = {}
        by_user = {}
        for wallet_id, account_id, user_id in Wallet.objects.values_list(
            'id', 'account_id', 'user_id',
        ).iterator():
            if account_id:
                by_account[account_id] = wallet_id
            if user_id:
                by_user[user_id] = wallet_id
        return by_account, by_user

    def _deposit_by_wallet(self):
        """按钱包聚合陪玩侧押金流水：``{wallet_id: (笔数, 金额合计)}``。

        口径严格锁死 ``tx_type='DEPOSIT'``，**不含** ``DEPOSIT_INCOME``。
        新枚举是平台侧的配对入账，混进来会让「已缴押金」的标尺被自己补的数
        污染——那正是当初否决"复用 DEPOSIT 靠正负区分"的理由。
        """
        rows = (
            Transaction.objects
            .filter(tx_type=Transaction.TxType.DEPOSIT)
            .values('wallet_id')
            .annotate(n=Count('id'), total=Sum('amount'))
        )
        return {r['wallet_id']: (r['n'], r['total'] or 0) for r in rows}

    # -- 报告 -------------------------------------------------------------

    def _report(self, top):
        """打印盘点报告，返回汇总 dict（供测试断言）。"""
        by_account, by_user = self._wallet_index()
        deposit_by_wallet = self._deposit_by_wallet()

        tx_count = sum(n for n, _ in deposit_by_wallet.values())
        # DEPOSIT 流水全是负数（陪玩侧出账），取绝对值才是"缴了多少"。
        tx_outflow = -sum(total for _, total in deposit_by_wallet.values())

        paid_total = 0
        paid_nonzero = 0
        rows = []
        matched_wallets = set()

        profiles = EscortProfile.objects.values_list(
            'id', 'display_name', 'account_id', 'user_id', 'deposit_paid',
        )
        for pid, name, account_id, user_id, deposit_paid in profiles.iterator():
            paid_total += deposit_paid
            if deposit_paid:
                paid_nonzero += 1

            wallet_id = by_account.get(account_id) or by_user.get(user_id)
            n, total = deposit_by_wallet.get(wallet_id, (0, 0))
            if wallet_id is not None and wallet_id in deposit_by_wallet:
                matched_wallets.add(wallet_id)
            flow = -total  # 该陪玩通过流水缴出去的总额

            diff = flow - deposit_paid
            if diff or deposit_paid or n:
                rows.append({
                    'profile_id': pid,
                    'display_name': name or f'#{pid}',
                    'tx_count': n,
                    'flow': flow,
                    'deposit_paid': deposit_paid,
                    'diff': diff,
                })

        # 有 DEPOSIT 流水却挂不到任何陪玩档案的钱包：要么档案被删、要么
        # 钱包两个维度都断链。这类数据回补时会被漏掉，必须单独点名。
        orphan_wallets = [
            wid for wid in deposit_by_wallet if wid not in matched_wallets
        ]
        orphan_flow = -sum(
            deposit_by_wallet[wid][1] for wid in orphan_wallets
        )

        platform_rows = Transaction.objects.filter(
            tx_type=Transaction.TxType.DEPOSIT_INCOME,
        ).aggregate(n=Count('id'), total=Sum('amount'))
        platform_count = platform_rows['n'] or 0
        platform_total = platform_rows['total'] or 0

        summary = {
            'tx_count': tx_count,
            'tx_outflow': tx_outflow,
            'paid_total': paid_total,
            'paid_nonzero': paid_nonzero,
            'diff': tx_outflow - paid_total,
            'platform_count': platform_count,
            'platform_total': platform_total,
            'pending_backfill': tx_outflow - platform_total,
            'orphan_wallet_count': len(orphan_wallets),
            'orphan_flow': orphan_flow,
            'mismatched_count': sum(1 for r in rows if r['diff']),
        }

        w = self.stdout.write
        w('=== 押金账务盘点（只读，未写入任何数据）===')
        w('')
        w(f'[1] 陪玩侧 DEPOSIT 流水：{tx_count} 笔，'
          f'出账合计 {tx_outflow}（{_yuan(tx_outflow)} 兴安币）')
        w(f'[2] EscortProfile.deposit_paid 合计：{paid_total}'
          f'（{_yuan(paid_total)} 兴安币），其中非零 {paid_nonzero} 名陪玩')
        w(f'[3] 差额 [1]-[2]：{summary["diff"]}'
          f'（{_yuan(summary["diff"])} 兴安币）')
        if summary['diff'] == 0:
            w(self.style.SUCCESS(
                '    -> 对得上：历史押金均由 DepositView 写入，'
                '可按 [1] 作为平台侧回补基准。'
            ))
        else:
            w(self.style.WARNING(
                '    -> 对不上：存在绕过 DepositView 的 deposit_paid 写入'
                '（后台表单直改已堵，但历史数据留在库里）。'
            ))
            w(self.style.WARNING(
                '       回补基准需人工判定，不要按其中任一数字直接反推另一个。'
            ))
        w('')
        w(f'[4] 平台侧 DEPOSIT_INCOME 流水：{platform_count} 笔，'
          f'入账合计 {platform_total}（{_yuan(platform_total)} 兴安币）')
        w(f'[5] 待回补金额 [1]-[4]：{summary["pending_backfill"]}'
          f'（{_yuan(summary["pending_backfill"])} 兴安币）')
        w('    说明：[4] 含修复上线后新产生的正常入账，回补只需补 [5] 这部分；'
          '重复执行时 [5] 会收敛到 0，这就是幂等的判据。')

        if orphan_wallets:
            w('')
            w(self.style.WARNING(
                f'[!] {len(orphan_wallets)} 个钱包有 DEPOSIT 流水但挂不到陪玩档案，'
                f'涉及 {orphan_flow}（{_yuan(orphan_flow)} 兴安币）。'
            ))
            w(self.style.WARNING(
                f'    wallet_id: {sorted(orphan_wallets)[:20]}'
                + ('（仅列前 20）' if len(orphan_wallets) > 20 else '')
            ))

        if top:
            mismatched = sorted(
                (r for r in rows if r['diff']),
                key=lambda r: abs(r['diff']),
                reverse=True,
            )
            w('')
            if not mismatched:
                w('明细：逐个陪玩核对全部一致，无差额条目。')
            else:
                w(f'差额明细（共 {len(mismatched)} 名对不上，'
                  f'按差额绝对值倒序取前 {min(top, len(mismatched))}）：')
                w(f'{_pad("档案ID", 8, ">")}  {_pad("陪玩", 20)}'
                  f'{_pad("笔数", 6, ">")}{_pad("流水出账", 14, ">")}'
                  f'{_pad("已缴押金", 14, ">")}{_pad("差额", 14, ">")}')
                for r in mismatched[:top]:
                    w(f'{_pad(r["profile_id"], 8, ">")}  '
                      f'{_pad(_clip(r["display_name"], 20), 20)}'
                      f'{_pad(r["tx_count"], 6, ">")}{_pad(r["flow"], 14, ">")}'
                      f'{_pad(r["deposit_paid"], 14, ">")}'
                      f'{_pad(r["diff"], 14, ">")}')

        return summary

    # -- 入口 -------------------------------------------------------------

    def handle(self, *args, **options):
        top = max(int(options.get('top') or 0), 0)
        summary = self._report(top)

        if options.get('commit'):
            # 故意放在报告之后：执行的人能同时看到数字和拒绝理由，
            # 不用再跑一次 dry-run 才知道现在是什么状况。
            raise CommandError(
                '--commit 尚未启用。平台侧回补的基准取决于上面 [3] 的差额，'
                '必须先由人工确认「以流水为准还是以 deposit_paid 为准」，'
                '确认结论后再实现回补分支。当前请只使用只读盘点。'
            )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'盘点完成（只读）：{summary["mismatched_count"]} 名陪玩账实不符，'
            f'待回补 {summary["pending_backfill"]}'
            f'（{_yuan(summary["pending_backfill"])} 兴安币）。'
        ))
