from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('wallet', '0048_refunddepositrecoverypermit_zero_ordinary_addon_person_and_more')]

    operations = [
        migrations.CreateModel(
            name='RefundDepositCashRepayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.PositiveIntegerField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('deficit', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name='cash_repayments', to='wallet.refunddepositdeficit')),
                ('topup', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name='refund_gap_repayments', to='wallet.cashdeposittopup')),
            ],
            options={'constraints': [
                models.UniqueConstraint(fields=('topup', 'deficit'), name='refund_deposit_cash_once'),
                models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='refund_deposit_cash_positive'),
            ]},
        ),
        migrations.AddField(
            model_name='refunddepositrecoverypermit', name='cash_topup',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='closed_recovery_permits', to='wallet.cashdeposittopup'),
        ),
        migrations.AddConstraint(
            model_name='refunddepositrecoverypermit',
            constraint=models.CheckConstraint(condition=(models.Q(cash_topup__isnull=True) |
                models.Q(closed_at__isnull=False, close_reason='CASH_GAP_PAID', income_application__isnull=True,
                    zero_income_settlement__isnull=True, zero_negotiated_share__isnull=True,
                    zero_points_refund_person__isnull=True, zero_ordinary_addon_person__isnull=True)),
                name='refund_recovery_cash_valid'),
        ),
    ]
