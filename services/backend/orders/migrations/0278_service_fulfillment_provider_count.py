from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('orders', '0277_points_followup_transfer_release')]
    operations = [migrations.AddField(model_name='serviceitem', name='fulfillment_provider_count',
        field=models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(2)],
                                               help_text='服务实际履约陪玩人数'))]
