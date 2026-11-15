from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0186_orderaddonhandover_orderaddonhandoverdecision_and_more')]

    operations = [
        migrations.AlterField(
            model_name='orderaddonsettlement', name='payment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='settlements', to='orders.orderaddonpayment'),
        ),
        migrations.AddField(
            model_name='orderaddonsettlement', name='segment',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='orders.orderparticipationsegment'),
        ),
        migrations.AddConstraint(
            model_name='orderaddonsettlement',
            constraint=models.UniqueConstraint(fields=('payment', 'segment'),
                                               name='addon_settlement_payment_segment_unique'),
        ),
    ]
