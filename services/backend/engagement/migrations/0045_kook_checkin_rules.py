from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0044_kookpointsconfig_revision'), ('club_accounts', '0012_licensequota_active_count')]
    operations = [
        migrations.AddField(model_name='kookpointsconfig', name='listener_revision', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='kookpointsconfig', name='daily_checkin_points', field=models.PositiveIntegerField(default=5)),
        migrations.CreateModel(name='KookCheckinRuleVersion', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('revision', models.PositiveIntegerField(unique=True)),
            ('points', models.PositiveIntegerField()),
            ('effective_at', models.DateTimeField()),
            ('operator', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
        ]),
    ]
