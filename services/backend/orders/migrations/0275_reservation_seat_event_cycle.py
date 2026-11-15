from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0274_ordinary_paid_addon_refund')]

    operations = [
        migrations.AddField(
            model_name='reservationseatevent',
            name='provider_business_cycle',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True),
        ),
    ]
