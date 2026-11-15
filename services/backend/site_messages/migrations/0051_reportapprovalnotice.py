from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0050_depositapplicationnotice'), ('wallet', '0062_withdrawal_account_cycle'),
                    ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='ReportApprovalNotice', fields=[
        ('id', models.BigAutoField(primary_key=True, serialize=False)),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('audited_at', models.DateTimeField()), ('preview', models.CharField(max_length=255)),
        ('status', models.CharField(default='PENDING', max_length=10)),
        ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('report', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.providerreport')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('report', 'audited_at'), name='report_approval_notice_once')]})]
