from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0049_kook_online_rule_balance')]
    operations = [migrations.CreateModel(name='KookOnlineRuleDraft', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('config_revision', models.PositiveIntegerField(unique=True)),
        ('window_ms', models.PositiveIntegerField()),
        ('points', models.PositiveIntegerField()),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('operator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
    ])]
