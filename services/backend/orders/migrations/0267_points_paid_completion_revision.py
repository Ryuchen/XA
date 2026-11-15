import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0266_points_paid_handover_evidence')]

    operations = [migrations.CreateModel(name='PointsPaidCompletionSettlementRevision', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('paid_snapshot', models.JSONField()), ('paid_hash', models.CharField(max_length=64)),
        ('before_plan', models.JSONField()), ('before_plan_hash', models.CharField(max_length=64)),
        ('after_plan', models.JSONField()), ('after_plan_hash', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='paid_completion_revisions', to='orders.ordersegmentsettlementmanifest')),
        ('completion_log', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
            related_name='paid_completion_revision', to='orders.orderstatuslog')),
    ])]
