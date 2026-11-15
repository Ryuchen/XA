from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0206_ordergrabpoolopeningintent')]

    operations = [
        migrations.AddField(
            model_name='ordergrabreceiptprobe',
            name='actor_business_cycle',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name='ordergrabreceiptprobe',
            name='actor_cycle_guarded',
            field=models.BooleanField(default=False, editable=False),
        ),
    ]
