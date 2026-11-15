from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0276_points_followup_transfer_revision')]
    operations = [migrations.CreateModel(
        name='PointsFollowupTransferRelease',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.PositiveBigIntegerField()),
            ('allocation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderpointsfundingallocation')),
            ('revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='releases', to='orders.pointsfollowuptransferrevision')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('revision', 'allocation'), name='pts_follow_trans_release_uniq'),
                                 models.CheckConstraint(condition=models.Q(amount__gt=0), name='pts_follow_trans_release_pos')]},
    )]
