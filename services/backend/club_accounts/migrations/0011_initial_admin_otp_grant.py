import uuid
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('club_accounts', '0010_provider_lifecycle_lock')]
    operations = [migrations.CreateModel(name='InitialAdminOtpGrant', fields=[
        ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
        ('token_hash', models.CharField(max_length=64, unique=True)),
        ('password_fingerprint', models.CharField(max_length=64)),
        ('secret_ciphertext', models.TextField(blank=True, default='')),
        ('platform_account_id', models.UUIDField()),
        ('operation_id', models.CharField(max_length=32)),
        ('expires_at', models.DateTimeField()),
        ('attempts', models.PositiveSmallIntegerField(default=0)),
        ('prepared_at', models.DateTimeField(null=True, blank=True)),
        ('revoked_at', models.DateTimeField(null=True, blank=True)),
        ('consumed_at', models.DateTimeField(null=True, blank=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='initial_otp_grants', to='club_accounts.clubaccount')),
    ])]
