import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chat', '0006_reception_assignment'), ('orders', '0086_order_gift_aftersale_hours_order_gift_freeze_hours')]
    operations = [
        migrations.AddField(model_name='chatmessage', name='related_order', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='consultation_messages', to='orders.order', verbose_name='关联订单')),
        migrations.AddField(model_name='chatmessage', name='order_snapshot', field=models.JSONField(blank=True, default=dict, verbose_name='咨询订单快照')),
    ]
