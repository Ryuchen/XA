import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0054_transaction_deposit_cycle')]

    operations = [migrations.AddField(
        model_name='incomehold', name='freeze_transaction',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                  related_name='created_income_hold', to='wallet.transaction'),
    )]
