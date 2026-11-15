from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0278_service_fulfillment_provider_count')]
    operations = [migrations.CreateModel(
        name='PointsTransferAddonLiabilityRevision',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('snapshot', models.JSONField()),
            ('evidence_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.order')),
            ('transfer_revision', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointspostrefundtransferrevision')),
            ('payment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonpayment')),
            ('refund', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonrefund')),
            ('funding_run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderaddonfundingreleaserun')),
            ('previous', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.pointstransferaddonliabilityrevision')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('refund', 'payment'), name='pts_addon_liability_ref_pay_uniq')]},
    )]
