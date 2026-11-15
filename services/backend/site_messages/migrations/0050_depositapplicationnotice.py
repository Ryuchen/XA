import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0049_depositeventnotice'), ('wallet', '0061_deposit_refund_application'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='DepositApplicationNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('status', models.CharField(default='PENDING', max_length=10)),
        ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('application', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.depositrefundapplication')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('application', 'account'), name='deposit_application_notice_once')]})]
