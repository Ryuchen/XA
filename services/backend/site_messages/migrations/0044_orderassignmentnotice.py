import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0043_evaluationreplynotice'), ('orders', '0256_application_business_cycles'),
                    ('wallet', '0054_transaction_deposit_cycle'), ('club_accounts', '0001_initial')]
    operations = [migrations.CreateModel(name='OrderAssignmentNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('slot', models.PositiveBigIntegerField()), ('account_type', models.CharField(max_length=12)),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('legacy_user_id', models.PositiveBigIntegerField(blank=True, null=True)),
        ('title', models.CharField(max_length=100)), ('preview', models.CharField(max_length=255)),
        ('status', models.CharField(default='PENDING', max_length=10)),
        ('note', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                      related_name='+', to='club_accounts.clubaccount')),
        ('assignment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                         related_name='+', to='orders.orderprovider')),
        ('payment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
        ('status_log', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='orders.orderstatuslog')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                        related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('status_log', 'slot'), name='order_assignment_notice_once')]})]
