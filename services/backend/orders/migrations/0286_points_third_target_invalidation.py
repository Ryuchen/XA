from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0285_points_third_transfer')]
    operations = [migrations.CreateModel(name='PointsThirdHandoverTargetInvalidation', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('snapshot', models.JSONField()),
        ('evidence_hash', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('handover', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonhandover')),
        ('original_revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsthirdtransferrevision')),
        ('transfer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderoriginalsharetransfer')),
        ('posting', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsnegotiationposting')),
    ], options={'constraints': [models.CheckConstraint(condition=(
        models.Q(transfer__isnull=False, posting__isnull=True) |
        models.Q(transfer__isnull=True, posting__isnull=False)), name='pts_third_target_invalid_cause')]})]
