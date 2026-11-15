from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0234_points_post_refund_transfer')]

    operations = [
        migrations.AlterField(model_name='orderaddoncompletion', name='execution',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='orders.negotiationexecution')),
        migrations.AddField(model_name='orderaddoncompletion', name='points_posting',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='orders.pointsnegotiationposting')),
        migrations.AddConstraint(model_name='orderaddoncompletion',
            constraint=models.CheckConstraint(condition=(models.Q(execution__isnull=False, points_posting__isnull=True)
                | models.Q(execution__isnull=True, points_posting__isnull=False)), name='addon_completion_one_origin')),
    ]
