from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0262_addon_handover_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderrefund',
            name='customer_transaction',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                       related_name='ordinary_customer_refund', to='wallet.transaction'),
        ),
    ]
