"""新增 ``DEPOSIT_INCOME`` 押金收入类型（押金的平台侧配对入账）。

本迁移只动 ``tx_type`` 的 choices 与 ``transaction_type_valid`` 白名单，
**不回补历史数据**：历史押金的平台侧补记走一次性 management command
``backfill_deposit_platform_income``，那边必须先人工核对盘点数字再执行。
把回补写进迁移的话，`migrate` 一跑就无条件改钱，没有复核窗口。

生成方式与 0020_add_withdraw_tax_and_adjust_tx_types 一致。
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('club_accounts', '0002_clubaccount_club_account_type_active_idx_and_more'),
        ('orders', '0025_backfill_gift_order_accounts'),
        ('wallet', '0021_idempotencykey'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='transaction',
            name='transaction_type_valid',
        ),
        migrations.AlterField(
            model_name='transaction',
            name='tx_type',
            field=models.CharField(choices=[('TOPUP', '充值'), ('PAY', '支付'), ('INCOME', '收益'), ('WITHDRAW', '提现'), ('REWARD', '奖励'), ('GIFT', '赠送'), ('PENALTY', '罚款'), ('DEPOSIT', '押金'), ('DEPOSIT_INCOME', '押金收入'), ('SHOP_INCOME', '平台收入'), ('REFUND', '订单退款'), ('PASS_PURCHASE', '通行证购买'), ('WITHDRAW_TAX', '提现税费'), ('ADJUST', '后台调账')], db_index=True, max_length=20),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(condition=models.Q(('tx_type__in', ['TOPUP', 'PAY', 'INCOME', 'WITHDRAW', 'REWARD', 'GIFT', 'PENALTY', 'DEPOSIT', 'DEPOSIT_INCOME', 'SHOP_INCOME', 'REFUND', 'PASS_PURCHASE', 'WITHDRAW_TAX', 'ADJUST'])), name='transaction_type_valid'),
        ),
    ]
