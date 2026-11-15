from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0243_addon_refund_zero_wallet_evidence')]
    operations = [migrations.CreateModel(
        name='PointsHandoverRefundInvalidation',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('handover', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='points_refund_invalidation', to='orders.orderaddonhandover')),
            ('refund', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='points_handover_invalidations', to='orders.orderaddonrefund')),
        ],
    )]
