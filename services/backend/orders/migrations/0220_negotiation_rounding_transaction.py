from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0219_ordinary_refund_rounding_transaction'),
        ('wallet', '0049_refunddepositcashrepayment'),
    ]

    operations = [migrations.AddField(
        model_name='negotiationexecution', name='rounding_transaction',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                  related_name='+', to='wallet.transaction'),
    )]
