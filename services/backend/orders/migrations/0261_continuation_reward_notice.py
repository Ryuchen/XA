from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0260_order_continuation'), ('site_messages', '0042_staffstartnotice')]
    operations = [
        migrations.AddField(model_name='ordercontinuationreward', name='notice_message',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                       related_name='+', to='site_messages.message')),
        migrations.AddField(model_name='ordercontinuationreward', name='notice_completed_at',
            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='ordercontinuationreward', name='notice_error',
            field=models.CharField(blank=True, default='', max_length=255)),
    ]
