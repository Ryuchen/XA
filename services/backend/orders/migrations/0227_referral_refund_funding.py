from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0226_ordinary_transfer_followup_round'), ('users', '0049_webloginsession_wechat_unionid')]
    operations = [
        migrations.CreateModel(name='ReferralRefundFunding', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.PositiveIntegerField()), ('target_refund_amount', models.PositiveIntegerField()),
            ('request_key', models.CharField(max_length=160, unique=True)),
            ('allocated_by', models.CharField(max_length=64)), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='referral_refund_funding', to='orders.order')),
            ('receipt', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='referral_refund_allocations', to='users.passrefundrepaymentreceipt')),
            ('refund', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='referral_funding', to='orders.orderrefund')),
        ], options={'constraints': [models.CheckConstraint(condition=models.Q(amount__gt=0), name='referral_refund_funding_positive')]}),
        migrations.CreateModel(name='ReferralRefundFundingAction', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('request_key', models.CharField(max_length=160, unique=True)), ('actor', models.CharField(max_length=64)),
            ('result', models.JSONField()), ('created_at', models.DateTimeField(auto_now_add=True)),
            ('allocation', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='platform_action', to='orders.referralrefundfunding')),
        ]),
    ]
