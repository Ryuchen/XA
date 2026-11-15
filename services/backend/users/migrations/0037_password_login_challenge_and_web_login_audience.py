import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0036_web_login_session')]
    operations = [
        migrations.AddField(
            model_name='webloginsession',
            name='audience',
            field=models.CharField(db_index=True, default='boss', max_length=12),
        ),
        migrations.CreateModel(
            name='PasswordLoginChallenge',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('scope', models.CharField(max_length=12)),
                ('password_fingerprint', models.CharField(max_length=64)),
                ('phone', models.CharField(max_length=20)),
                ('sms_hash', models.CharField(max_length=64)),
                ('sms_sent_at', models.DateTimeField()),
                ('sms_expires_at', models.DateTimeField()),
                ('sms_attempts', models.PositiveSmallIntegerField(default=0)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('consumed', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='password_login_challenges', to='club_accounts.clubaccount')),
            ],
            options={'verbose_name': '账号密码短信登录挑战'},
        ),
        migrations.AddIndex(
            model_name='passwordloginchallenge',
            index=models.Index(fields=['account', 'created_at'], name='password_sms_account_idx'),
        ),
    ]
