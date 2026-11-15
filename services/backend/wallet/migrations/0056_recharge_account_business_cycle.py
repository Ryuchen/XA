from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0055_incomehold_freeze_transaction')]
    operations = [migrations.AddField(model_name='rechargerecord', name='account_business_cycle',
                                      field=models.PositiveIntegerField(blank=True, editable=False, null=True))]
