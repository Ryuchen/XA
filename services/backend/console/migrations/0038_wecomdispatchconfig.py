from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('console', '0037_payroll_reemployment_refund_waiver')]
    operations = [migrations.CreateModel(name='WecomDispatchConfig', fields=[
        ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
        ('enabled', models.BooleanField(default=False)),
        ('auto_new_orders', models.BooleanField(default=False)),
        ('auto_rejected_orders', models.BooleanField(default=False)),
        ('webhook_ciphertext', models.TextField(blank=True, default='')),
        ('revision', models.PositiveIntegerField(default=0)),
        ('updated_at', models.DateTimeField(auto_now=True)),
    ])]
