from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0283_points_negotiation_addon_deduction')]
    operations = [migrations.CreateModel(
        name='PointsThirdAddonFundingRevision',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.order')),
            ('third', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointsrefundroundevidence')),
            ('payment', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonpayment')),
            ('refund', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonrefund')),
            ('handover', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonhandover')),
            ('run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonfundingreleaserun')),
            ('previous', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                          related_name='+', to='orders.pointsthirdaddonfundingrevision')),
        ],
        options={'constraints': [models.CheckConstraint(condition=(
            models.Q(payment__isnull=False, refund__isnull=True, handover__isnull=True) |
            models.Q(payment__isnull=True, refund__isnull=False, handover__isnull=True) |
            models.Q(payment__isnull=True, refund__isnull=True, handover__isnull=False)), name='pts_third_addon_event_cause')]},
    )]
