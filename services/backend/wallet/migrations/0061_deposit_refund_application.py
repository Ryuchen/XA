import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0060_recovery_zero_unaffected'), ('club_accounts', '0001_initial'),
                    ('users', '0001_initial')]
    operations = [migrations.CreateModel(name='DepositRefundApplication', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField()), ('request_id', models.CharField(max_length=64)),
        ('amount', models.PositiveBigIntegerField()), ('note', models.CharField(blank=True, default='', max_length=255)),
        ('status', models.CharField(default='PENDING', max_length=12)),
        ('eligible_at', models.DateTimeField(blank=True, null=True)), ('cleared_at', models.DateTimeField(blank=True, null=True)),
        ('resolution', models.CharField(blank=True, default='', max_length=255)),
        ('processed_at', models.DateTimeField(blank=True, null=True)), ('notification_state', models.CharField(default='PENDING', max_length=12)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('operator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ('profile', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='users.escortprofile')),
        ('refund_transaction', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
    ], options={'constraints': [models.UniqueConstraint(fields=('account', 'request_id'), name='deposit_application_request_unique'),
        models.CheckConstraint(condition=models.Q(amount__gt=0), name='deposit_application_amount_positive'),
        models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'REFUNDED', 'REJECTED', 'CANCELLED']), name='deposit_application_status_valid')]})]
