import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0018_grabdisqualificationnotices'),
                    ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='OrderApplicationNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('kind', models.CharField(max_length=16)),
        ('title', models.CharField(max_length=100)),
        ('preview', models.CharField(max_length=255)),
        ('action_url', models.CharField(blank=True, default='', max_length=255)),
        ('assignment_alert', models.BooleanField(default=False)),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('application', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='notice_intents', to='orders.orderapplication')),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='club_accounts.clubaccount')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('application', 'account', 'kind'),
                                                        name='app_notice_event_once')]})]
