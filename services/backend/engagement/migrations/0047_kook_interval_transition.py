from django.db import migrations, models
import django.db.models.deletion


def seed_rule(apps, schema_editor):
    rule = apps.get_model('engagement', 'KookOnlineRuleVersion')
    rule.objects.using(schema_editor.connection.alias).create(revision=0, window_ms=7200000, points=1)


class Migration(migrations.Migration):
    dependencies = [('engagement', '0046_kook_online_interval')]
    operations = [
        migrations.CreateModel(name='KookOnlineRuleVersion', fields=[
            ('revision', models.PositiveIntegerField(primary_key=True, serialize=False)),
            ('window_ms', models.PositiveIntegerField()), ('points', models.PositiveIntegerField()),
            ('effective_at', models.DateTimeField(null=True, blank=True)),
            ('operator', models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
        ]),
        migrations.RunPython(seed_rule, migrations.RunPython.noop),
        migrations.AddField(model_name='kookonlineinterval', name='rule_version',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlineruleversion')),
        *[migrations.AddField(model_name='kookonlineinterval', name=name,
                              field=models.PositiveBigIntegerField(default=0), preserve_default=False)
          for name in ('online_before', 'online_after', 'awarded_before', 'awarded_after',
                       'online_base_before', 'online_base_after', 'award_base_before', 'award_base_after')],
        migrations.AddField(model_name='kookonlineinterval', name='points_enabled_used',
                            field=models.BooleanField(null=True)),
    ]
