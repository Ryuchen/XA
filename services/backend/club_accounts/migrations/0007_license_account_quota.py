from django.db import migrations, models


def seed_quota_locks(apps, schema_editor):
    Lock = apps.get_model('club_accounts', 'LicenseQuotaLock')
    for account_type in ('BOSS', 'PROVIDER'):
        Lock.objects.using(schema_editor.connection.alias).get_or_create(account_type=account_type)


class Migration(migrations.Migration):
    dependencies = [('club_accounts', '0006_wechat_identity')]
    operations = [
        migrations.AddField(
            model_name='clubaccount', name='license_membership_active',
            field=models.BooleanField(default=True, editable=False),
        ),
        migrations.CreateModel(name='LicenseQuotaLock', fields=[
            ('account_type', models.CharField(max_length=20, primary_key=True, serialize=False)),
        ]),
        migrations.RunPython(seed_quota_locks, migrations.RunPython.noop),
    ]
