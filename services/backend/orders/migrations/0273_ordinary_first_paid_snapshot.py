from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0272_first_refund_paid_person')]
    operations = [migrations.AddField(
        model_name='negotiationproposal', name='ordinary_paid_settlement_snapshot',
        field=models.JSONField(blank=True, null=True),
    )]
