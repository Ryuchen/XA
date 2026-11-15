from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0284_points_third_addon_funding')]
    operations = [
        migrations.CreateModel(name='PointsThirdTransferRevision', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.order')),
            ('transfer', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderoriginalsharetransfer')),
            ('third', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsrefundroundevidence')),
            ('previous_addon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsthirdaddonfundingrevision')),
            ('previous_transfer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsthirdtransferrevision')),
        ]),
        migrations.AddField(model_name='pointsthirdaddonfundingrevision', name='previous_transfer', field=models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsthirdtransferrevision')),
        migrations.CreateModel(name='PointsThirdTransferRelease', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.PositiveBigIntegerField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('allocation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderpointsfundingallocation')),
            ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='releases', to='orders.pointsthirdtransferrevision')),
        ], options={'constraints': [models.UniqueConstraint(fields=('revision', 'allocation'), name='pts_third_transfer_release_unique')]}),
    ]
