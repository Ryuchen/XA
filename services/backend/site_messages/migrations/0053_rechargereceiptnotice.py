import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0052_report_rejection_notice'), ('wallet', '0062_withdrawal_account_cycle'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='RechargeReceiptNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField(null=True, blank=True)),
        ('gift_units', models.PositiveIntegerField(default=0)), ('gift_pending', models.BooleanField(default=False)),
        ('status', models.CharField(default='PENDING', max_length=10)), ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('recharge', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.rechargerecord')),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', null=True, blank=True, to='club_accounts.clubaccount')),
        ('recharge_tx', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
        ('gift_tx', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', null=True, blank=True, to='wallet.transaction')),
        ('message', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', null=True, blank=True, to='site_messages.message')),
    ])]
