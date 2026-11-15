from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0051_incomehold_business_cycle')]

    operations = [
        migrations.AddField(model_name='withdrawrequest', name='account_business_cycle',
                            field=models.PositiveIntegerField(null=True, blank=True, editable=False)),
    ]
