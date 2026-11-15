import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('stores', '0002_store_currency_name')]
    operations = [migrations.CreateModel(
        name='AdminLoginHandoff',
        fields=[
            ('ticket', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('poll_token_hash', models.CharField(max_length=64)),
            ('bridge_token_hash', models.CharField(max_length=64, blank=True)),
            ('purpose', models.CharField(default='login', max_length=20)),
            ('store_slug', models.SlugField(blank=True, db_index=True, max_length=48)),
            ('expected_account_id', models.PositiveBigIntegerField(blank=True, null=True)),
            ('confirmed_account_id', models.PositiveBigIntegerField(blank=True, null=True)),
            ('wechat_openid', models.CharField(blank=True, max_length=100)),
            ('confirmed_at', models.DateTimeField(blank=True, null=True)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('expires_at', models.DateTimeField(db_index=True)),
        ],
        options={'verbose_name': '管理后台微信登录会话'},
    )]
