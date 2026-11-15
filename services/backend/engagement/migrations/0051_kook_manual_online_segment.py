from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0050_kook_online_rule_draft')]
    operations = [migrations.CreateModel(name='KookManualOnlineSegment', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('started_at', models.DateTimeField()), ('ended_at', models.DateTimeField()),
        ('elapsed_ms', models.PositiveBigIntegerField()),
        ('rule_window_ms', models.PositiveIntegerField()), ('rule_points', models.PositiveIntegerField()),
        ('review', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rule_segments', to='engagement.kookmanualonlinereview')),
        ('rule_version', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='engagement.kookonlineruleversion')),
    ], options={'constraints': [models.UniqueConstraint(fields=('review', 'started_at'), name='kook_manual_segment_start_unique')]})]
