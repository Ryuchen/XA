from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0022_add_deposit_income_tx_type')]
    operations = [
        migrations.AddField(
            model_name='rechargerecord', name='receipt_key',
            field=models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False),
        ),
    ]
