from django.db import migrations, models
from django.db.models import F
import orders.models


def preserve_settled_orders(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    Order.objects.filter(status='COMPLETED', payment_status='PAID').update(settled_at=F('created_at'))
    Order.objects.filter(status='COMPLETED', payment_status='PAID', completed_at__isnull=False).update(settled_at=F('completed_at'))


class Migration(migrations.Migration):
    dependencies = [('orders', '0026_reservation_and_status_log')]
    operations = [
        migrations.AddField(
            model_name='order', name='settlement_mode',
            field=models.CharField(max_length=20, choices=[('COMPLETION', '完成服务结算'), ('REPORT_APPROVAL', '报单审核后结算')], default='COMPLETION'),
            preserve_default=False,
        ),
        migrations.AddField(model_name='order', name='settled_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.RunPython(preserve_settled_orders, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='order', name='settlement_mode',
            field=models.CharField(max_length=20, choices=[('COMPLETION', '完成服务结算'), ('REPORT_APPROVAL', '报单审核后结算')], default=orders.models.default_settlement_mode),
        ),
    ]
