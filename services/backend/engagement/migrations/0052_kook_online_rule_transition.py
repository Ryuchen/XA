from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0051_kook_manual_online_segment')]
    operations = [migrations.CreateModel(name='KookOnlineRuleTransition', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('effective_at', models.DateTimeField()), ('carried_ms', models.PositiveBigIntegerField()),
        ('old_pending_points', models.PositiveBigIntegerField()),
        ('new_remainder_ms', models.PositiveBigIntegerField()), ('new_pending_points', models.PositiveBigIntegerField()),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('cursor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rule_transitions', to='engagement.kookonlinecursor')),
        ('from_rule', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='engagement.kookonlineruleversion')),
        ('to_rule', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='engagement.kookonlineruleversion')),
    ], options={'constraints': [models.UniqueConstraint(fields=('cursor', 'to_rule'), name='kook_online_transition_unique')]})]
