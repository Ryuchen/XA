import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0233_addon_main_segment_deduction')]

    operations = [
        migrations.CreateModel(name='PointsPostRefundTransferRevision', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='points_post_refund_transfers', to='orders.order')),
            ('prior', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointspostrefundtransferrevision')),
            ('transfer', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderoriginalsharetransfer')),
        ]),
        migrations.CreateModel(name='PointsPostRefundTransferRelease', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('amount', models.PositiveBigIntegerField()),
            ('allocation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderpointsfundingallocation')),
            ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='releases', to='orders.pointspostrefundtransferrevision')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=('revision', 'allocation'), name='pts_post_ref_trans_release_uniq'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_post_ref_trans_release_positive'),
        ]}),
    ]
