from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('coupons', '0014_vipmonthlycoupongrant')]
    operations = [
        migrations.CreateModel(name='VipMonthlyCutover', fields=[
            ('id', models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False, serialize=False)),
            ('activated_at', models.DateTimeField()),
            ('platform_actor_id', models.CharField(max_length=64)),
            ('operation_id', models.CharField(max_length=64, unique=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ], options={'constraints': [models.CheckConstraint(condition=models.Q(id=1), name='vip_monthly_cutover_singleton')]}),
    ]
