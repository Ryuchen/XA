"""回补礼物打赏单缺失的 account 维度。

背景
----
``TipOrderView`` 在创建礼物单时只写了 legacy user 外键
（customer / provider / inviter），三个 ``*_account`` 字段一直是 NULL。
后台所有按 ClubAccount 维度做的统计 —— 陪玩收益榜、老板消费榜、
邀请人分佣报表 —— 都会整批漏掉打赏流水：钱确实到账了，报表上却查无此单。

0022 那次全量回补跑在本 bug 修复之前，此后新产生的礼物单依旧缺维度，
因此这里再跑一次。只填 NULL，不覆盖已有值，可安全重复执行。

手写迁移（本机无 Django 运行环境，无法 makemigrations）；本迁移只有数据操作，
没有 schema 变更。
"""

from django.db import migrations

# (account 字段, 对应的 legacy user 字段)
ACCOUNT_RELATIONS = (
    ('customer_account_id', 'customer_id'),
    ('provider_account_id', 'provider_id'),
    ('inviter_account_id', 'inviter_id'),
)

# 单批更新的映射条数，避免一次性拼出超长 SQL 拖垮 MySQL。
BATCH_SIZE = 500


def backfill_order_accounts(apps, schema_editor):
    """按 legacy user -> account 映射回补订单上缺失的 account 外键。"""
    LegacyAccountMap = apps.get_model('club_accounts', 'LegacyAccountMap')
    Order = apps.get_model('orders', 'Order')
    database = schema_editor.connection.alias

    mappings = list(
        LegacyAccountMap.objects.using(database).values_list(
            'legacy_user_id', 'account_id',
        )
    )
    if not mappings:
        return

    for account_field, legacy_field in ACCOUNT_RELATIONS:
        # 按 account_id 聚合 legacy_user_id，把「一个账户对应的所有旧用户」
        # 合成一条 UPDATE ... WHERE legacy_field IN (...)，减少往返次数。
        grouped = {}
        for legacy_user_id, account_id in mappings:
            grouped.setdefault(account_id, []).append(legacy_user_id)

        for account_id, legacy_ids in grouped.items():
            for start in range(0, len(legacy_ids), BATCH_SIZE):
                chunk = legacy_ids[start:start + BATCH_SIZE]
                Order.objects.using(database).filter(
                    **{
                        f'{legacy_field}__in': chunk,
                        # 只补空值，绝不覆盖已经写对的数据
                        f'{account_field}__isnull': True,
                    }
                ).update(**{account_field: account_id})


def noop_reverse(apps, schema_editor):
    """不可逆：回补的是本就该有的数据，清空只会把 bug 重新引回来。"""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('club_accounts', '0002_clubaccount_club_account_type_active_idx_and_more'),
        ('orders', '0024_order_order_split_not_exceed_amount'),
    ]

    operations = [
        migrations.RunPython(backfill_order_accounts, noop_reverse),
    ]
