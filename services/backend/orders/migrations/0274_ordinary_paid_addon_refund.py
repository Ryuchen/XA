import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0273_ordinary_first_paid_snapshot')]
    operations = [migrations.CreateModel(name='OrdinaryPaidAddonRefundEvidence', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('snapshot', models.JSONField()),
        ('snapshot_hash', models.CharField(max_length=64)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('manifest', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
             related_name='ordinary_paid_addon_refunds', to='orders.ordersegmentsettlementmanifest')),
        ('refund', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
             related_name='ordinary_paid_evidence', to='orders.orderaddonrefund')),
    ])]
