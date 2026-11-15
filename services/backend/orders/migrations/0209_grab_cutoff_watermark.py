import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0208_aftersale_addon_payment_scope')]

    operations = [
        migrations.CreateModel(
            name='OrderGrabCutoffWatermark',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cutoff_at', models.DateTimeField()),
                ('observed_at', models.DateTimeField()),
                ('precision_us', models.PositiveIntegerField()),
                ('clock_vendor', models.CharField(max_length=12)),
                ('server_id', models.CharField(max_length=64)),
                ('receipt_count', models.PositiveIntegerField()),
                ('receipt_digest', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('round', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='cutoff_watermark', to='orders.ordergrabround')),
            ],
        ),
    ]
