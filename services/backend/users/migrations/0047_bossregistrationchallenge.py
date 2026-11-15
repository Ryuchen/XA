import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0046_escortschedule_overnight'),
        ('club_accounts', '0007_license_account_quota'),
    ]

    operations = [
        migrations.CreateModel(
            name='BossRegistrationChallenge',
            fields=[
                ('phone', models.CharField(max_length=11, primary_key=True, serialize=False)),
                ('nonce', models.UUIDField(default=uuid.uuid4, unique=True)),
                ('code_hash', models.CharField(max_length=64)),
                ('sent_at', models.DateTimeField()),
                ('expires_at', models.DateTimeField()),
                ('attempts', models.PositiveSmallIntegerField(default=0)),
                ('consumed', models.BooleanField(default=False)),
            ],
        ),
    ]
