from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0280_points_followup_pending_snapshot')]
    operations = [
        migrations.AddField(model_name='order', name='gift_category_id_snapshot',
                            field=models.PositiveBigIntegerField(blank=True, db_index=True, editable=False, null=True)),
    ]
