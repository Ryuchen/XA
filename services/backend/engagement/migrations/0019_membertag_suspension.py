from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0018_membertag_review')]
    operations = [
        migrations.AddField(model_name='membertag', name='suspended_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='membertag', name='suspended_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        migrations.AddField(model_name='membertag', name='suspension_reason', field=models.CharField(blank=True, default='', max_length=255)),
    ]
