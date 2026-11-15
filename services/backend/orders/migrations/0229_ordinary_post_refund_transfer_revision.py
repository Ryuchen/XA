from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0228_ordinary_transfer_followup_revision')]
    operations = [migrations.CreateModel(name='OrdinaryPostRefundTransferRevision', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('snapshot', models.JSONField()), ('snapshot_hash', models.CharField(max_length=64)),
        ('source_kind', models.CharField(max_length=20)), ('source_id', models.PositiveBigIntegerField()),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='ordinary_post_refund_transfers', to='orders.order')),
        ('previous', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='next_versions', to='orders.ordinarypostrefundtransferrevision')),
        ('transfer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='ordinary_post_refund_revisions', to='orders.orderoriginalsharetransfer')),
    ], options={'constraints': [models.UniqueConstraint(fields=('order', 'source_kind', 'source_id'),
                    name='ordinary_refund_transfer_source_uniq')]})]
