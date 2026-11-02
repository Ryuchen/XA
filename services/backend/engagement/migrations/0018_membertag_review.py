from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('engagement', '0017_lottery_ten_guarantee'), ('club_accounts', '0002_clubaccount_club_account_type_active_idx_and_more')]
    operations = [
        migrations.AddField(model_name='membertag', name='reviewed_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='membertag', name='reviewed_by', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
    ]
