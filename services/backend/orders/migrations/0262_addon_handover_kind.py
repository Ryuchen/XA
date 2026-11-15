from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0261_continuation_reward_notice'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderaddonhandover',
            name='kind',
            field=models.CharField(choices=[('STANDARD', '既有接替'), ('VOLUNTARY', '自愿接替')],
                                   default='STANDARD', max_length=12),
        ),
    ]
