from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0052_kook_online_rule_transition')]
    operations = [migrations.CreateModel(name='KookOnlinePointAllocation', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('points', models.PositiveBigIntegerField()),
        ('cursor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlinecursor')),
        ('ledger', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='kook_rule_allocations', to='engagement.pointledger')),
        ('rule_version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlineruleversion')),
    ], options={'constraints': [models.UniqueConstraint(fields=('ledger', 'rule_version'), name='kook_online_allocation_unique')]})]
