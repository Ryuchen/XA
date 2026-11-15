from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0279_points_transfer_addon_liability')]
    operations = [migrations.AddField(
        model_name='orderpointstransferfollowupfundingrevision',
        name='pending_handover_snapshot',
        field=models.JSONField(default=list),
    )]
