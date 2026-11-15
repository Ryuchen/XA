from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0045_kook_checkin_rules'), ('console', '0038_wecomdispatchconfig')]
    operations = [migrations.CreateModel(name='KookOnlineInterval', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('account_cycle', models.PositiveIntegerField()),
        ('binding_cycle', models.PositiveIntegerField(null=True, blank=True)),
        ('channel_id', models.CharField(max_length=64)),
        ('started_at', models.DateTimeField()), ('ended_at', models.DateTimeField()),
        ('elapsed_ms', models.PositiveIntegerField()),
        ('rule_revision', models.PositiveIntegerField(default=0)),
        ('rule_window_ms', models.PositiveIntegerField(default=7200000)),
        ('rule_points', models.PositiveIntegerField(default=1)),
        ('interval_outcome', models.CharField(max_length=8)),
        ('award_outcome', models.CharField(max_length=8)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
        ('binding', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='online_intervals', to='engagement.kookbinding')),
        ('decision_source', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='console.adminauditlog')),
        ('points_ledger', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='engagement.pointledger')),
    ], options={'constraints': [models.UniqueConstraint(fields=('binding', 'started_at', 'ended_at'), name='kook_online_interval_unique')]})]
