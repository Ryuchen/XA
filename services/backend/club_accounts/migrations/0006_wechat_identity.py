import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('club_accounts', '0005_clubaccount_business_cycle')]
    operations = [migrations.CreateModel(
        name='WechatIdentity',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('channel', models.CharField(max_length=20)),
            ('appid', models.CharField(max_length=64)),
            ('openid', models.CharField(max_length=100)),
            ('unionid', models.CharField(blank=True, db_index=True, max_length=100)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                related_name='wechat_identities', to='club_accounts.clubaccount')),
        ],
        options={
            'verbose_name': '微信渠道身份',
            'constraints': [models.UniqueConstraint(fields=('channel', 'appid', 'openid'), name='uniq_wechat_channel_openid')],
            'indexes': [models.Index(fields=['account', 'channel'], name='wechat_account_channel_idx')],
        },
    )]
