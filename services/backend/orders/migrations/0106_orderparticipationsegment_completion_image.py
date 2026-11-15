from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0105_ordersegmenttransferbasis_fixed_fee_store_shortfall')]

    operations = [
        migrations.AddField(
            model_name='orderparticipationsegment',
            name='completion_image',
            field=models.ImageField(blank=True, null=True, upload_to='reports/participation/'),
        ),
    ]
