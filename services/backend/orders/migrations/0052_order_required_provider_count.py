from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [('orders', '0051_order_provider_requirement_snapshot_and_more')]

    operations = [
        migrations.AddField(
            model_name='order', name='required_provider_count',
            field=models.PositiveSmallIntegerField(
                blank=True, editable=False, null=True,
                validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(2)],
                help_text='新订单成交时需要的陪玩人数；历史订单为空，不从后改目录补推',
            ),
        ),
    ]
