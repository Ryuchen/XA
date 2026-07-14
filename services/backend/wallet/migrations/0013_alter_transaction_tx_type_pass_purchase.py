from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0012_withdraw_payout_audit')]

    operations = [
        migrations.AlterField(
            model_name='transaction', name='tx_type',
            field=models.CharField(choices=[
                ('TOPUP', '充值'), ('PAY', '支付'), ('INCOME', '收益'),
                ('WITHDRAW', '提现'), ('REWARD', '奖励'), ('GIFT', '赠送'),
                ('PENALTY', '罚款'), ('DEPOSIT', '押金'),
                ('SHOP_INCOME', '平台收入'), ('REFUND', '订单退款'),
                ('PASS_PURCHASE', '通行证购买'),
            ], db_index=True, max_length=20),
        ),
    ]
