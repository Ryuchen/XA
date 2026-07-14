from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0011_alter_transaction_tx_type_refund')]

    operations = [
        migrations.AddField(
            model_name='withdrawrequest', name='payout_reference',
            field=models.CharField(
                blank=True, default='', max_length=100,
                help_text='实际打款的渠道流水号或人工转账凭证编号',
            ),
        ),
        migrations.AddField(
            model_name='withdrawrequest', name='paid_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
