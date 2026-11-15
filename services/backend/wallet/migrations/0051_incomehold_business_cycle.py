from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0050_incomeaftersaleclosureevidence')]

    operations = [
        migrations.AddField(model_name='incomehold', name='account_business_cycle',
                            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
    ]
