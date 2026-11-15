from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club_accounts', '0011_initial_admin_otp_grant')]
    operations = [migrations.AddField(
        model_name='licensequotalock', name='active_count',
        field=models.PositiveIntegerField(default=0),
    )]
