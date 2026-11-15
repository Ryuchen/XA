from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0187_addon_settlement_segments')]

    operations = [
        migrations.AlterField(
            model_name='orderaddonfundingreleaserun', name='refund',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                      related_name='funding_release_run', to='orders.orderaddonrefund'),
        ),
        migrations.AddField(
            model_name='orderaddonfundingreleaserun', name='handover',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                      related_name='funding_release_run', to='orders.orderaddonhandover'),
        ),
        migrations.AddConstraint(
            model_name='orderaddonfundingreleaserun',
            constraint=models.CheckConstraint(condition=(
                models.Q(refund__isnull=False, handover__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=False)), name='addon_release_single_cause'),
        ),
    ]
