from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0270_points_negotiation_segment_deduction')]

    operations = [migrations.AddField(
        model_name='negotiationproposal', name='ordinary_segment_pending_plan',
        field=models.JSONField(blank=True, null=True),
    )]
