from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0047_kook_interval_transition')]
    operations = [
        migrations.AddField(model_name='kookonlineinterval', name='processing_status',
                            field=models.CharField(default='DONE', max_length=12)),
        migrations.AddField(model_name='kookonlineinterval', name='processed_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.CreateModel(name='KookOnlineCursor', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('business_cycle', models.PositiveIntegerField()),
            ('blocked', models.BooleanField(default=False)),
            ('remainder_ms', models.PositiveBigIntegerField(default=0)),
            ('pending_points', models.PositiveBigIntegerField(default=0)),
            ('unbound_online_ms', models.PositiveBigIntegerField(default=0)),
            ('unbound_awarded_points', models.PositiveBigIntegerField(default=0)),
            ('rule_version', models.ForeignKey(default=0, on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlineruleversion')),
            ('last_ended_at', models.DateTimeField(blank=True, null=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='kook_online_cursors', to='club_accounts.clubaccount')),
            ('binding', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='online_cursors', to='engagement.kookbinding')),
        ], options={'constraints': [models.UniqueConstraint(fields=('account', 'business_cycle'), name='kook_online_cursor_cycle_unique')]}),
        migrations.CreateModel(name='KookManualOnlineReview', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('business_cycle', models.PositiveIntegerField()), ('source_id', models.CharField(max_length=100)),
            ('started_at', models.DateTimeField()), ('ended_at', models.DateTimeField()),
            ('online_seconds', models.PositiveIntegerField()), ('status', models.CharField(default='PENDING_REVIEW', max_length=20)),
            ('reason', models.CharField(blank=True, default='', max_length=100)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('resolution_request_id', models.UUIDField(blank=True, null=True, unique=True)),
            ('resolution_outcome', models.CharField(blank=True, default='', max_length=8)),
            ('resolution_points', models.PositiveIntegerField(blank=True, null=True)),
            ('resolution_reason', models.CharField(blank=True, default='', max_length=500)),
            ('platform_actor_id', models.UUIDField(blank=True, null=True)),
            ('resolved_at', models.DateTimeField(blank=True, null=True)),
            ('points_ledger', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='engagement.pointledger')),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
            ('cursor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlinecursor')),
            ('operator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ], options={'constraints': [models.UniqueConstraint(fields=('account', 'business_cycle', 'source_id'), name='kook_manual_online_source_unique')]}),
        migrations.CreateModel(name='KookOnlineProcessing', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('online_before', models.PositiveBigIntegerField()), ('online_after', models.PositiveBigIntegerField()),
            ('awarded_before', models.PositiveBigIntegerField()), ('awarded_after', models.PositiveBigIntegerField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('interval', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='processing', to='engagement.kookonlineinterval')),
            ('points_ledger', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='engagement.pointledger')),
        ]),
    ]
