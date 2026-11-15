import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('club_accounts', '0006_wechat_identity'),
        ('site_messages', '0006_licensenoticedelivery'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManualMessageCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.CharField(max_length=64)),
                ('request_fingerprint', models.CharField(max_length=64)),
                ('selector', models.JSONField(default=dict)),
                ('title', models.CharField(max_length=100)),
                ('preview', models.CharField(blank=True, default='', max_length=255)),
                ('detail', models.TextField(blank=True, default='')),
                ('message_type', models.CharField(choices=[('SYSTEM', '系统通知'), ('ORDER', '订单消息'), ('SUPPORT', '客服消息'), ('PROMOTION', '活动通知')], max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('creator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('creator', 'request_id'), name='manual_msg_creator_request_unique')]},
        ),
        migrations.CreateModel(
            name='ManualMessageRecipient',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_cycle', models.PositiveIntegerField()),
                ('account_type', models.CharField(max_length=20)),
                ('status', models.CharField(choices=[('PENDING', '待创建'), ('CREATED', '已创建'), ('SKIPPED', '账号已变化')], db_index=True, default='PENDING', max_length=10)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('last_error', models.CharField(blank=True, default='', max_length=100)),
                ('next_attempt_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='recipients', to='site_messages.manualmessagecampaign')),
                ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('campaign', 'account'), name='manual_msg_campaign_account_unique')],
                'indexes': [models.Index(fields=['status', 'next_attempt_at', 'id'], name='manual_msg_pending_idx')],
            },
        ),
    ]
