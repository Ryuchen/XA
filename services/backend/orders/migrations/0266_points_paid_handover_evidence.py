import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0265_points_voluntary_paid_payment_evidence')]

    operations = [migrations.CreateModel(
        name='PointsPaidHandoverEvidence',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('handover', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='paid_settlement_evidence', to='orders.orderaddonhandover')),
            ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='paid_handover_events', to='orders.ordersegmentsettlementmanifest')),
            ('funding_run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='paid_handover_evidence', to='orders.orderaddonfundingreleaserun')),
        ]), migrations.CreateModel(
        name='PointsPaidHandoverSettlementRevision',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('before_plan', models.JSONField()),
            ('before_plan_hash', models.CharField(max_length=64)),
            ('after_plan', models.JSONField()),
            ('after_plan_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='paid_handover_revisions', to='orders.ordersegmentsettlementmanifest')),
            ('evidence', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='settlement_revision', to='orders.pointspaidhandoverevidence')),
        ])]
