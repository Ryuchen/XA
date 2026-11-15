from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0269_points_refund_rounding_transaction')]

    operations = [migrations.AddField(
        model_name='pointsnegotiationterms', name='segment_pending_plan',
        field=models.JSONField(blank=True, null=True),
    ), migrations.CreateModel(
        name='PointsNegotiationSegmentDeduction',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.PositiveIntegerField()),
            ('segment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='points_negotiation_deductions', to='orders.orderparticipationsegment')),
            ('share', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='segment_deductions', to='orders.pointsnegotiationpostingshare')),
        ],
        options={'constraints': [
            models.UniqueConstraint(fields=('share', 'segment'), name='points_neg_share_segment_uniq'),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='points_neg_segment_amount_pos'),
        ]},
    )]
