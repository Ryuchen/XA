from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0241_ordinary_prestart_addon_release')]

    operations = [
        migrations.CreateModel(
            name='PointsTransferZeroSettlementRevision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('before_plan', models.JSONField()),
                ('before_plan_hash', models.CharField(max_length=64)),
                ('after_plan', models.JSONField()),
                ('after_plan_hash', models.CharField(max_length=64)),
                ('paid_snapshot', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('manifest', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='points_zero_revision', to='orders.ordersegmentsettlementmanifest')),
                ('termination', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='transfer_settlement_revision', to='orders.pointszerofollowuptermination')),
            ],
        ),
    ]
