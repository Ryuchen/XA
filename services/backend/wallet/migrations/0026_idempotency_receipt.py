from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0025_pass_income_ledger')]

    operations = [
        migrations.AddField(
            model_name='idempotencykey', name='request_fingerprint',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='idempotencykey', name='response',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
