from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0222_segment_refund_rounding_transaction')]

    operations = [
        migrations.AddField(
            model_name='orderaddonfundingreleaserun',
            name='points_payment_snapshot',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='orderaddonfundingreleaserun',
            name='points_payment_hash',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
