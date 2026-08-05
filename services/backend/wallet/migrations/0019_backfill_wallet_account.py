"""回填钱包的 ClubAccount 维度，并检出需要人工处理的脏数据。

背景：Wallet 同时挂 user / account 两个 OneToOne，历史代码两套维度混用，
留下大量 account=NULL 的钱包。这类钱包会让 account 维度的访问走创建分支、
撞上 user 唯一约束报 500，用户从此无法使用钱包。

本迁移做两件事：
  1. 按 LegacyAccountMap 把 account=NULL 的钱包补齐 account_id
  2. 跳过会造成冲突的记录（目标 account 已被另一个钱包占用），
     并在日志中列出，交由人工合并 —— 绝不自动挪动资金
"""

from django.db import migrations


def backfill_wallet_account(apps, schema_editor):
    Wallet = apps.get_model('wallet', 'Wallet')
    LegacyAccountMap = apps.get_model('club_accounts', 'LegacyAccountMap')

    taken = set(
        Wallet.objects.exclude(account_id=None).values_list('account_id', flat=True)
    )
    mapping = dict(
        LegacyAccountMap.objects.values_list('legacy_user_id', 'account_id')
    )

    fixed = 0
    conflicts = []
    pending = Wallet.objects.filter(account_id=None).only('id', 'user_id')
    for wallet in pending.iterator(chunk_size=500):
        account_id = mapping.get(wallet.user_id)
        if account_id is None:
            # 平台系统账户等没有业务账号的钱包，保持 NULL 属正常状态
            continue
        if account_id in taken:
            conflicts.append((wallet.id, wallet.user_id, account_id))
            continue
        Wallet.objects.filter(pk=wallet.pk).update(account_id=account_id)
        taken.add(account_id)
        fixed += 1

    if fixed:
        print(f'\n  [wallet] 已回填 account 维度的钱包：{fixed} 条')
    if conflicts:
        print(
            f'  [wallet] 检出 {len(conflicts)} 条冲突钱包，需人工合并资金后再回填：'
        )
        for wallet_id, user_id, account_id in conflicts[:20]:
            print(
                f'    - 钱包#{wallet_id} (user={user_id}) 目标 account={account_id} 已被占用'
            )
        if len(conflicts) > 20:
            print(f'    ... 其余 {len(conflicts) - 20} 条略')


def noop_reverse(apps, schema_editor):
    """回填不可逆：不清空 account，避免再次制造 NULL 维度。"""


class Migration(migrations.Migration):

    dependencies = [
        ('wallet', '0018_disposerecord_dispose_account_type_idx_and_more'),
        ('club_accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_wallet_account, noop_reverse),
    ]
