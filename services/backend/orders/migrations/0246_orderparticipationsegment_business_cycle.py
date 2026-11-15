from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0245_orderprovider_business_cycle')]

    operations = [
        migrations.AddField(model_name='orderparticipationsegment', name='provider_business_cycle',
                            field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
    ]
