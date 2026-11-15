from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('club_accounts', '0009_storeroutingsnapshot')]

    operations = [
        migrations.CreateModel(
            name='ProviderLifecycleLock',
            fields=[('account', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                primary_key=True, related_name='provider_lifecycle_lock', serialize=False,
                to='club_accounts.clubaccount'))],
        ),
    ]
