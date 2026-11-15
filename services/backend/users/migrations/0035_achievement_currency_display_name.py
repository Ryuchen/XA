from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0034_checkin_legacy_user_optional'),
    ]

    operations = [
        migrations.AlterField(
            model_name='achievement',
            name='metric',
            field=models.CharField(choices=[('orders', '完成订单数'), ('amount', '累计消费（浪花）')], default='orders', max_length=16),
        ),
    ]
