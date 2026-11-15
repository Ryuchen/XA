from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0229_ordinary_post_refund_transfer_revision')]
    operations = [
        migrations.RemoveConstraint(model_name='orderaddonfundingreleaserun', name='addon_release_single_cause'),
        migrations.AddField(model_name='orderaddonfundingreleaserun', name='ordinary_transfer_revision',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='funding_release_run', to='orders.ordinarypostrefundtransferrevision')),
        migrations.AddConstraint(model_name='orderaddonfundingreleaserun', constraint=models.CheckConstraint(
            condition=(
                models.Q(refund__isnull=False, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=False, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=False, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=False, negotiation__isnull=True, ordinary_transfer_revision__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=False, ordinary_transfer_revision__isnull=True) |
                models.Q(refund__isnull=True, handover__isnull=True, payment__isnull=True, prestart_handoff__isnull=True, negotiation__isnull=True, ordinary_transfer_revision__isnull=False)),
            name='addon_release_single_cause')),
    ]
