import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('site_messages', '0029_customercancellationnotice'), ('orders', '0256_application_business_cycles'),
                    ('club_accounts', '0001_initial'), ('wallet', '0056_recharge_account_business_cycle')]
    operations = [migrations.CreateModel(name='OrderRefundNotice', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('business_cycle', models.PositiveIntegerField(blank=True, null=True)),
        ('legacy_user_id', models.PositiveBigIntegerField(blank=True, null=True)),
        ('amount', models.PositiveIntegerField()),
        ('title', models.CharField(max_length=100)),
        ('preview', models.CharField(max_length=255)),
        ('can_open_order', models.BooleanField(default=True)),
        ('skipped_reason', models.CharField(blank=True, default='', max_length=100)),
        ('completed_at', models.DateTimeField(blank=True, null=True)),
        ('created_at', models.DateTimeField(auto_now_add=True)),
        ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='club_accounts.clubaccount')),
        ('refund', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
            related_name='notice_intents', to='orders.orderrefund')),
        ('wallet_transaction', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='wallet.transaction')),
        ('message', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name='+', to='site_messages.message')),
    ], options={'constraints': [models.UniqueConstraint(fields=('refund', 'account'), name='order_refund_notice_once')]})]
