from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0259_addon_refund_share_allocation'),
                    ('club_accounts', '0010_provider_lifecycle_lock'), ('wallet', '0056_recharge_account_business_cycle')]
    operations = [
        migrations.CreateModel(name='OrderContinuation', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('business_cycle', models.PositiveIntegerField()), ('request_id', models.CharField(max_length=64)),
            ('status', models.CharField(default='PENDING', max_length=12)), ('submitted_at', models.DateTimeField(auto_now_add=True)),
            ('verified_at', models.DateTimeField(null=True)), ('verification_note', models.CharField(blank=True, default='', max_length=1000)),
            ('verification_request_id', models.CharField(blank=True, default='', max_length=64)), ('participants', models.JSONField(null=True)),
            ('reward_state', models.CharField(default='PENDING', max_length=16)),
            ('reward_review_reason', models.CharField(blank=True, default='', max_length=255)),
            ('original_order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='continuations', to='orders.order')),
            ('continuation_order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='continuation_claim', to='orders.order')),
            ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('verified_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
        ], options={'constraints': [
            models.UniqueConstraint(fields=('customer', 'business_cycle', 'request_id'), name='continuation_customer_request_unique'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING', 'VERIFIED', 'REJECTED']), name='continuation_status_valid'),
            models.CheckConstraint(condition=~models.Q(original_order=models.F('continuation_order')), name='continuation_orders_distinct'),
        ]}),
        migrations.CreateModel(name='OrderContinuationReward', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('provider_business_cycle', models.PositiveIntegerField()), ('amount', models.PositiveIntegerField()),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('continuation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='rewards', to='orders.ordercontinuation')),
            ('provider', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='club_accounts.clubaccount')),
            ('transaction', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.transaction')),
        ], options={'constraints': [models.UniqueConstraint(fields=('continuation', 'provider'), name='continuation_reward_provider_unique')]}),
    ]
