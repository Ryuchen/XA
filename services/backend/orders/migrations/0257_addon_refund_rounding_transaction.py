from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0256_application_business_cycles'), ('wallet', '0001_initial')]

    operations = [migrations.AddField(
        model_name='orderaddonrefund', name='rounding_transaction',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                   related_name='+', to='wallet.transaction'),
    )]
