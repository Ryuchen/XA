import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0264_points_voluntary_paid_refund_evidence')]

    operations = [migrations.CreateModel(
        name='PointsVoluntaryPaidPaymentEvidence',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('payment', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='voluntary_paid_evidence', to='orders.orderaddonpayment')),
            ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='voluntary_paid_payments', to='orders.ordersegmentsettlementmanifest')),
            ('funding_run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='voluntary_paid_payment_evidence', to='orders.orderaddonfundingreleaserun')),
        ]), migrations.CreateModel(
        name='PointsVoluntaryPaidPaymentSettlementRevision',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('payment_ids', models.JSONField()),
            ('refund_ids', models.JSONField(default=list)),
            ('before_plan', models.JSONField()),
            ('before_plan_hash', models.CharField(max_length=64)),
            ('after_plan', models.JSONField()),
            ('after_plan_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                related_name='voluntary_payment_revisions', to='orders.ordersegmentsettlementmanifest')),
            ('evidence', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='settlement_revision', to='orders.pointsvoluntarypaidpaymentevidence')),
        ])]
