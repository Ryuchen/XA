import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wallet', '0056_recharge_account_business_cycle')]

    operations = [
        migrations.CreateModel(
            name='WithdrawalRelease',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.PositiveIntegerField()),
                ('balance_before', models.IntegerField()), ('balance_after', models.IntegerField()),
                ('frozen_before', models.IntegerField()), ('frozen_after', models.IntegerField()),
                ('actor_kind', models.CharField(choices=[('OWNER', '本人'), ('STORE', '门店'), ('PLATFORM', '平台')], max_length=12)),
                ('platform_actor_id', models.UUIDField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor_account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                    related_name='+', to='club_accounts.clubaccount')),
                ('original_transaction', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                               related_name='+', to='wallet.transaction')),
                ('wallet', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='wallet.wallet')),
                ('withdrawal', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                    related_name='release', to='wallet.withdrawrequest')),
            ],
            options={'constraints': [
                models.CheckConstraint(condition=models.Q(amount__gt=0), name='withdraw_release_amount_positive'),
                models.CheckConstraint(condition=models.Q(balance_after=models.F('balance_before') + models.F('amount')),
                                       name='withdraw_release_balance_valid'),
                models.CheckConstraint(condition=models.Q(frozen_before=models.F('frozen_after') + models.F('amount')),
                                       name='withdraw_release_frozen_valid'),
                models.CheckConstraint(condition=(
                    models.Q(actor_kind__in=['OWNER', 'STORE'], actor_account__isnull=False, platform_actor_id__isnull=True)
                    | models.Q(actor_kind='PLATFORM', actor_account__isnull=True, platform_actor_id__isnull=False)),
                    name='withdraw_release_actor_valid'),
            ]},
        ),
    ]
