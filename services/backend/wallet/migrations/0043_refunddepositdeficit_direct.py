from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('wallet', '0042_refunddepositrecoverypermit_zero_income_settlement')]

    operations = [
        migrations.AddField(
            model_name='refunddepositdeficit', name='source_kind',
            field=models.CharField(choices=[('PAIRED', '成对冲正'), ('DIRECT', '押金直接承担')], default='PAIRED', max_length=12),
        ),
        migrations.AddField(
            model_name='refunddepositdeficit', name='direct_key',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AlterField(
            model_name='refunddepositdeficit', name='source_provider_transaction',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                       related_name='+', to='wallet.transaction'),
        ),
        migrations.AddConstraint(
            model_name='refunddepositdeficit',
            constraint=models.CheckConstraint(condition=(models.Q(source_kind='PAIRED', source_provider_transaction__isnull=False, direct_key__isnull=True) |
                models.Q(source_kind='DIRECT', source_provider_transaction__isnull=True, direct_key__isnull=False)), name='refund_deposit_source_valid'),
        ),
    ]
