import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0236_addon_points_handover_snapshot')]
    operations = [
        migrations.CreateModel(
            name='PointsPrestartCancelFundingEvidence',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('store_slug', models.CharField(max_length=48)), ('facts', models.JSONField()),
                    ('evidence_hash', models.CharField(max_length=64)), ('created_at', models.DateTimeField(auto_now_add=True)),
                    ('points_refund', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                        related_name='prestart_cancel_funding', to='orders.orderpointsrefund'))],
        ),
        migrations.CreateModel(
            name='PointsPrestartCancelFundingRelease',
            fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                    ('amount', models.PositiveIntegerField()), ('created_at', models.DateTimeField(auto_now_add=True)),
                    ('allocation', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                        related_name='prestart_cancel_release', to='orders.orderpointsfundingallocation')),
                    ('evidence', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                        related_name='releases', to='orders.pointsprestartcancelfundingevidence'))],
            options={'constraints': [models.CheckConstraint(condition=models.Q(amount__gt=0),
                                name='points_prestart_cancel_release_positive')]},
        ),
    ]
