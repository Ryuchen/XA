import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0059_recovery_zero_initial_points'), ('orders', '0120_negotiation_execution')]
    operations = [migrations.AddField(
        model_name='refunddepositrecoverypermit', name='zero_unaffected_assignment',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='zero_negotiation_recovery_permit', to='orders.orderprovider'),
    ), migrations.AddField(
        model_name='refunddepositrecoverypermit', name='zero_unaffected_execution',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='zero_unaffected_recovery_permits', to='orders.negotiationexecution'),
    ), migrations.AddConstraint(
        model_name='refunddepositrecoverypermit',
        constraint=models.CheckConstraint(condition=(
            models.Q(zero_unaffected_assignment__isnull=True, zero_unaffected_execution__isnull=True) |
            models.Q(zero_unaffected_assignment__isnull=False, zero_unaffected_execution__isnull=False,
                closed_at__isnull=False, close_reason='ZERO_UNAFFECTED_SETTLED', income_application__isnull=True,
                cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True,
                zero_service_assignment__isnull=True, zero_initial_points_person__isnull=True)),
            name='refund_recovery_unaffected_valid'),
    )]
