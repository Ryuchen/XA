from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0288_points_addon_previous_round')]
    operations = [migrations.AddField(model_name='pointsthirdtransferrevision', name='previous_round',
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                related_name='+', to='orders.pointsrefundroundevidence'))]
