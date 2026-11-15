from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0246_orderparticipationsegment_business_cycle')]

    operations = [
        migrations.AddField(
            model_name='order', name='gift_provider_business_cycle',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
    ]
