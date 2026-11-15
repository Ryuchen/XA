from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('engagement', '0055_kook_rule_source_draft')]
    operations = [
        migrations.AddField(model_name='achievementlicenseevent', name='sales_snapshot',
                            field=models.JSONField(blank=True, null=True)),
        migrations.AddField(model_name='achievementlicenseevent', name='sales_snapshot_hash',
                            field=models.CharField(blank=True, max_length=64)),
    ]
