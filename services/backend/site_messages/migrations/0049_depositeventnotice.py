import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0048_addonhandoverproposalnotice'), ('wallet', '0054_transaction_deposit_cycle'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='DepositEventNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('kind', models.CharField(max_length=8)), ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('preview', models.CharField(max_length=255)), ('status', models.CharField(default='PENDING', max_length=10)),
        ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)), ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('topup', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.cashdeposittopup')),
        ('refund', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.CheckConstraint(condition=(models.Q(kind='TOPUP', topup__isnull=False, refund__isnull=True)
        | models.Q(kind='REFUND', topup__isnull=True, refund__isnull=False)), name='deposit_notice_one_source')]})]
