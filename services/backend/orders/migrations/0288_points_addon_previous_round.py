from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0287_points_refund_round_latest_transfer')]
    operations = [migrations.AddField(model_name='pointsthirdaddonfundingrevision', name='previous_round',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                related_name='+', to='orders.pointsrefundroundevidence'))]
