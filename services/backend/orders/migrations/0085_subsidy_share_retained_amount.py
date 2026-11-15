from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0084_orderplatformsubsidy_orderplatformsubsidyshare_and_more')]

    operations = [
        migrations.AddField(
            model_name='orderplatformsubsidyshare',
            name='retained_amount',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
