import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0263_orderrefund_customer_transaction')]

    operations = [
        migrations.CreateModel(
            name='PointsVoluntaryPaidRefundEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('snapshot', models.JSONField()),
                ('evidence_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('refund', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='voluntary_paid_evidence', to='orders.orderaddonrefund')),
                ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name='voluntary_paid_refunds', to='orders.ordersegmentsettlementmanifest')),
                ('funding_run', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='voluntary_paid_evidence', to='orders.orderaddonfundingreleaserun')),
            ],
        ),
    ]
