from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0267_points_paid_completion_revision')]

    operations = [migrations.AddField(
        model_name='orderaddonrefundproposal', name='cross_pending_plan',
        field=models.JSONField(blank=True, null=True),
    )]
