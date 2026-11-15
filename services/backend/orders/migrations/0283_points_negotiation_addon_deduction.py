from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0282_points_refund_round_evidence')]
    operations = [migrations.AddField(
        model_name='pointsnegotiationterms', name='addon_pending_plan',
        field=models.JSONField(blank=True, null=True),
    ), migrations.CreateModel(
        name='PointsNegotiationAddonDeduction',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('position', models.PositiveIntegerField()),
            ('income_before', models.PositiveBigIntegerField()),
            ('amount', models.PositiveBigIntegerField()),
            ('share', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                        related_name='addon_deductions', to='orders.pointsnegotiationpostingshare')),
            ('payment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonpayment')),
            ('segment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderparticipationsegment')),
            ('addon_share', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                              related_name='+', to='orders.orderaddonhandovershare')),
        ],
        options={'constraints': [
            models.UniqueConstraint(fields=('share', 'position'), name='pts_neg_addon_deduct_position'),
            models.CheckConstraint(condition=models.Q(amount__gt=0, income_before__gte=models.F('amount')),
                                   name='pts_neg_addon_deduct_amount'),
        ]},
    )]
