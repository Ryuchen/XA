import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('users', '0035_achievement_currency_display_name')]
    operations = [migrations.CreateModel(
        name='WebLoginSession',
        fields=[
            ('ticket', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('poll_token_hash', models.CharField(max_length=64)),
            ('bridge_token_hash', models.CharField(blank=True, max_length=64)),
            ('purpose', models.CharField(default='login', max_length=20)),
            ('wechat_openid', models.CharField(blank=True, max_length=100)),
            ('phone', models.CharField(blank=True, max_length=20)),
            ('sms_hash', models.CharField(blank=True, max_length=64)),
            ('sms_sent_at', models.DateTimeField(blank=True, null=True)),
            ('sms_expires_at', models.DateTimeField(blank=True, null=True)),
            ('sms_attempts', models.PositiveSmallIntegerField(default=0)),
            ('confirmed_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('expires_at', models.DateTimeField(db_index=True)),
            ('confirmed_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='web_login_confirmations', to='club_accounts.clubaccount')),
            ('expected_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='web_login_requests', to='club_accounts.clubaccount')),
        ], options={'verbose_name': '网页微信登录会话'},
    )]
