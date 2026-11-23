from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0062_withdrawal_account_cycle')]

    operations = [
        migrations.AlterField(
            model_name='idempotencykey', name='scope',
            field=models.CharField(max_length=64, blank=True, default='',
                                   help_text='业务标注，仅用于排障，不参与唯一约束'),
        ),
    ]
