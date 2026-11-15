from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0048_kook_online_cursor')]
    operations = [migrations.CreateModel(name='KookOnlineRuleBalance', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('remainder_ms', models.PositiveBigIntegerField(default=0)),
        ('pending_points', models.PositiveBigIntegerField(default=0)),
        ('cursor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rule_balances', to='engagement.kookonlinecursor')),
        ('rule_version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlineruleversion')),
    ], options={'constraints': [models.UniqueConstraint(fields=('cursor', 'rule_version'), name='kook_online_rule_balance_unique')]})]
