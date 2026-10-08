from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0027_order_report_settlement')]
    operations = [migrations.AddField(
        model_name='orderprovider', name='released_at', field=models.DateTimeField(blank=True, null=True),
    )]
