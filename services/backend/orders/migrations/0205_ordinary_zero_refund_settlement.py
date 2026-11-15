from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0204_orderaddoncompletion')]

    operations = [
        migrations.RemoveConstraint(model_name='orderpointssettlement', name='points_settlement_balanced'),
        migrations.AddConstraint(
            model_name='orderpointssettlement',
            constraint=models.CheckConstraint(
                condition=(models.Q(paid_units__gte=10) | models.Q(redemption__isnull=True, paid_units=0)) & models.Q(
                    paid_units=models.F('principal_units') + models.F('gift_units') + models.F('compensation_units'),
                    principal_units__exact=models.F('provider_units') + models.F('inviter_units') + models.F('store_units')
                    - models.F('funding_units') - models.F('recovery_units')),
                name='points_settlement_balanced'),
        ),
    ]
