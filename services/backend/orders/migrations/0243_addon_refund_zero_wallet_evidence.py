from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('orders', '0242_points_transfer_zero_settlement_revision'),
                    ('wallet', '0054_transaction_deposit_cycle')]

    operations = [
        migrations.CreateModel(
            name='AddonRefundZeroWalletEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('business_cycle', models.PositiveIntegerField()),
                ('balance', models.PositiveIntegerField()),
                ('frozen_amount', models.PositiveIntegerField()),
                ('is_active', models.BooleanField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='club_accounts.clubaccount')),
                ('refund', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                    related_name='zero_wallet_evidence', to='orders.orderaddonrefund')),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='wallet.wallet')),
            ],
            options={'constraints': [models.CheckConstraint(condition=models.Q(balance=0, is_active=True),
                                                            name='addon_refund_wallet_zero_active')]},
        ),
    ]
