import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0088_aftersale_affected_providers')]

    operations = [
        migrations.AddField(model_name='evaluation', name='low_rating_aftersale_state',
            field=models.CharField(blank=True, default='', editable=False, max_length=16,
                choices=[('', '非低星或历史未核实'), ('OPENED', '已创建售后'), ('ATTACHED', '已关联处理中售后'),
                         ('CLOSED', '已有售后已结案'), ('EXPIRED', '售后期已结束'), ('INELIGIBLE', '订单不可创建售后')])),
        migrations.AddField(model_name='evaluation', name='low_rating_aftersale',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='low_rating_evaluations', to='orders.aftersale')),
    ]
