from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0286_points_third_target_invalidation')]
    operations = [migrations.AddField(model_name='pointsnegotiationterms', name='round_context',
        field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='pointsrefundroundevidence', name='latest_transfer_version',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                related_name='+', to='orders.pointsthirdtransferrevision'))]
