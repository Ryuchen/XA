from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0268_addon_refund_cross_pending_plan'), ('wallet', '0001_initial')]

    operations = [migrations.AddField(
        model_name='pointsnegotiationposting', name='rounding_transaction',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                   related_name='+', to='wallet.transaction'),
    )]
