import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0058_recovery_zero_service_assignment'),
                    ('orders', '0126_orderpointssettlement_orderpointssettlementfunding_and_more')]
    operations = [migrations.AddField(
        model_name='refunddepositrecoverypermit', name='zero_initial_points_person',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                   related_name='zero_initial_recovery_permit', to='orders.orderpointssettlementperson'),
    ), migrations.AddConstraint(
        model_name='refunddepositrecoverypermit',
        constraint=models.CheckConstraint(
            condition=(models.Q(zero_initial_points_person__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='ZERO_INITIAL_PTS_SETTLED', income_application__isnull=True,
                    cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                    zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True,
                    zero_service_assignment__isnull=True)), name='refund_recovery_init_points_valid'),
    )]
