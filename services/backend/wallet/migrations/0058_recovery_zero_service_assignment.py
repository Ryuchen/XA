import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0057_withdrawalrelease'),
                    ('orders', '0012_order_escort_mode_alter_gamecategory_id_and_more')]

    operations = [migrations.AddField(
        model_name='refunddepositrecoverypermit', name='zero_service_assignment',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                   related_name='zero_refund_recovery_permit', to='orders.orderprovider'),
    ), migrations.AddConstraint(
        model_name='refunddepositrecoverypermit',
        constraint=models.CheckConstraint(
            condition=(models.Q(zero_service_assignment__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='ZERO_SERVICE_SETTLED', income_application__isnull=True,
                    cash_topup__isnull=True, zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                    zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True)),
            name='refund_recovery_service_zero_valid'),
    )]
