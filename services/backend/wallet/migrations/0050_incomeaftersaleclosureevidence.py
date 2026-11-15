from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('wallet', '0049_refunddepositcashrepayment'), ('orders', '0224_grab_ranking_snapshot')]

    operations = [migrations.AddField(model_name='incomehold', name='release_transaction',
        field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                  related_name='released_income_hold', to='wallet.transaction')),
                  migrations.AddField(model_name='incomehold', name='deferred_deposit_percent',
                                       field=models.PositiveSmallIntegerField(blank=True, null=True)),
                  migrations.AddField(model_name='incomehold', name='deferred_deposit_debit',
                      field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                          related_name='deferred_deposit_hold', to='wallet.transaction')),
                  migrations.AddField(model_name='incomehold', name='deferred_deposit_credit',
                      field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                          related_name='deferred_deposit_platform_hold', to='wallet.transaction')),
                  migrations.AddField(model_name='incomehold', name='initial_deposit_debit',
                      field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                          related_name='initial_deposit_hold', to='wallet.transaction')),
                  migrations.AddField(model_name='incomehold', name='initial_deposit_credit',
                      field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                          related_name='initial_deposit_platform_hold', to='wallet.transaction')),
                  migrations.CreateModel(
        name='IncomeAfterSaleClosureEvidence',
        fields=[('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('case_opened_at', models.DateTimeField(blank=True, null=True)), ('lower_at', models.DateTimeField()),
                ('upper_at', models.DateTimeField(blank=True, null=True)),
                ('barrier', models.BooleanField(default=False)),
                ('clock_evidence', models.JSONField(default=dict)),
                ('observed_cases', models.JSONField(blank=True, default=list)),
                ('case', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='income_closure_evidence', to='orders.aftersale')),
                ('hold', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                    related_name='priority_opening_evidence', to='wallet.incomehold'))],
        options={'constraints': [models.UniqueConstraint(fields=('case', 'case_opened_at'),
                                                           name='income_case_episode_once'),
            models.CheckConstraint(condition=(models.Q(case__isnull=False, case_opened_at__isnull=False, hold__isnull=True, barrier=False)
                | models.Q(case__isnull=True, case_opened_at__isnull=True, hold__isnull=False, barrier=False)
                | models.Q(case__isnull=True, case_opened_at__isnull=True, hold__isnull=True, barrier=True)),
                name='income_priority_event_kind')]})]
