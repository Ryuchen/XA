from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('wallet', '0064_settlementclockevidence'),
    ]

    operations = [
        migrations.AlterField(
            model_name='settlementclockevidence',
            name='order',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='+', to='orders.order'),
        ),
        migrations.AlterField(
            model_name='settlementclockevidence',
            name='subject_type',
            field=models.CharField(choices=[('SUBMISSION', '报单版本'), ('MANIFEST', '结算清单'),
                ('PERSON', '逐人结算'), ('ADDON', '加购逐段结算'), ('HOLD', '冻结收益')], max_length=16),
        ),
        migrations.AlterField(
            model_name='settlementclockevidence',
            name='operation',
            field=models.CharField(choices=[('SUBMIT', '报单提交'), ('REVIEW', '报单审核'),
                ('PLAN', '结算计划'), ('PERSON', '逐人结算'), ('ADDON', '加购逐段结算'),
                ('FINALIZE', '整单结算'), ('FREEZE', '收益冻结'), ('RELEASE', '收益解冻')], max_length=16),
        ),
        migrations.AddField(
            model_name='settlementclockevidence',
            name='deadline_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name='settlementclockevidence',
            constraint=models.CheckConstraint(
                condition=models.Q(subject_type='HOLD') | models.Q(order__isnull=False),
                name='settlement_clock_order_scope'),
        ),
        migrations.AddConstraint(
            model_name='settlementclockevidence',
            constraint=models.CheckConstraint(
                condition=models.Q(subject_type='HOLD', deadline_at__isnull=False)
                | models.Q(deadline_at__isnull=True),
                name='settlement_clock_deadline_scope'),
        ),
    ]
