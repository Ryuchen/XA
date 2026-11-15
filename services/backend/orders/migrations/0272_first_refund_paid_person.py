import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0271_ordinary_negotiation_segment_plan')]
    operations = [migrations.CreateModel(
        name='PointsFirstRefundPaidPersonEvidence',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('allocation_snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('person', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='first_refund_evidence', to='orders.orderpointssettlementperson')),
            ('posting', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                related_name='first_paid_person_evidence', to='orders.pointsnegotiationposting')),
        ],
    )]
